"""그림 → 퍼펫 캐릭터 빌더 (공용 모듈).

원리: 그림을 512 캔버스에 맞추고, 눈·입을 주변 배경색(테두리 중앙값)으로 지운 base를 만든 뒤
눈은 원본 패치(뜬눈)/호(감은눈) 스프라이트로, 입은 벡터 입(proceduralMouth)으로 대체.
모든 좌표는 원본 이미지 픽셀 기준.
"""
import json
import statistics
from pathlib import Path

from PIL import Image, ImageDraw

CANVAS = 512
DEFAULT_STYLE = {"line": "#2b2b2b", "fill": "#8a3535", "tongue": "#d97b7b",
                 "teeth": "#ffffff", "width": 26}


def _border_median(img, box, ring=4):
    """box 바깥 ring px 테두리 픽셀들의 중앙값 색."""
    x0, y0, x1, y1 = box
    px = img.load()
    samples = []
    for x in range(max(0, x0 - ring), min(img.width, x1 + ring)):
        for y in list(range(max(0, y0 - ring), y0)) + list(range(y1, min(img.height, y1 + ring))):
            samples.append(px[x, y])
    for y in range(max(0, y0), min(img.height, y1)):
        for x in list(range(max(0, x0 - ring), x0)) + list(range(x1, min(img.width, x1 + ring))):
            samples.append(px[x, y])
    return tuple(int(statistics.median(c[i] for c in samples)) for i in range(3))


def build_character(src_path, out_dir, name, eyes, mouth_box, mouth_center,
                    mouth_style=None, jaw_drop=6, closed_eye=("#1a1a1a", 4),
                    deletable=False):
    """eyes: {"L": (cx, cy, 반박스), "R": ...} / mouth_box: (x0,y0,x1,y1) / 좌표는 원본 픽셀."""
    src = Image.open(src_path).convert("RGB")
    s = min(CANVAS / src.width, CANVAS / src.height)
    w, h = int(src.width * s), int(src.height * s)
    ox, oy = (CANVAS - w) // 2, (CANVAS - h) // 2
    T = lambda x, y: (ox + x * s, oy + y * s)

    edge = [src.getpixel((x, y)) for x in range(src.width) for y in (0, 1, src.height - 2, src.height - 1)] \
         + [src.getpixel((x, y)) for y in range(src.height) for x in (0, 1, src.width - 2, src.width - 1)]
    bg = tuple(int(statistics.median(c[i] for c in edge)) for i in range(3))
    base = Image.new("RGB", (CANVAS, CANVAS), bg)
    base.paste(src.resize((w, h), Image.LANCZOS), (ox, oy))

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    d = ImageDraw.Draw(base)

    for side, (cx, cy, hb) in eyes.items():
        x0, y0 = T(cx - hb, cy - hb)
        x1, y1 = T(cx + hb, cy + hb)
        box = tuple(map(int, (x0, y0, x1, y1)))

        open_sprite = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
        open_sprite.paste(base.crop(box).convert("RGBA"), box[:2])
        open_sprite.save(out / f"eye_{side}_open.png")

        closed = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
        color, lw = closed_eye
        ccx, ccy = T(cx, cy)
        r = hb * s
        ImageDraw.Draw(closed).arc((ccx - r, ccy - r * 0.6, ccx + r, ccy + r), 20, 160,
                                   fill=color, width=lw)
        closed.save(out / f"eye_{side}_closed.png")

        d.rectangle(box, fill=_border_median(base, box))

    mb = mouth_box
    box = tuple(map(int, (*T(mb[0], mb[1]), *T(mb[2], mb[3]))))
    d.rectangle(box, fill=_border_median(base, box))
    base.convert("RGBA").save(out / "base.png")

    mcx, mcy = T(*mouth_center)
    manifest = {
        "name": name,
        "pupilRange": 0, "browRange": 0, "jawDrop": jaw_drop,
        "mouthCenter": [round(mcx), round(mcy)],
        "proceduralMouth": True,
        "mouthStyle": {**DEFAULT_STYLE, **(mouth_style or {})},
        "deletable": deletable,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

    for pv, state in (("preview.png", "open"), ("preview_blink.png", "closed")):
        comp = Image.open(out / "base.png").copy()
        for side in eyes:
            comp.alpha_composite(Image.open(out / f"eye_{side}_{state}.png"))
        comp.save(out / pv)
    return out
