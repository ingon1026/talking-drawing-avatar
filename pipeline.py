"""JoyVASA 인프로세스 추론 래퍼 + 깜빡임·표정 스케줄 주입.

모델은 첫 generate() 호출 시 1회 로드되어 GPU에 상주한다 (재로드 없음).
렌더의 warping+spade 는 FasterLivePortrait TRT 엔진이 있으면 그쪽으로 넘긴다 (trt_warp.py).
깜빡임은 JoyVASA 렌더 루프의 eye retargeting 분기에 프레임별 target
eyes-open ratio 스케줄을 주입해 구현 (JoyVASA/src/live_portrait_wmg_pipeline.py 참조).
"""
import inspect
import random
import subprocess
import sys
import threading
from pathlib import Path

import trt_warp

ROOT = Path(__file__).parent
JOYVASA = ROOT / "JoyVASA"
sys.path.insert(0, str(JOYVASA))

FPS = 25  # JoyVASA inference_config output_fps 기본값

# 상류 execute 에 우리 패치·몽키패치가 걸려 있는지 판별하는 문자열.
# **두 곳이 쓴다** — _load() 가 렌더 직전에(정확), app._joyvasa_ready() 가 /api/health 에서
# (모델 로드 없이). 예전엔 app 이 목록을 손으로 베껴 들고 있었고 검사 범위도 달랐다
# (파일 전체 vs execute 메서드). 갈라지면 증상은 "라디오는 열렸는데 누르면 500" 이다.
PATCH_MARKERS = ("exp_delta_schedule", "eye_ratio_schedule")   # 표정·깜빡임 주입 지점
STREAM_MARKER = "images2video"    # 스트리밍 인코더가 무력화할 일괄 인코딩 호출


def execute_source() -> str:
    """상류 LivePortraitPipeline.execute 의 소스 텍스트. 빈 문자열이면 못 찾은 것.

    _load() 는 inspect.getsource 를 쓰지만 그건 JoyVASA 모듈이 import 된 뒤에만 된다.
    /api/health 는 페이지 로드마다 불려서 그 비용을 낼 수 없어 파일에서 직접 뜬다.
    **범위를 execute 로 좁히는 게 핵심이다** — 파일 전체를 보면 상단 import 줄의
    images2video 가 항상 걸려서, 몽키패치 대상이 사라져도 통과한다.

    끝 경계에 `\\n\\S`(들여쓰기 없는 줄)가 필요하다. execute 는 현재 클래스의 **마지막
    메서드**라 `\\n    def ` 로만 끊으면 파일 끝까지 삼킨다 — 상류가 뒤에 모듈 레벨 코드나
    주석을 붙이면 거기 있는 마커가 execute 안에 있는 것처럼 읽힌다(실측으로 걸렸다).
    """
    import re
    try:
        src = (JOYVASA / "src" / "live_portrait_wmg_pipeline.py").read_text()
    except OSError:
        return ""
    m = re.search(r"\n    def execute\(.*?(?=\n    def |\n\S|\Z)", src, re.S)
    return m.group(0) if m else ""

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
SMILE_MAX = 0.5   # 아래 표에 **이미 접어 넣었다** — 곱셈 경로가 아니라 표의 상한이다

