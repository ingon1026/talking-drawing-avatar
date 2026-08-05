"""LivePortrait 표정 벡터(exp 21×3) 인덱스가 얼굴 어디를 움직이는지 찾는다.

    PYTHONPATH= .venv/bin/python tools/exp_index_probe.py --image <그림> [--idx 3 4 5]

각 인덱스에 델타를 주고 1프레임씩 렌더해, 중립 프레임 대비 어느 영역이 변했는지 출력한다.
JoyVASA 는 이 벡터의 의미를 문서화하지 않았고 소스 주석에 입술 인덱스([6,12,14,17,19,20])만
있어서 나머지는 실측으로 찾아야 한다.

읽는 법 — 두 번 크게 틀린 뒤에 얻은 규칙이다:

  * **`null` 행을 먼저 본다.** 델타 없이 한 번 더 렌더한 차분이다. 시드를 박아서 0 이어야
    정상이고, 0 이 아니면 그 값이 노이즈 바닥이라 그 아래 신호는 못 믿는다.
  * **국소도로 판정한다.** "어느 대역이 가장 큰가"로 보면 틀린다 — 3·4·11·15 는 얼굴
    전체를 상하로 옮겨서 눈썹 대역이 커 보이고, 실제로 눈썹으로 오판한 적이 있다.
  * **숫자로 끝내지 말고 렌더를 눈으로 본다.** 눈썹 부호를 반대로 적은 적이 있다
    (앞머리가 눈썹을 가리는 그림이라 얼굴 이동을 눈썹으로 봤다). 눈썹을 볼 거면
    이마가 드러난 그림을 쓸 것 — JoyVASA/assets/examples/imgs/joyvasa_005.png 등.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LIP_IDX = [6, 12, 14, 17, 19, 20]   # 소스 코드에 명시된 입술 인덱스


def _first_frame(mp4: Path):
    import subprocess
    png = mp4.with_suffix(".probe.png")
    subprocess.run(["ffmpeg", "-y", "-i", str(mp4), "-vframes", "1", "-vf", "scale=512:512",
                    str(png)], check=True, capture_output=True)
    a = Image.open(png).convert("L")
    png.unlink(missing_ok=True)
    return a


def bands(diff, thr=18):
    """변화 픽셀을 눈썹/눈/입 대역으로 센다.

    실측 기준(512 프레임, 상반신 구도): 눈썹 y≈175, 눈 y≈196, 입 y≈253.
    대역을 균등 삼등분하면 눈과 입이 한 칸에 들어가 구분이 안 된다.
    """
    h = diff.shape[0]
    cuts = [(int(h * 0.31), int(h * 0.37)),   # 눈썹
            (int(h * 0.36), int(h * 0.43)),   # 눈
            (int(h * 0.45), int(h * 0.55))]   # 입
    return [int((diff[a:b] > thr).sum()) for a, b in cuts]


# 입 주변만 잘라 좌/우로 나눈다. 입꼬리는 한쪽만 움직이는 경우가 많아
# (한 인덱스 = 한 implicit keypoint) 좌우 비대칭이 판정 근거가 된다.
#
# 좌표는 한복 소녀(orig_rgb.png) 512 프레임에 맞춘 값이다 — **다른 그림에서는 입L/입R
# 컬럼이 무의미하다.** 얼굴 위치가 다르면 --mouth-roi 로 넘기거나 그 컬럼을 무시할 것.
MOUTH_ROI = (230, 290, 190, 320)   # y0, y1, x0, x1  (512 프레임 기준)


def mouth_lr(diff, roi, thr=18):
    y0, y1, x0, x1 = roi
    roi = diff[y0:y1, x0:x1] > thr
    mid = roi.shape[1] // 2
    return int(roi[:, :mid].sum()), int(roi[:, mid:].sum())


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--image", required=True)
    ap.add_argument("--idx", type=int, nargs="*", help="검사할 인덱스(생략 시 0~20 전부)")
    ap.add_argument("--amp", type=float, default=0.06, help="델타 크기")
    ap.add_argument("--axis", type=int, default=1, help="0=x 1=y 2=z")
    ap.add_argument("--mouth-roi", type=int, nargs=4, metavar=("Y0", "Y1", "X0", "X1"),
                    default=MOUTH_ROI, help="입 ROI(512 프레임 기준) — 그림마다 다르다")
    a = ap.parse_args()
    sys.argv = sys.argv[:1]   # JoyVASA ArgumentConfig 가 argv 를 다시 파싱한다

    # app 을 먼저 import 한다 — pipeline 이 sys.path 맨 앞에 JoyVASA 를 끼워 넣어서,
    # 그 뒤에 부르면 `app` 이 JoyVASA/app.py 로 잡히고 animal 파이프라인(XPose)까지
    # 끌려와 ModuleNotFoundError 로 죽는다.
    from app import tts_to_wav  # noqa: E402
    out_dir = ROOT / "output"
    wav = out_dir / "_probe.wav"
    if not wav.exists():
        tts_to_wav("아", "ko-KR-InJoonNeural", wav)

    from pipeline import AvatarPipeline  # noqa: E402

    pipe = AvatarPipeline()
    base = np.asarray(_first_frame(_gen(pipe, a.image, wav, out_dir, None)), dtype=np.int16)
    idxs = a.idx if a.idx else list(range(21))
    print(f"축={a.axis} 진폭={a.amp}  (입술 인덱스 {LIP_IDX} 는 소스에 명시됨)")
    print(f"{'idx':>4} {'눈썹':>7} {'눈':>7} {'입':>7}  {'국소':>5} {'입L':>5} {'입R':>5}  판정")

    def row(label, cur):
        diff = np.abs(cur - base)
        b = bands(diff)
        lo, ro = mouth_lr(diff, tuple(a.mouth_roi))
        # 국소성 판정 — 어느 대역이 "가장 큰가"가 아니라 "그 대역만 변했는가".
        # 인덱스 3·4 는 얼굴 전체를 상하로 옮겨서 세 대역이 다 커진다. 그건 눈썹이 아니다.
        tot = sum(b) or 1
        loc = max(x / tot for x in b)
        tag = "-" if max(b) < 40 else (
            f"{['눈썹', '눈', '입'][int(np.argmax(b))]}" if loc >= 0.55 else "전역(머리)")
        print(f"{label:>4} {b[0]:>7} {b[1]:>7} {b[2]:>7}   {loc:.2f} {lo:>5} {ro:>5}  {tag}")

    # 노이즈 바닥 — 델타 없이 한 번 더 렌더한 차분. 이 값보다 작은 변화는 의미 없다.
    row("null", np.asarray(_first_frame(_gen(pipe, a.image, wav, out_dir, None)), dtype=np.int16))
    for i in idxs:
        d = np.zeros((21, 3), dtype=np.float32)
        d[i, a.axis] = a.amp
        row(str(i), np.asarray(_first_frame(_gen(pipe, a.image, wav, out_dir, d)), dtype=np.int16))


def _gen(pipe, img, wav, out_dir, delta):
    # JoyVASA 의 모션 생성은 디퓨전 샘플링이라 시드를 안 고정하면 같은 wav·같은 그림도
    # 매번 다른 입 모양이 나온다. 실측해 보니 그 노이즈만으로 입 대역 변화 픽셀이 321개 —
    # 델타의 신호(600 안팎)와 구분이 안 된다. 렌더마다 같은 시드를 박아 차분을 깨끗하게 만든다.
    import torch
    torch.manual_seed(0)
    torch.cuda.manual_seed_all(0)
    out = out_dir / "_probe.mp4"
    pipe.generate(wav, out, blink_interval=0, blink_strength=0, image=Path(img),
                  do_crop=True, exp_delta=delta)
    return out


if __name__ == "__main__":
    main()
