"""LivePortrait 표정 벡터(exp 21×3) 인덱스가 얼굴 어디를 움직이는지 찾는다.

    PYTHONPATH= .venv/bin/python tools/exp_index_probe.py --image <그림> [--idx 3 4 5]

각 인덱스에 델타를 주고 1프레임씩 렌더해, 중립 프레임 대비 어느 영역이 변했는지 출력한다.
JoyVASA 는 이 벡터의 의미를 문서화하지 않았고 소스 주석에 입술 인덱스([6,12,14,17,19,20])만
있어서, 눈썹·눈꼬리는 실측으로 찾아야 한다.

출력의 y 대역은 얼굴을 셋으로 나눈 것: 상(눈썹) / 중(눈) / 하(입).
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


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--image", required=True)
    ap.add_argument("--idx", type=int, nargs="*", help="검사할 인덱스(생략 시 0~20 전부)")
    ap.add_argument("--amp", type=float, default=0.06, help="델타 크기")
    ap.add_argument("--axis", type=int, default=1, help="0=x 1=y 2=z")
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
    print(f"{'idx':>4} {'눈썹':>7} {'눈':>7} {'입':>7}  {'국소':>5}  판정")
    for i in idxs:
        d = np.zeros((21, 3), dtype=np.float32)
        d[i, a.axis] = a.amp
        cur = np.asarray(_first_frame(_gen(pipe, a.image, wav, out_dir, d)), dtype=np.int16)
        b = bands(np.abs(cur - base))
        # 국소성 판정 — 어느 대역이 "가장 큰가"가 아니라 "그 대역만 변했는가".
        # 인덱스 3·4 는 얼굴 전체를 상하로 옮겨서 세 대역이 다 커진다. 그건 눈썹이 아니다.
        tot = sum(b) or 1
        share = [x / tot for x in b]
        loc = max(share)
        tag = "-" if max(b) < 40 else (
            f"{['눈썹', '눈', '입'][int(np.argmax(b))]}" if loc >= 0.55 else "전역(머리)")
        print(f"{i:>4} {b[0]:>7} {b[1]:>7} {b[2]:>7}   {loc:.2f}  {tag}")


def _gen(pipe, img, wav, out_dir, delta):
    out = out_dir / "_probe.mp4"
    pipe.generate(wav, out, blink_interval=0, blink_strength=0, image=Path(img),
                  do_crop=True, exp_delta=delta)
    return out


if __name__ == "__main__":
    main()