# 눈 크게 뜨기는 4항 조합이다. 단일 인덱스로는 안 나온다 — 11 은 혼자 주면 얼굴을 통째로
# 끌고 가고 13/16 만으로는 약하다. 좌우가 각각 두 인덱스로 움직이고 **둘의 부호가 서로
# 반대다**(화면 왼눈 = 11 음수 / 13 양수, 오른눈 = 15 음수 / 16 양수). 부호를 통째로
# 뒤집으면 실눈이 된다 — 그게 부호 검증에 쓴 방법이다.
#
# 실측은 사진이 아니라 **대상 손그림**(assets_characters/u_524599, 대형 만화 눈)에서 했다.
# 512 프레임의 눈 박스에서 홍채 면적과 드러난 흰자 픽셀을 세고, 얼굴 전역 이동을 먼저
# 추정해 빼고 비교했다(11·15 가 얼굴을 옮기는 함정 때문에 — 실측 결과 이 조합은 전역
# 이동 0):
#   노이즈 바닥(시드 고정, 델타 없이 재렌더) 프레임 전체 4~9px, 흰자 지표로는 ±4px
#   계수 0.020 : 흰자 +63 / +105px, 홍채 +105 / +164px — 뚜렷하게 커진다
#   계수 -0.020: 흰자 122→22 / 280→85px — 실눈. 부호가 맞다
#   계수 0.030 : 여기까지 깨끗하다 — **눈 축 단독 기준이다**
#   계수 0.040 : 홍채 텍스처와 캐치라이트가 뭉개지기 시작
#   계수 0.060 : 눈이 부풀고 색수차가 생긴다 — 붕괴
# 눈썹이 같이 걸리면 상한이 더 내려온다: surprise(눈썹 0.75 + 눈 0.028)는 홍채가 이미
# 뭉개져 보였고 그래서 SCALE 을 0.035 가 아니라 0.025 로 잡았다. 아래 self-check 는 곱만
# 보므로 SCALE 을 0.035 로 올려도 통과한다 — 통과한다고 괜찮은 게 아니다, 렌더를 볼 것.
EYE_WIDE = ((11, 1, -1.0), (13, 1, +0.3), (15, 1, -1.0), (16, 1, +0.3))
EYE_WIDE_SCALE = 0.025    # 표의 최대치 surprise 0.8 → 최종 0.020. 상한 0.030 에 여유를 둔다
EYE_WIDE_SAFE = 0.030     # 실측 안전 상한(최종 계수 절대값). 표가 이걸 넘으면 self-check 가 잡는다

# **깜빡임과 부분 상쇄된다.** eye_ratio_schedule 의 eyes_delta 는 x_s 에서 따로 계산돼
# 가산되므로(patches/joyvasa_inject.patch) 눈 축이 눈꺼풀을 반대로 밀어 깜빡임이 얕아진다.
# 실측(홍채 면적 감소율, 발화 + blink_interval=4.0): 중립 54% → surprise 37%.
# 눈은 여전히 감기지만 완전히 닫히지는 않는다. 두 스케줄을 서로 알게 만들면 없앨 수 있는데
# 새 결합이라 여기선 안 했다 — 고칠 거면 blink 프레임에서 눈 축만 0 으로 접는 경로가 된다.

