"""AI 재생성 비짐 이미지에서 입 획만 분리해 캐릭터 스프라이트로 이식.

전체 재생성(구도 어긋남) 이미지 대응: 이미지별 입 bbox를 수동 지정하고,
bbox 안에서 어두운 획+붉은 내부를 마스크(구멍 채움)로 떼어 투명 스프라이트化.
캔버스 앵커는 "입 상단 y + 가로 중심 x" (턱이 아래로 자라는 자연스러운 배치).

사용: .venv/bin/python tools/extract_mouth_sprites.py pig
"""
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent
CANVAS = 512

CONFIG = {
    "pig": {
        "char": "pig",
        "src_original": ROOT / "test_1.png",          # closed 입 추출용
        "closed_box": (160, 124, 224, 155),           # 원본의 다문 입
        "viseme_dir": ROOT / "mouths_pig",
        "anchor": (248, 132),                          # 캔버스: (가로중심 x, 입 상단 y)
        "boxes": {                                     # 각 생성 이미지 안의 입 bbox
            "A": (146, 106, 230, 198),
            "E": (172, 116, 250, 168),
            "I": (186, 134, 258, 166),
            "M": (192, 154, 256, 180),
            "O": (178, 128, 242, 188),
            "U": (206, 136, 246, 176),
        },
        "ref_width": 396,  # 크기 정규화 기준(원본 폭) — 생성 이미지 폭과의 비율로 스케일
    },
}


def extract_sprite(img_path, box, scale, anchor):
    img = Image.open(img_path).convert("RGB")
    arr = np.asarray(img.crop(box)).astype(int)
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    dark = arr.max(axis=2) < 110                       # 검은 마커 획
    red = (r > 100) & (r - b > 30) & (r - g > 25)      # 붉은 입 안쪽
    mask = ndimage.binary_closing(dark | red, iterations=2)
    mask = ndimage.binary_fill_holes(mask)             # 이빨(흰색) 포함해 내부 채움
    labels, n = ndimage.label(mask)
    if n > 1:  # 가장 큰 덩어리만 (주변 획 오염 제거)
        sizes = ndimage.sum(mask, labels, range(1, n + 1))
        mask = labels == (1 + int(np.argmax(sizes)))
    alpha = (mask * 255).astype(np.uint8)

    patch = img.crop(box).convert("RGBA")
    patch.putalpha(Image.fromarray(alpha).filter(ImageFilter.GaussianBlur(1)))
    w, h = round(patch.width * scale), round(patch.height * scale)
    patch = patch.resize((w, h), Image.LANCZOS)

    # 마스크 실제 범위 기준 앵커 (bbox 여백 무시)
    ys, xs = np.nonzero(alpha)
    cx_frac = (xs.min() + xs.max()) / 2 / alpha.shape[1]
    top_frac = ys.min() / alpha.shape[0]

    sprite = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    px = round(anchor[0] - w * cx_frac)
    py = round(anchor[1] - h * top_frac)
    sprite.alpha_composite(patch, (px, py))
    return sprite


def main(cfg_id):
    cfg = CONFIG[cfg_id]
    out = ROOT / "assets_characters" / cfg["char"]
    made = []

    for v, box in cfg["boxes"].items():
        f = Path(cfg["viseme_dir"]) / f"{v}.png"
        with Image.open(f) as im:
            scale = cfg["ref_width"] / im.width * (CANVAS / 496)  # 생성크기→원본→캔버스
        extract_sprite(f, box, scale, cfg["anchor"]).save(out / f"mouth_{v}.png")
        made.append(v)

    # closed: 원본 그림의 다문 입을 같은 방식으로
    scale = CANVAS / 496
    extract_sprite(cfg["src_original"], cfg["closed_box"], scale, cfg["anchor"]).save(out / "mouth_closed.png")
    made.append("closed")

    mf = out / "manifest.json"
    manifest = json.loads(mf.read_text())
    manifest["proceduralMouth"] = False
    mf.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"OK: {made} → {out}, 스프라이트 모드 전환")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "pig")
