"""JoyVASA 인프로세스 추론 래퍼 + 깜빡임·표정 스케줄 주입.

모델은 첫 generate() 호출 시 1회 로드되어 GPU에 상주한다 (재로드 없음).
깜빡임은 JoyVASA 렌더 루프의 eye retargeting 분기에 프레임별 target
eyes-open ratio 스케줄을 주입해 구현 (JoyVASA/src/live_portrait_wmg_pipeline.py 참조).
"""
import inspect
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
JOYVASA = ROOT / "JoyVASA"
sys.path.insert(0, str(JOYVASA))

IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
FPS = 25  # JoyVASA inference_config output_fps 기본값

# 감정 → LivePortrait 표정 벡터(exp 21×3) 델타.
# 인덱스 의미가 문서화돼 있지 않아 tools/exp_index_probe.py 로 실측했다:
#   2 y축 = 눈썹 (양수 올림, 국소도 0.60 — 얼굴도 약간 따라 움직인다)
#   6·12·14·19·20 = 입술 (소스에 명시, 립싱크가 이미 쓰므로 건드리지 않는다)
#   3·4·11·15 = 얼굴 전체 상하 이동 — 눈썹처럼 보이지만 전역이라 쓰면 안 된다
# ±0.08 은 과하고 ±0.04 가 화면에서 읽히는 크기다.
BROW_IDX, BROW_AXIS = 2, 1
# 값은 avatar_core.js EMOTIONS 의 brow 채널 최대치 × BROW_SCALE 로 유도한다 — 손으로 적으면
# 두 경로의 감정 간 상대 세기가 어긋난다(실시간은 fear 0.85 > sad 0.7 인데 영상은 같았다).
BROW_SCALE = 0.053         # ±0.045 가 화면에서 읽히는 상한이 되도록 맞춘 배율
_BROW_UP = {"sad": 0.7, "surprise": 0.75, "fear": 0.85, "joy": 0.0, "shy": 0.0, "neutral": 0.0}
_BROW_DOWN = {"angry": 0.85}
EMOTION_BROW = {k: round(v * BROW_SCALE, 4) for k, v in _BROW_UP.items()}
EMOTION_BROW.update({k: -round(v * BROW_SCALE, 4) for k, v in _BROW_DOWN.items()})
# joy·shy 는 EMOTIONS 에 brow 채널이 아예 없다 — 그 감정의 신호는 입꼬리·볼에 있고
# 영상 경로는 아직 그 축을 못 쓴다. 억지로 눈썹을 올리면 실시간과 다른 얼굴이 된다.


def emotion_exp_delta(emo: str | None, intensity: float = 1.0):
    """감정 라벨 → (21,3) 표정 델타. 없거나 중립이면 None(주입 안 함)."""
    v = EMOTION_BROW.get(emo or "neutral", 0.0) * max(0.0, min(1.0, intensity))
    if abs(v) < 1e-4:
        return None
    import numpy as np
    d = np.zeros((21, 3), dtype="float32")
    d[BROW_IDX, BROW_AXIS] = v
    return d


SAMPLE_FALLBACK = JOYVASA / "assets" / "examples" / "imgs" / "joyvasa_003.png"


def find_avatar_image() -> Path | None:
    """face 폴더 최상위의 그림 파일 우선, 없으면 JoyVASA 샘플로 폴백."""
    for p in sorted(ROOT.iterdir()):
        if p.suffix.lower() in IMG_EXTS:
            return p
    return SAMPLE_FALLBACK if SAMPLE_FALLBACK.exists() else None


def wav_duration(wav: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(wav)],
        capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def make_blink_schedule(n_frames: int, interval_s: float, strength: float):
    """프레임별 target eyes-open ratio 리스트. None = 해당 프레임 주입 없음(모델 원본 유지)."""
    if interval_s <= 0 or strength <= 0:
        return None
    # ponytail: 절대 ratio 상수(열림≈0.3 가정) 사용. 그림별로 어색하면 source 랜드마크 기반 보정으로 교체
    closed = 0.3 * (1.0 - strength)
    edge = min(closed + 0.12, 0.3)
    env = (edge, closed, closed, edge)  # 4프레임 = 160ms @25fps
    sched = [None] * n_frames
    step = max(int(interval_s * FPS), len(env) + 2)
    for start in range(step, n_frames - len(env), step):
        for off, v in enumerate(env):
            sched[start + off] = v
    return sched


def _partial_fields(cls, kwargs):
    return cls(**{k: v for k, v in kwargs.items() if hasattr(cls, k)})


