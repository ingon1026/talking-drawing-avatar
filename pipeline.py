"""JoyVASA 인프로세스 추론 래퍼 + 깜빡임·표정 스케줄 주입.

모델은 첫 generate() 호출 시 1회 로드되어 GPU에 상주한다 (재로드 없음).
렌더의 warping+spade 는 FasterLivePortrait TRT 엔진이 있으면 그쪽으로 넘긴다 (trt_warp.py).
깜빡임은 JoyVASA 렌더 루프의 eye retargeting 분기에 프레임별 target
eyes-open ratio 스케줄을 주입해 구현 (JoyVASA/src/live_portrait_wmg_pipeline.py 참조).
"""
import inspect
import subprocess
import sys
from pathlib import Path

import trt_warp

ROOT = Path(__file__).parent
JOYVASA = ROOT / "JoyVASA"
sys.path.insert(0, str(JOYVASA))

FPS = 25  # JoyVASA inference_config output_fps 기본값

# 감정 → LivePortrait 표정 벡터(exp 21×3) 델타.
#
# 인덱스 의미가 업스트림에 문서화돼 있지 않아 tools/exp_index_probe.py 로 실측했다.
# 두 번 크게 틀렸으니 고칠 때 같은 함정을 조심할 것:
#   1) JoyVASA 모션 생성은 디퓨전이라 시드를 안 박으면 널 차분만으로 입 대역이 321px
#      변한다 — 신호(600 안팎)와 구분이 안 된다. probe 가 torch.manual_seed 를 박는 이유.
#   2) 대역별 변화량이 큰 인덱스가 그 부위인 게 아니다. 3·4·11·15 는 얼굴 전체를
#      상하로 옮겨서 눈썹 대역이 커 보인다. 국소도로 거르고, 반드시 렌더를 눈으로 볼 것.
#
# 눈썹은 2항으로 움직인다(단일 인덱스가 아니다). 실사 이미지(joyvasa_005)로 검증:
#   올림 = [1,1] +k / [2,1] -k     내림 = 그 반대
# 예전에 [2,1] 양수를 "올림"으로 적었는데 반대였다 — 앞머리가 눈썹을 가리는 그림으로
# 판정해서 얼굴 전체 이동을 눈썹으로 오인했다.
BROW_UP = ((1, 1, +1.0), (2, 1, -1.0))   # 계수는 아래 스케일로 곱한다
BROW_SCALE = 0.035                        # ±0.03 이 화면에서 뚜렷하고 과하지 않은 크기

# 미소는 8항 조합(상류 LivePortrait update_delta_new_smile 을 우리 경로로 재현·실측).
# 립싱크가 6·12·14·17·19·20 을 덮어쓰지만 우리 주입은 그 *뒤*라 가산으로 합성된다 —
# 그래서 14·17·20 을 써도 된다. s=+0.5 가 안전 상한: +1.0 이면 닫힌 비제마에 치아가 샌다.
SMILE = ((20, 1, -0.01), (14, 1, -0.02), (17, 1, 0.0065), (17, 2, 0.003),
         (13, 1, -0.00275), (16, 1, -0.00275), (3, 1, -0.0035), (7, 1, -0.0035))
SMILE_MAX = 0.5

# 감정별 (눈썹 올림 정도, 미소 정도). 부호·크기는 avatar_core.js EMOTIONS 와 맞춘다.
# 음수 미소는 "오므림(뾰로통)"이지 찡그림이 아니고 입 벌림과 싸운다 — 찡그림은
# 눈썹 내림으로만 표현한다(실측: -0.5 면 열린 비제마가 닫힌 입술로 뭉개진다).
EMOTION_FACE = {
    "neutral":  (0.0,  0.0),
    "joy":      (0.0,  1.0),    # EMOTIONS.joy 는 brow 채널이 없고 mouthsmile 0.55
    "sad":      (0.7,  0.0),    # browinnerup 0.7
    "angry":   (-0.85, 0.0),    # browdown 0.85
    "surprise": (0.75, 0.0),    # browouterup 0.75
    "fear":     (0.85, 0.0),    # browinnerup 0.85
    "shy":      (0.0,  0.4),    # mouthsmile 0.3 — 옅은 미소
}


def emotion_exp_delta(emo: str | None, intensity: float = 1.0):
    """감정 라벨 → (21,3) 표정 델타. 없거나 중립이면 None(주입 안 함)."""
    brow, smile = EMOTION_FACE.get(emo or "neutral", (0.0, 0.0))
    k = max(0.0, min(1.0, intensity))
    brow, smile = brow * k, smile * k * SMILE_MAX
    if abs(brow) < 1e-3 and abs(smile) < 1e-3:
        return None
    import numpy as np
    d = np.zeros((21, 3), dtype="float32")
    for i, ax, c in BROW_UP:
        d[i, ax] += brow * BROW_SCALE * c
    for i, ax, c in SMILE:
        d[i, ax] += smile * c
    return d


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