# 감정별 (눈썹 올림 정도, 미소 정도, 눈 크게 뜨기) — 셋 다 그 축의 **최종 크기**다
# (intensity 만 더 곱한다).
# 예전엔 미소만 여기서 한 번 더 SMILE_MAX 를 곱했다. 두 축의 단위가 달라져서(눈썹은
# 최종값, 미소는 "상한의 몇 배") 표를 읽고 크기를 가늠할 수가 없었고, 실시간 경로의
# emotion[k] = base[k] * intensity (avatar_core.js) 와도 식이 어긋났다. 상한은 표에
# 접어 넣어 지키고(joy 0.5 = 예전 1.0×SMILE_MAX), 곱셈은 intensity 하나로 남긴다.
#
# 음수 미소는 "오므림(뾰로통)"이지 찡그림이 아니고 입 벌림과 싸운다 — 찡그림은
# 눈썹 내림으로만 표현한다(실측: -0.5 면 열린 비제마가 닫힌 입술로 뭉개진다).
#
# **avatar_core.js EMOTIONS 의 사본이 아니다.** 옆의 채널 이름은 출처 표시일 뿐,
# 자동으로 따라오지 않는다. 눈썹은 값이 마침 1:1 이지만(내림인 angry 만 부호를 뒤집는다)
# 어느 채널을 읽을지는 판단이 들어간다 — surprise 는 EMOTIONS 에 browinnerup 0.6 과 browouterup
# 0.75 가 같이 있는데 여기선 outer 를 골랐다. 미소는 비례하지도 않는다(0.55→0.5,
# 0.3→0.2). 단위도 ARKit 채널이 아니라 LivePortrait exp 축 계수라 렌더를 봐야 검증된다.
# EMOTIONS 를 고쳤으면 여기도 손으로 고치고 영상을 눈으로 볼 것.
#
# 눈 축도 마찬가지다. eyewide 는 surprise 0.8 / fear 0.7 을 그대로 가져왔지만 **비율만**
# 참고한 것이고 절대 크기는 EYE_WIDE_SCALE 로 따로 잡았다.
#
# joy 는 EMOTIONS 에 eyesquint 0.5(눈웃음)가 있는데 여기선 0 이다. 근거는 코드 읽기다 —
# SMILE 이 이미 13,1 / 16,1 에 음수를 넣고 있어 눈 축의 음수와 같은 자리를 겹쳐 쓴다.
# **실측으로는 확인하지 못했다**: joy 렌더의 흰자 변화가 -5 / -21px 였는데 프로세스를
# 새로 띄워 같은 중립을 두 번 렌더한 차이가 -4 / -17px 이라 구분이 안 된다(같은 프로세스
# 안에서는 ±4px 인데 프로세스 간에는 이만큼 벌어진다 — 노이즈 바닥을 인용할 때 주의).
# joy 에 눈웃음을 넣을지는 열려 있고, 넣는다면 11·15 항이 새로 붙으므로 렌더로 판정할 것.
EMOTION_FACE = {
    "neutral":  (0.0,  0.0, 0.0),
    "joy":      (0.0,  0.5, 0.0),    # EMOTIONS.joy 는 brow 채널이 없고 mouthsmile 0.55
    "sad":      (0.7,  0.0, 0.0),    # browinnerup 0.7
    "angry":   (-0.85, 0.0, 0.0),    # browdown 0.85
    "surprise": (0.75, 0.0, 0.8),    # browouterup 0.75, eyewide 0.8
    "fear":     (0.85, 0.0, 0.7),    # browinnerup 0.85, eyewide 0.7
    "shy":      (0.0,  0.2, 0.0),    # mouthsmile 0.3 — 옅은 미소
}


def emotion_exp_delta(emo: str | None, intensity: float = 1.0):
    """감정 라벨 → (21,3) 표정 델타. 없거나 중립이면 None(주입 안 함)."""
    if emo and emo not in EMOTION_FACE:
        # 조용히 중립이 되면 증상이 "그 감정만 표정이 안 나옴" 뿐이라 아무도 못 찾는다.
        # EMOTIONS 에 감정을 하나 더한 뒤 여기 안 더하면 바로 이 상태가 된다.
        print(f"[pipeline] EMOTION_FACE 에 없는 감정 '{emo}' — 표정 없이(중립) 진행")
    brow, smile, eye = EMOTION_FACE.get(emo or "neutral", (0.0, 0.0, 0.0))
    k = max(0.0, min(1.0, intensity))
    brow, smile, eye = brow * k, smile * k, eye * k
    # 축을 하나 더할 때 여기 빼먹으면 그 축만 있는 감정이 조용히 중립이 된다.
    if abs(brow) < 1e-3 and abs(smile) < 1e-3 and abs(eye) < 1e-3:
        return None
    import numpy as np
    d = np.zeros((21, 3), dtype="float32")
    for i, ax, c in BROW_UP:
        d[i, ax] += brow * BROW_SCALE * c
    for i, ax, c in SMILE:
        d[i, ax] += smile * c
    for i, ax, c in EYE_WIDE:
        d[i, ax] += eye * EYE_WIDE_SCALE * c
    return d


CROSSFADE_S = 0.25   # avatar_core.js followTrack 과 같은 값 — 두 경로가 다르게 보이면 안 된다


