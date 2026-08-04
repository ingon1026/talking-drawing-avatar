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


def ink_color(img, box, frac=0.05):
    """box 안에서 가장 어두운 frac 비율 픽셀의 채널별 중앙값 — 그 그림의 실제 '선 색'.

    감은 눈 호를 검정(#1a1a1a) 고정으로 그리면 연필 그림·갈색 선 캐릭터에서 그 획만
    남의 것처럼 튄다. 자매 리포 drawface-live 가 같은 방식(warp.js 의 ink(), 휘도 하위
    5% 중앙값)으로 그림에서 선 색을 뽑아 쓰고 있어 그대로 옮겼다.
    """
    px = img.convert("RGB").load()
    pxs = [px[x, y] for y in range(box[1], box[3]) for x in range(box[0], box[2])]
    if not pxs:
        return (26, 26, 26)
    pxs.sort(key=sum)
    keep = pxs[:max(1, int(len(pxs) * frac))]
    return tuple(int(statistics.median(c[i] for c in keep)) for i in range(3))


def snap_eye_box(img, box, pad=4, cap_scale=2.5, max_iter=8):
    """클릭 상자를 그 안팎 잉크(어두운 픽셀)에 맞춘다 — 작으면 넓히고 크면 조인다.

    사용자는 눈 *중심*만 클릭하고 크기는 지정하지 않는다. 반경이 '원본 최대변의 3%'
    고정이라 실제 눈과 무관해서, 작은 눈은 상자가 남아돌고(돼지 24px 상자에 점 11px)
    큰 눈은 상자가 모자라 눈 일부만 잘렸다 — 나머지가 base 에 남아 눈꺼풀이 움직여도
    화면이 안 변했다. 눈꺼풀 압축이 눈의 실제 하단을 피벗으로 쓰므로 상자가 눈에 맞아야 한다.

    자매 리포 drawface-live 의 expandBoxToInk(imageops.js:169) 와 같은 방식 —
    pad 만큼 넓혀 잉크 bbox 를 다시 재기를 수렴할 때까지 반복하되, 눈썹·머리카락까지
    빨아들이지 않게 원래 상자의 cap_scale 배 안으로 제한한다.
    """
    px = img.convert("RGB").load()
    w, h = img.size
    x0, y0, x1, y1 = (round(v) for v in box)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    mw, mh = max(24, (x1 - x0) * cap_scale), max(24, (y1 - y0) * cap_scale)
    bnd = (round(cx - mw / 2), round(cy - mh / 2), round(cx + mw / 2), round(cy + mh / 2))
    for _ in range(max_iter):
        sx, sy = max(0, bnd[0], x0 - pad), max(0, bnd[1], y0 - pad)
        ex, ey = min(w - 1, bnd[2], x1 + pad), min(h - 1, bnd[3], y1 + pad)
        xs, ys = [], []
        for y in range(sy, ey + 1):
            for x in range(sx, ex + 1):
                if sum(px[x, y]) < 300:
                    xs.append(x)
                    ys.append(y)
        if not xs:
            return box
        nb = (min(xs), min(ys), max(xs) + 1, max(ys) + 1)
        if nb == (x0, y0, x1, y1):
            break
        x0, y0, x1, y1 = nb
    return (x0, y0, x1, y1)


def build_character(src_path, out_dir, name, eyes, mouth_box, mouth_center,
                    mouth_style=None, jaw_drop=6, closed_eye=(None, 4),
                    deletable=False, persona=None):
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

    eye_boxes = {}
    for side, (cx, cy, hb) in eyes.items():
        x0, y0 = T(cx - hb, cy - hb)
        x1, y1 = T(cx + hb, cy + hb)
        box = snap_eye_box(base, tuple(map(int, (x0, y0, x1, y1))))
        eye_boxes[side] = box

        open_sprite = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
        open_sprite.paste(base.crop(box).convert("RGBA"), box[:2])
        open_sprite.save(out / f"eye_{side}_open.png")

        closed = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
        color, lw = closed_eye
        if color is None:            # 색을 안 주면 그 눈의 실제 선 색을 그림에서 뽑는다
            color = ink_color(base, box)
        # 감은 눈 호는 조인 상자를 따른다 — 클릭 반경을 쓰면 눈보다 큰 호가 그려진다.
        ccx, ccy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        r = (box[2] - box[0]) / 2
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
    bs = list(eye_boxes.values())
    manifest = {
        "name": name,
        "pupilRange": 0, "browRange": 0, "jawDrop": jaw_drop,
        "eyeCenter": [round(sum((b[0] + b[2]) / 2 for b in bs) / len(bs)),
                      round(sum((b[1] + b[3]) / 2 for b in bs) / len(bs))],
        # 눈 반높이 — 워프 앵커·폴백용. 눈꺼풀 압축은 스프라이트의 잉크 bbox 를 직접 쓴다.
        "eyeHalf": round(sum((b[3] - b[1]) / 2 for b in bs) / len(bs)),
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