class _StreamEncoder:
    """렌더된 프레임을 나오는 즉시 ffmpeg 에 흘려보낸다.

    상류는 프레임을 전부 메모리에 모았다가 끝나고 인코딩(0.21s) + 오디오 먹싱(0.21s) 한다.
    그 0.42s 도 아깝지만 진짜 문제는 **다 만들 때까지 재생을 시작조차 못 한다**는 것이다.
    프래그먼트 mp4(`frag_keyframe+empty_moov`)로 쓰면 moov 가 맨 앞에 있어 파일이 자라는
    중에도 브라우저가 재생할 수 있다 — 3.3초 대기가 1초대 체감으로 줄어든다.

    프레임 크기는 첫 프레임을 봐야 알아서 ffmpeg 을 그때 띄운다.
    """

    def __init__(self, out_mp4: Path, wav: Path, fps: int = FPS):
        self.out, self.wav, self.fps, self.p = Path(out_mp4), Path(wav), fps, None

    def __call__(self, frame):
        """frame: HxWx3 uint8 (RGB)"""
        if self.p is None:
            h, w = frame.shape[:2]
            self.p = subprocess.Popen([
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                "-r", str(self.fps), "-i", "-",
                "-i", str(self.wav),
                # zerolatency = b프레임·lookahead 없음, g=25 = 1초마다 키프레임.
                # 둘 다 첫 조각이 빨리 떨어지게 하는 설정이다.
                "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
                "-crf", "18", "-g", str(self.fps), "-pix_fmt", "yuv420p", "-c:a", "aac",
                # -shortest 는 쓰면 안 된다: 오디오가 프레임보다 먼저 끝나면 ffmpeg 이
                # 그 자리에서 종료해 남은 프레임 write 가 Broken pipe 로 죽는다(실측).
                # 상류 add_audio_to_video 도 안 쓴다 — 길이는 max(영상, 오디오).
                "-movflags", "frag_keyframe+empty_moov+default_base_moof",
                "-frag_duration", "200000", "-flush_packets", "1",
                str(self.out)], stdin=subprocess.PIPE)
        self.p.stdin.write(frame.tobytes())

    def abort(self):
        """예외 경로 — ffmpeg 를 죽이고 반쯤 쓰인 파일을 지운다.

        close() 를 대신 부르면 안 된다: 그쪽은 정상 종료를 기다렸다가 remux 까지 하는데,
        렌더가 중간에 죽었으면 남은 건 잘린 영상이라 저장 버튼에 걸릴 값어치가 없다.
        """
        if self.p is None:
            return
        self.p.kill()
        self.p.stdin.close()
        self.p.wait()
        self.out.unlink(missing_ok=True)

    def close(self):
        if self.p is None:
            raise RuntimeError("렌더된 프레임이 하나도 없습니다 (execute 가 parse_output 을 안 불렀다)")
        self.p.stdin.close()
        if self.p.wait() != 0:
            raise RuntimeError("ffmpeg 인코딩 실패")
        # 조각 포맷은 재생 시작은 빠르지만 플레이어에 따라 길이를 못 읽거나 아예 못 연다.
        # 저장 버튼이 주는 파일은 어디서나 열려야 하므로 재인코딩 없이 다시 담는다(~50ms).
        # 이미 스트림을 읽고 있는 쪽은 rename 뒤에도 옛 inode 를 계속 읽으므로 안전하다.
        tmp = self.out.with_suffix(".fix.mp4")
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(self.out),
                            "-c", "copy", "-movflags", "+faststart", str(tmp)])
        if r.returncode == 0 and tmp.exists():
            tmp.replace(self.out)
        else:
            tmp.unlink(missing_ok=True)


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
        # TRT 엔진이 있으면 컴파일 대상(warping/spade)이 통째로 대체되므로 자동으로 꺼진다.
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
        # generate() 가 P.images2video 를 무력화해 일괄 인코딩을 끈다. 상류가 이 호출을
        # 없애거나 이름을 바꾸면 몽키패치가 조용히 헛돌고 — 증상은 "왜 느리지" + output/ 에
        # 임시 mp4 누적뿐이다. (모듈 전역이 아니라 `video.images2video` 같은 속성 접근으로
        # 바뀌는 경우까지는 이 문자열 검사로 못 잡는다.)
        if "images2video" not in _src:
            raise RuntimeError(
                "상류 execute 에 images2video 호출이 없습니다 — 스트리밍 인코더의 "
                "일괄 인코딩 무력화(P.images2video 몽키패치)가 헛돕니다. pipeline.py 의 "
                "generate() 를 상류 변경에 맞춰 고치세요.")

        # torch.compile 이 손대는 건 warping_module·spade_generator 둘뿐이라
        # (live_portrait_wmg_wrapper.py:74-75) TRT 로 갈아끼우면 컴파일할 게 없다.
        use_trt = trt_warp.available()
        base = ArgumentConfig(animation_mode=self.animation_mode,
                              flag_do_crop=self.do_crop, cfg_scale=self.cfg_scale,
                              flag_use_half_precision=self.half,
                              flag_do_torch_compile=self.compile and not use_trt)
        self._pipe = LivePortraitPipeline(
            inference_cfg=_partial_fields(InferenceConfig, base.__dict__),
            crop_cfg=_partial_fields(CropConfig, base.__dict__))
        if use_trt:
            import torch
            lw = self._pipe.live_portrait_wrapper
            lw.warp_decode = trt_warp.TrtWarpDecode()
            lw.warping_module = lw.spade_generator = None   # TRT 와 중복 상주 = VRAM 낭비
            torch.cuda.empty_cache()
            print(f"[pipeline] warping+spade → TRT ({trt_warp.ENGINE.name})")
        else:
            # 조용히 느려지지 않게 남긴다 — 엔진 경로가 어긋나면 증상이 "왜 느리지" 뿐이다.
            print(f"[pipeline] TRT 엔진 없음 → torch 경로 (1.4배 느림): {trt_warp.ENGINE}")
        self._ArgumentConfig = ArgumentConfig
        return self._pipe

    def generate(self, wav: Path, out_mp4: Path,
                 blink_interval: float = 4.0, blink_strength: float = 1.0,
                 exp_delta=None,
                 image: Path = None, do_crop: bool = None) -> Path:
        """wav + 그림 → 립싱크 mp4 (blink_interval초마다 강제 깜빡임, 0 = 주입 없음).

        image: **필수.** 예전엔 없으면 리포 최상위를 알파벳 순으로 훑어 첫 이미지를 썼는데,
            아무도 고르지 않은 파일이 얼굴이 됐다 — 실제로 테스트 픽스처 test_1.png(돼지
            낙서)가 잡혀서 "/" 로 만든 영상이 전부 돼지였다. 얼굴은 호출측이 정한다.
        do_crop: 실사 얼굴이면 True(InsightFace 크롭). 미지정 시 인스턴스 기본값.
        """
        img = Path(image) if image else None
        if img is None or not img.exists():
            raise RuntimeError(f"애니메이션할 그림/사진이 지정되지 않았습니다: {image!r}")

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

        # 프레임을 나오는 대로 인코딩한다 — 상류의 끝단 일괄 인코딩은 무력화한다.
        # parse_output 이 렌더 루프에서 프레임당 정확히 한 번, 순서대로 불리는 유일한 지점이라
        # JoyVASA 패치를 늘리지 않고 여기에 붙는다.
        inf = pipe.live_portrait_wrapper.inference_cfg
        if inf.flag_pasteback and inf.flag_do_crop and inf.flag_stitching:
            raise RuntimeError("pasteback 경로는 parse_output 뒤에 프레임을 또 합성한다 — "
                               "스트리밍 인코더가 합성 전 프레임을 내보내므로 지원하지 않는다.")
        import torch   # generate() 스코프에는 없다 — _load() 의 것은 그 함수 지역이다
        import src.live_portrait_wmg_pipeline as P
        lw = pipe.live_portrait_wrapper
        enc, orig = _StreamEncoder(out_mp4, wav), lw.parse_output
        keep = (P.images2video, P.add_audio_to_video)

        def parse_and_stream(out):
            # 상류 parse_output 을 부르지 않고 같은 일을 GPU 에서 한다. 상류는 fp32 CHW 3MB 를
            # 통째로 내린 뒤 numpy 로 후처리하는데, np.transpose 가 만든 비연속 뷰가 clip·astype
            # (둘 다 order='K')을 지나 그대로 남아 인코더의 tobytes() 가 786KB 를 스트라이드로
            # 긁어모은다. 실측 3.75ms/frame → 0.22ms/frame (118프레임 기준 417ms 절감).
            #
            # in-place(mul_·clamp_) 금지: TrtWarpDecode 의 out 은 매 프레임 재사용하는
            # 사전할당 버퍼라 직접 고치면 다음 프레임이 깨진다. torch 폴백 경로는 매번 새
            # 텐서를 주므로 그쪽으로 테스트하면 이 함정이 안 잡힌다.
            #
            # 상류와 바이트 동일함을 확인했다(범위 밖·경계값 포함). 상류는 clip(0,1) 후 ×255,
            # 여기는 ×255 후 clamp(0,255) — 순서가 달라도 결과가 같다.
            f = (out.mul(255).clamp(0, 255).to(torch.uint8)
                    .permute(0, 2, 3, 1).contiguous().cpu().numpy())   # 1xHxWx3 uint8
            enc(f[0])
            return f

        lw.parse_output = parse_and_stream
        P.images2video = P.add_audio_to_video = lambda *a, **k: None
        try:
            pipe.execute(args)      # 반환 경로는 우리가 안 쓴다 — 파일은 enc 가 이미 썼다
        except BaseException:
            # close() 는 정상 종료 대기 + remux 라 여기서 부르면 안 된다. 안 부르고 넘기면
            # ffmpeg 자식이 stdin 열린 채 남아 실패한 잡마다 쌓인다(상시 서비스라 누적된다).
            enc.abort()
            raise
        finally:
            lw.parse_output = orig
            P.images2video, P.add_audio_to_video = keep
        enc.close()
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