def emotion_exp_schedule(segments, n_frames: int, fps: int = FPS):
    """문장별 감정 → **프레임별** 표정 델타 리스트 (전부 중립이면 None).

    segments: [(시작초, 감정, 강도)] — 시작초는 오디오 기준(app.sentence_starts 산출).

    실시간 경로는 문장마다 표정을 바꾸는데 영상은 첫 문장 감정으로 통짜였다. 주입 지점이
    이미 프레임 단위라(exp_delta_schedule) 스케줄만 채우면 된다 — 새 배관이 필요 없다.

    **목소리 톤은 못 따라온다**: edge-tts 프로소디는 발화 하나에 하나고, 사후 ffmpeg
    필터도 구간별로 못 건다. 얼굴만 문장을 따라가고 톤은 첫 문장 것으로 남는다.
    """
    if not segments:
        return None
    import numpy as np
    zero = np.zeros((21, 3), dtype="float32")
    starts = [s for s, _, _ in segments]
    deltas = [emotion_exp_delta(e, i) for _, e, i in segments]
    if all(d is None for d in deltas):
        return None
    deltas = [zero if d is None else d for d in deltas]

    sched, k = [], 0
    for f in range(n_frames):
        t = f / fps
        while k + 1 < len(starts) and t >= starts[k + 1]:
            k += 1
        d = deltas[k]
        # 문장이 바뀌는 순간 표정이 튀지 않게 앞 문장에서 선형으로 넘어온다.
        gap = t - starts[k]
        if k > 0 and gap < CROSSFADE_S:
            a = gap / CROSSFADE_S
            d = deltas[k - 1] * (1 - a) + d * a
        sched.append(None if not d.any() else d)
    return sched


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
    # 4프레임 = 160ms @25fps. 실시간(avatar_core makeBlink)은 140ms 연속 곡선인데 여기선
    # 못 맞춘다 — 25fps 에서 140ms 는 3.5프레임이고 주입은 프레임 단위다. 곡선 모양도 안 맞춘다:
    # 실시간의 연속화는 소비 측 blink>0.5 이진 분기 때문이었고 여기엔 그 분기가 없다.
    env = (edge, closed, closed, edge)
    sched = [None] * n_frames
    step = max(int(interval_s * FPS), len(env) + 2)
    # 간격을 흔든다 — 정확히 등간격이면 메트로놈처럼 보인다. 폭은 실시간과 같은 ±30%.
    # 시드 고정: 영상마다 같은 패턴이어도 한 영상 안에서 흔들리면 목적은 달성되고,
    # 대신 self-check 와 재현이 살아 있다(같은 입력 → 같은 스케줄).
    rng = random.Random(0)
    start = step
    while start + len(env) < n_frames:
        for off, v in enumerate(env):
            sched[start + off] = v
        start += max(len(env) + 2, int(step * (0.7 + rng.random() * 0.6)))
    return sched


# 상류 렌더 루프가 프레임을 쌓는 리스트에 넣을 자리표시자. 위 주석 참조.
_EMPTY_FRAME = None   # 첫 사용 시 numpy 로 만든다 (모듈 임포트에 numpy 를 끌지 않으려고)


def _partial_fields(cls, kwargs):
    return cls(**{k: v for k, v in kwargs.items() if hasattr(cls, k)})


# can_animate 용 InsightFace 로더. 단독 로드 1.11s, 판정은 46~88ms 라 캐시가 필요하다.
# AvatarPipeline._load() 가 여기에 자기 cropper 를 대입한다(아래 이유 참조).
_cropper = None
_cropper_lock = threading.Lock()


