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
                    deletable=False, persona=None, eye_blink=True):
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
    # 눈 중심 — 부분 감김·눈 커짐이 이 점을 축으로 세로 스케일한다. 안 적으면 렌더가
    # [256,258](정면 인물화 기준)로 폴백하는데, 화이트보드 그림처럼 얼굴이 위쪽에 있는
    # 캐릭터는 축이 180px 넘게 어긋나 눈이 캔버스 밖으로 날아간다.
    ecs = [T(cx, cy) for cx, cy, _ in eyes.values()]
    manifest = {
        "name": name,
        "pupilRange": 0, "browRange": 0, "jawDrop": jaw_drop,
        "eyeCenter": [round(sum(p[0] for p in ecs) / len(ecs)),
                      round(sum(p[1] for p in ecs) / len(ecs))],
        # 눈 반높이 — 눈꺼풀 클립 경계. 반박스가 곧 눈 높이의 절반이다.
        "eyeHalf": round(sum(hb for _, _, hb in eyes.values()) / len(eyes) * s),
        # 이미 감긴 눈(실눈)은 깜빡이지 않는다 — 가릴 눈알이 없어 내리면 선만 토막난다.
        # 그림에서 자동 판별하려 했지만(선 밀도·두께) 실눈과 작은 눈이 안 갈려 물어보는 쪽을 택했다.
        "eyeBlink": eye_blink,
        "mouthCenter": [round(mcx), round(mcy)],
        "proceduralMouth": True,
        "mouthStyle": {**DEFAULT_STYLE, **(mouth_style or {})},
        "deletable": deletable,
    }
    if persona:
        manifest["persona"] = persona
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))

    for pv, state in (("preview.png", "open"), ("preview_blink.png", "closed")):
        comp = Image.open(out / "base.png").copy()
        for side in eyes:
            comp.alpha_composite(Image.open(out / f"eye_{side}_{state}.png"))
        comp.save(out / pv)
    return out