class AvatarPipeline:
    def __init__(self):
        self.animation_mode = "human"  # 손그림 검출 실패 시 "animal" 폴백
        self.do_crop = False  # 그림에서 얼굴이 중앙 정렬 안 돼 있으면 True로
        self.cfg_scale = 2.0
        # fp16: 렌더 루프가 전체 시간의 대부분이라 체감이 크다. RTX 4070 Ti(Ada) 실측 —
        # 웜 15.8s → 11.1s(1.42배), 화질 육안 동일, 상류 주석이 경고한 검은 박스 없음.
        # 구형 GPU에서 깨지면 False 로.
        self.half = True
        # torch.compile: 프로세스당 첫 생성에 컴파일 ~28~52s(인덱터 디스크 캐시로 재기동 시 단축),
        # 이후 8.7s 오디오 기준 8.9s = 거의 실시간(1.0x). 오디오 길이가 달라져도 재컴파일 없음
        # (2.2s→2.6s, 12.5s→12.4s 실측). 화질 차이는 디퓨전 자체 변동(같은 설정 재실행 30.9~42.1dB)
        # 범위 안. triton 2.2.0 필요 — 없으면 ImportError 나므로 False 로.
        self.compile = True
        self._pipe = None
        self._ArgumentConfig = None

    def _load(self):
        from src.config.argument_config import ArgumentConfig
        from src.config.inference_config import InferenceConfig
        from src.config.crop_config import CropConfig
        from src.live_portrait_wmg_pipeline import LivePortraitPipeline

        # 패치 미적용을 여기서 잡는다. ArgumentConfig 는 frozen 이 아니라 없는 속성을
        # 대입해도 예외가 안 나고, 렌더 분기가 항등식이라 립싱크는 그대로 나온다 —
        # 표정과 깜빡임만 조용히 죽고 영상은 정상으로 보인다. 그 상태로 인덱스를 측정하면
        # "어떤 인덱스도 효과 없음" 이라는 거짓 결론까지 나온다.
        _src = inspect.getsource(LivePortraitPipeline.execute)
        _missing = [m for m in ("exp_delta_schedule", "eye_ratio_schedule") if m not in _src]
        if _missing:
            raise RuntimeError(
                f"JoyVASA 패치가 적용돼 있지 않습니다 (누락: {', '.join(_missing)}). "
                "patches/README.md 참조 — cd JoyVASA && git apply ../patches/joyvasa_inject.patch")

        base = ArgumentConfig(animation_mode=self.animation_mode,
                              flag_do_crop=self.do_crop, cfg_scale=self.cfg_scale,
                              flag_use_half_precision=self.half,
                              flag_do_torch_compile=self.compile)
        self._pipe = LivePortraitPipeline(
            inference_cfg=_partial_fields(InferenceConfig, base.__dict__),
            crop_cfg=_partial_fields(CropConfig, base.__dict__))
        self._ArgumentConfig = ArgumentConfig
        return self._pipe

    def generate(self, wav: Path, out_mp4: Path,
                 blink_interval: float = 4.0, blink_strength: float = 1.0,
                 exp_delta=None,
                 image: Path = None, do_crop: bool = None) -> Path:
        """wav + 그림 → 립싱크 mp4 (blink_interval초마다 강제 깜빡임, 0 = 주입 없음).

        image: 지정하면 그 사진으로 애니메이션(실사 업로드용), 없으면 폴더 기본 이미지.
        do_crop: 실사 얼굴이면 True(InsightFace 크롭). 미지정 시 인스턴스 기본값.
        """
        img = Path(image) if image else find_avatar_image()
        if img is None or not Path(img).exists():
            raise RuntimeError("그림/사진 파일이 없습니다.")

        pipe = self._pipe or self._load()

        n_frames = int(wav_duration(wav) * FPS) + FPS  # 여유 1초
        sched = make_blink_schedule(n_frames, blink_interval, blink_strength)
        pipe.live_portrait_wrapper.inference_cfg.flag_eye_retargeting = sched is not None

        args = self._ArgumentConfig(
            animation_mode=self.animation_mode, reference=str(img), audio=str(wav),
            output_dir=str(out_mp4.parent), cfg_scale=self.cfg_scale,
            flag_do_crop=self.do_crop if do_crop is None else do_crop)
        args.eye_ratio_schedule = sched
        # 표정 델타 — 발화 내내 같은 값(감정은 문장 단위라 프레임마다 바뀌지 않는다).
        args.exp_delta_schedule = [exp_delta] * n_frames if exp_delta is not None else None

        final = Path(pipe.execute(args))
        temp = final.with_name(final.stem + "_temp.mp4")
        if temp.exists():
            temp.unlink()
        final.rename(out_mp4)
        return out_mp4


if __name__ == "__main__":
    # 스케줄 로직 자체 검증 (GPU 불필요): python pipeline.py
    s = make_blink_schedule(100, 2.0, 1.0)  # step=50, env 4프레임
    assert s[49] is None and s[50] == 0.12 and s[51] == 0.0 and s[54] is None
    assert make_blink_schedule(100, 0, 1.0) is None      # 간격 0 = 주입 없음
    assert make_blink_schedule(100, 2.0, 0) is None      # 강도 0 = 주입 없음
    half = make_blink_schedule(100, 2.0, 0.5)
    assert abs(half[51] - 0.15) < 1e-9                   # 강도 0.5 = 반감김
    print("blink schedule self-check OK")