def can_animate(image: Path) -> bool:
    """이 그림이 영상 경로를 탈 수 있는가 = LivePortrait 의 얼굴 검출이 되는가.

    LivePortrait 는 실사 포트레이트로 학습돼서 손그림 전신 낙서는 얼굴을 못 찾고
    그림 전체를 얼굴로 잡아 통째로 늘렸다 줄인다. 실측상 머리만 잘라 줘도 검출이
    안 되니 자동 크롭으로는 못 구제한다 — 등록 시점에 걸러 스프라이트 전용으로 돌린다.

    InsightFace 는 CPU 로 돈다(이 환경의 onnxruntime CUDA provider 가 libcublasLt.so.11
    을 못 찾는다) — GPU 락 없이 불러도 된다.
    """
    # JoyVASA 임포트는 반드시 함수 안에서. 모듈 최상위로 올리면 app 을 import 하는
    # 것만으로 onnxruntime·insightface 가 딸려 와 테스트가 통째로 무거워진다.
    from src.config.argument_config import ArgumentConfig
    from src.config.crop_config import CropConfig
    from src.utils.cropper import Cropper
    from src.utils.io import load_image_rgb

    img = Path(image)
    if not img.exists():
        return False
    try:
        # 로더 생성과 판정을 같이 잠근다 — 등록·미리보기 엔드포인트가 동기 def 라
        # FastAPI 스레드풀에서 동시에 들어오면 Cropper 를 두 번 올린다.
        # 한계: _cropper 가 _pipe.cropper 일 때 렌더 경로(execute)가 같은 객체를
        # 이 락 밖에서 쓴다 — JoyVASA 를 안 건드리고는 거기까지 못 묶는다.
        with _cropper_lock:
            global _cropper
            if _cropper is None:
                # crop_cfg 를 _load() 와 똑같이 만든다(ArgumentConfig 를 거친다). 그냥
                # CropConfig() 를 쓰면 det_thresh 가 0.1 인데 pipe 의 것은 0.15 라 —
                # 판정이 "그 프로세스에서 영상 잡을 돌린 적 있느냐"에 따라 달라진다.
                # 증상: 미리보기는 가능이라 했는데 등록하니 video:false (또는 그 반대).
                _cropper = Cropper(crop_cfg=_partial_fields(CropConfig, ArgumentConfig().__dict__))
            return _cropper.crop_source_image(load_image_rgb(str(img)), _cropper.crop_cfg) is not None
    except Exception as e:
        # 검출 실패와 같게 False 지만 조용히 넘기지는 않는다 — 가중치 경로가 어긋나거나
        # onnxruntime 이 깨지면 전부 False 가 되고, 증상은 "영상 되는 캐릭터가 하나도
        # 안 생김" 뿐이라 아무도 원인을 못 찾는다.
        print(f"[can_animate] 판정 실패 → 영상 불가로 처리 ({type(e).__name__}: {e}): {img}")
        return False


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
        _missing = [m for m in PATCH_MARKERS if m not in _src]
        if _missing:
            raise RuntimeError(
                f"JoyVASA 패치가 적용돼 있지 않습니다 (누락: {', '.join(_missing)}). "
                "patches/README.md 참조 — cd JoyVASA && git apply ../patches/joyvasa_inject.patch")
        # generate() 가 P.images2video 를 무력화해 일괄 인코딩을 끈다. 상류가 이 호출을
        # 없애거나 이름을 바꾸면 몽키패치가 조용히 헛돌고 — 증상은 "왜 느리지" + output/ 에
        # 임시 mp4 누적뿐이다. (모듈 전역이 아니라 `video.images2video` 같은 속성 접근으로
        # 바뀌는 경우까지는 이 문자열 검사로 못 잡는다.)
        if STREAM_MARKER not in _src:
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
        # can_animate 가 이 pipe 의 cropper 를 쓰게 한다 — InsightFace 두 벌은 메모리 낭비다.
        # 재사용이 한쪽 방향뿐인 이유: LivePortraitPipeline.__init__ 이 Cropper 를 무조건
        # 자기가 만들어서(live_portrait_wmg_pipeline.py:35) 우리 것을 주입할 수가 없다.
        # 그래서 pipe 가 뜬 뒤 전역을 그쪽으로 갈아끼운다 — can_animate 가 먼저 올려둔
        # 단독 Cropper 는 참조가 끊겨 회수된다.
        global _cropper
        with _cropper_lock:
            _cropper = self._pipe.cropper
        return self._pipe

    def generate(self, wav: Path, out_mp4: Path,
                 blink_interval: float = 4.0, blink_strength: float = 1.0,
                 exp_delta=None, emo_segments=None, image: Path = None) -> Path:
        """wav + 그림 → 립싱크 mp4 (blink_interval초마다 강제 깜빡임, 0 = 주입 없음).

        image: **필수.** 예전엔 없으면 리포 최상위를 알파벳 순으로 훑어 첫 이미지를 썼는데,
            아무도 고르지 않은 파일이 얼굴이 됐다 — 실제로 테스트 픽스처 test_1.png(돼지
            낙서)가 잡혀서 "/" 로 만든 영상이 전부 돼지였다. 얼굴은 호출측이 정한다.
        emo_segments: [(시작초, 감정, 강도)] — 문장마다 표정이 바뀐다. 앱은 이쪽만 쓴다.
        exp_delta: (21,3) 상수 델타. tools/exp_index_probe.py 가 인덱스를 실측할 때 쓴다.

        do_crop 인자는 없앴다. 상류 execute() 가 args 에서 읽는 건 reference/audio/
        output_dir + 우리 스케줄 둘뿐이고 flag_do_crop 은 **안 읽는다** — 크롭 여부는
        로드 시점의 inference_cfg 값(=self.do_crop)으로만 정해진다. 요청마다 바꿀 수
        있는 것처럼 보이는 노브였는데 실제로는 아무 일도 안 했다.
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
            flag_do_crop=self.do_crop)   # execute() 는 안 읽는다 — 위 docstring 참조
        args.eye_ratio_schedule = sched
        # 표정 델타. 문장 단위로 바뀌므로 프레임별 스케줄이다 — 상수 exp_delta 는 인덱스
        # 실측용이라 n_frames 만큼 펴서 같은 자리에 넣는다.
        if emo_segments:
            args.exp_delta_schedule = emotion_exp_schedule(emo_segments, n_frames, FPS)
        else:
            args.exp_delta_schedule = [exp_delta] * n_frames if exp_delta is not None else None

        # 프레임을 나오는 대로 인코딩한다 — 상류의 끝단 일괄 인코딩은 무력화한다.
        # parse_output 이 렌더 루프에서 프레임당 정확히 한 번, 순서대로 불리는 유일한 지점이라
        # JoyVASA 패치를 늘리지 않고 여기에 붙는다.
        global _EMPTY_FRAME
        if _EMPTY_FRAME is None:
            import numpy as np
            _EMPTY_FRAME = np.empty((1, 0, 0, 3), dtype="uint8")
        # 상류와 같은 조건식을 본다. self.do_crop 이 False 인 동안은 걸릴 일이 없지만,
        # 그걸 켜는 날 pasteback 이 살아나 이 인코더가 합성 전 프레임을 내보내게 된다.
        inf = pipe.live_portrait_wrapper.inference_cfg
        if inf.flag_pasteback and inf.flag_do_crop and inf.flag_stitching:
            raise RuntimeError("pasteback 경로는 parse_output 뒤에 프레임을 또 합성한다 — "
                               "스트리밍 인코더가 합성 전 프레임을 내보내므로 지원하지 않는다.")
        import torch   # generate() 스코프에는 없다 — _load() 의 것은 그 함수 지역이다
        import src.live_portrait_wmg_pipeline as P
        lw = pipe.live_portrait_wrapper
        enc, orig = _StreamEncoder(out_mp4, wav), lw.parse_output
        keep = (P.images2video, P.add_audio_to_video)
        # **generate() 는 동시에 두 번 돌면 안 된다.** 아래 대입이 모듈 전역(P.*)과 공유
        # 인스턴스 속성(lw.parse_output)이라, 두 잡이 겹치면 한쪽 finally 가 다른 쪽이
        # 실행 중인 원본을 복구하고 enc 도 서로 남의 프레임을 받는다. 지금 안전한 이유는
        # app.py 의 워커 스레드가 **하나**이기 때문이다 — 그 전제가 여기 없으면 워커를
        # 늘리는 순간 조용히 깨진다. 늘릴 거면 여기에 락을 걸거나 프로세스를 나눌 것.

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
            # 상류는 이 반환값의 [0] 을 I_p_lst 에 쌓는데, 그 리스트를 읽는 건 아래에서
            # 죽여 둔 images2video 뿐이다 — 즉 전량이 죽은 보관이다. 프레임당 786KB 라
            # 오디오 1초에 19.7MB, 60초 내레이션이면 1.2GB 가 된다.
            # **슬라이스(f[:, :0])로 줄이면 안 된다** — 뷰라서 원본 버퍼가 그대로 살아 있다.
            return _EMPTY_FRAME

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
    # 간격이 흔들린다 — 등간격으로 되돌아가면(메트로놈) 여기서 걸린다.
    long = make_blink_schedule(300, 2.0, 1.0)
    starts = [i for i, v in enumerate(long) if v is not None and long[i - 1] is None]
    gaps = {b - a for a, b in zip(starts, starts[1:])}
    assert len(gaps) > 1, f"깜빡임 간격이 등간격이다: {gaps}"
    assert all(0.7 * 50 <= g <= 1.3 * 50 for g in gaps), gaps   # step=50 의 ±30%
    print("blink schedule self-check OK")

    # 표정 크기 (GPU 불필요). 아래 스케줄 검증은 양변이 같은 함수를 지나서 표의 절대
    # 크기를 못 잡는다 — joy 를 0.05 로 잘못 접어 넣어도 전부 통과한다. 그래서 한 점을 박는다.
    assert abs(emotion_exp_delta("joy", 1.0)[20, 1] + 0.005) < 1e-9   # 상한 접어넣기 전과 동일
    assert all(abs(s) <= SMILE_MAX for _, s, _ in EMOTION_FACE.values()), "미소가 안전 상한을 넘었다"
    # 눈 축도 같은 이유로 한 점을 박는다 — 스케줄 검증은 표의 절대 크기를 못 잡는다.
    assert abs(emotion_exp_delta("surprise", 1.0)[11, 1] + 0.020) < 1e-9   # 0.8 × 0.025
    assert all(abs(e) * EYE_WIDE_SCALE <= EYE_WIDE_SAFE + 1e-9
               for _, _, e in EMOTION_FACE.values()), "눈 크게 뜨기가 안전 상한을 넘었다(홍채가 뭉개진다)"
    print("emotion magnitude self-check OK")

    # 문장별 표정 스케줄 (GPU 불필요)
    sch = emotion_exp_schedule([(0.0, "joy", 1.0), (2.0, "angry", 1.0)], 100, 25)
    assert len(sch) == 100
    joy, angry = emotion_exp_delta("joy", 1.0), emotion_exp_delta("angry", 1.0)
    assert (sch[10] == joy).all()                                  # 첫 문장 구간
    assert (sch[99] == angry).all()                                # 두 번째 문장 정착
    mid = sch[52]                                                  # 2.08s = 전환 중(0.25s 페이드)
    assert not (mid == joy).all() and not (mid == angry).all(), "크로스페이드가 안 걸렸다"
    assert emotion_exp_schedule([(0.0, "neutral", 1.0)], 10) is None    # 중립뿐이면 주입 안 함
    assert emotion_exp_schedule([], 10) is None
    # 구간이 하나면 예전 동작(통짜)과 같아야 한다
    one = emotion_exp_schedule([(0.0, "joy", 1.0)], 30)
    assert all((d == joy).all() for d in one)
    print("emotion schedule self-check OK")

    # 얼굴 검출 판정 (GPU 불필요 — InsightFace 는 CPU 로 돈다)
    assert can_animate(JOYVASA / "assets/examples/imgs/joyvasa_005.png")  # 실사 → 영상 가능
    assert not can_animate(ROOT / "test_1.png")                           # 돼지 낙서 → 불가
    assert not can_animate(ROOT / "없는파일.png")                          # 파일 없으면 불가
    print("can_animate self-check OK")
