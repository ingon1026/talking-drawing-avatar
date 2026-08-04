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
INK_MAX = 300   # r+g+b 합이 이 미만이면 '획'. avatar_core.js 의 INK_MAX 와 같은 값이어야
                # 빌더가 자른 상자와 렌더가 재는 프로파일이 같은 눈을 가리킨다.
DEFAULT_STYLE = {"line": "#2b2b2b", "fill": "#8a3535", "tongue": "#d97b7b",
                 "teeth": "#ffffff", "width": 26}


_SNAP_PAD, _SNAP_CAP, _SNAP_ITER = 4, 2.5, 8   # 눈 상자 스냅: 확장 여유 / 상한 배율 / 반복 상한
_INK_FRAC = 0.05                               # 선 색 표본: 가장 어두운 비율


def _median_rgb(pixels):
    """픽셀 목록의 채널별 중앙값 색."""
    return tuple(int(statistics.median(c[i] for c in pixels)) for i in range(3))


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
    return _median_rgb(samples)


def ink_color(img, box):
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
    keep = pxs[:max(1, int(len(pxs) * _INK_FRAC))]
    return _median_rgb(keep)


def snap_eye_box(img, box):
    """클릭 상자를 그 안팎 잉크(어두운 픽셀)에 맞춘다 — 작으면 넓히고 크면 조인다.

    사용자는 눈 *중심*만 클릭하고 크기는 지정하지 않는다. 반경이 '원본 최대변의 3%'
    고정이라 실제 눈과 무관해서, 작은 눈은 상자가 남아돌고(돼지 24px 상자에 점 11px)
    큰 눈은 상자가 모자라 눈 일부만 잘렸다 — 나머지가 base 에 남아 눈꺼풀이 움직여도
    화면이 안 변했다. 눈꺼풀 압축이 눈의 실제 하단을 피벗으로 쓰므로 상자가 눈에 맞아야 한다.

    자매 리포 drawface-live 의 expandBoxToInk(imageops.js:169) 와 같은 방식 —
    _SNAP_PAD 만큼 넓혀 잉크 bbox 를 다시 재기를 수렴할 때까지 반복하되, 눈썹·머리카락까지
    빨아들이지 않게 원래 상자의 _SNAP_CAP 배 안으로 제한한다. 자매 쪽은 상자를 키우기만
    하고 여기는 조이기도 한다 — 의도적 차이라 한쪽으로 덮어쓰지 말 것.
    """
    px = img.convert("RGB").load()
    w, h = img.size
    x0, y0, x1, y1 = (round(v) for v in box)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    mw, mh = max(24, (x1 - x0) * _SNAP_CAP), max(24, (y1 - y0) * _SNAP_CAP)
    bnd = (round(cx - mw / 2), round(cy - mh / 2), round(cx + mw / 2), round(cy + mh / 2))
    for _ in range(_SNAP_ITER):
        sx, sy = max(0, bnd[0], x0 - _SNAP_PAD), max(0, bnd[1], y0 - _SNAP_PAD)
        ex, ey = min(w - 1, bnd[2], x1 + _SNAP_PAD), min(h - 1, bnd[3], y1 + _SNAP_PAD)
        xs, ys = [], []
        for y in range(sy, ey + 1):
            for x in range(sx, ex + 1):
                if sum(px[x, y]) < INK_MAX:
                    xs.append(x)
                    ys.append(y)
        if not xs:
            return box
        nb = (min(xs), min(ys), max(xs) + 1, max(ys) + 1)
        if nb == (x0, y0, x1, y1):
            break
        x0, y0, x1, y1 = nb
    return _largest_blob_box(px, (x0, y0, x1, y1))


def _fill_skin(img, box, ring=6):
    """상자를 테두리의 **비잉크** 픽셀 중앙값으로 채운다.

    _border_median 은 잉크를 포함해서, 눈 옆에 머리카락·속눈썹이 있으면 그 색이 중앙값을
    끌어가 얼굴에 머리색 사각형이 남았다. 방향 보간(가로/세로)도 해봤지만 눈 주변이
    앞머리·속눈썹으로 둘러싸인 그림에서는 줄무늬·세로 띠로 번진다 — 눈 주변은 대체로
    균일한 살색이라 단색이면 충분하다.
    """
    x0, y0, x1, y1 = box
    px = img.load()
    w, h = img.size
    skin = []
    for x in range(max(0, x0 - ring), min(w, x1 + ring)):
        for y in list(range(max(0, y0 - ring), y0)) + list(range(y1, min(h, y1 + ring))):
            if sum(px[x, y]) >= INK_MAX:
                skin.append(px[x, y])
    for y in range(max(0, y0), min(h, y1)):
        for x in list(range(max(0, x0 - ring), x0)) + list(range(x1, min(w, x1 + ring))):
            if sum(px[x, y]) >= INK_MAX:
                skin.append(px[x, y])
    fill = _median_rgb(skin) if skin else _border_median(img, box)
    ImageDraw.Draw(img).rectangle((x0, y0, x1 - 1, y1 - 1), fill=fill)


def _largest_blob_box(px, box):
    """상자 안 잉크 중 최대 연결성분(8-이웃)의 bbox. 렌더의 _buildProfile 과 같은 필터다.

    확장이 눈 옆 머리카락·눈썹을 물면 그 큰 상자가 통째로 지워져 얼굴에 머리색 사각형이
    남는다. 눈에 붙어 있지 않은 잉크는 눈이 아니다.
    """
    x0, y0, x1, y1 = box
    ink = lambda x, y: sum(px[x, y]) < INK_MAX
    seen, best = set(), []
    for sy in range(y0, y1):
        for sx in range(x0, x1):
            if (sx, sy) in seen or not ink(sx, sy):
                continue
            stack, comp = [(sx, sy)], []
            seen.add((sx, sy))
            while stack:
                cx, cy = stack.pop()
                comp.append((cx, cy))
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        nx, ny = cx + dx, cy + dy
                        if x0 <= nx < x1 and y0 <= ny < y1 and (nx, ny) not in seen and ink(nx, ny):
                            seen.add((nx, ny))
                            stack.append((nx, ny))
            if len(comp) > len(best):
                best = comp
    if not best:
        return box
    xs = [c[0] for c in best]
    ys = [c[1] for c in best]
    return (min(xs), min(ys), max(xs) + 1, max(ys) + 1)


def split_lips(img, box):
    """입 상자를 입술 사이 선에서 위/아래로 가른다. 반환: 분리선 y(캔버스 좌표).

    다문 입 그림은 '윗입술 / 사이 선(가장 어두움) / 아랫입술' 구조다. 그 선을 경계로
    두 장으로 나눠 두면 아랫입술만 내려 입을 벌릴 수 있다 — 원본 입술 모양을 지키면서
    립싱크가 되는 유일한 방법이다. 구강을 통째로 얹으면 입술을 덮어버린다(실측).
    """
    x0, y0, x1, y1 = box
    px = img.convert("RGB").load()
    n = max(1, x1 - x0)
    rows = [(sum(sum(px[x, y]) for x in range(x0, x1)) / n, y) for y in range(y0 + 2, y1 - 2)]
    return min(rows)[1] if rows else (y0 + y1) // 2


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
    bg = _median_rgb(edge)
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

        _fill_skin(base, box)

    # 입술을 위/아래 두 장으로 가른다 — 아랫입술만 내려 벌리면 원본 모양이 지켜진다.
    # 벡터 입으로 대체하면 얼굴이 다른 사람이 되고, 원본을 그대로 두면 벌릴 틈이 없다.
    mb = tuple(map(int, (*T(mouth_box[0], mouth_box[1]), *T(mouth_box[2], mouth_box[3]))))
    lip_y = split_lips(base, mb)
    # 구강 색 — 입술 사이 선에서 뽑되 더 어둡게. 그대로 쓰면 입술과 같은 밝기라
    # 벌어진 게 아니라 입술이 두꺼워진 것처럼 보인다.
    mouth_ink = tuple(int(c * 0.78) for c in ink_color(base, mb))
    for tag, (sy, ey) in (("upper", (mb[1], lip_y)), ("lower", (lip_y, mb[3]))):
        lay = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
        lay.paste(base.crop((mb[0], sy, mb[2], ey)).convert("RGBA"), (mb[0], sy))
        lay.save(out / f"mouth_{tag}.png")
    _fill_skin(base, mb)                     # 원래 입 자리는 지운다(두 장이 대신 그린다)
    base.convert("RGBA").save(out / "base.png")

    mcx, mcy = T(*mouth_center)
    # 눈 기하는 manifest 에 안 남긴다 — 렌더가 스프라이트의 잉크 프로파일을 직접 재기
    # 때문에 소비자가 없다. 예전에 eyeCenter/eyeHalf 를 적었지만 눈꺼풀이 세로 스케일에서
    # 오클루전으로 바뀌면서 읽는 쪽이 사라졌고, 값만 남으면 다음 사람이 렌더에 영향을
    # 준다고 믿고 튜닝하게 된다.
    manifest = {
        "name": name,
        "pupilRange": 0, "browRange": 0, "jawDrop": jaw_drop,
        "mouthCenter": [round(mcx), round(mcy)],
        "lipSplit": lip_y, "mouthBox": list(mb),
        "proceduralMouth": False,
        "mouthStyle": {**DEFAULT_STYLE, "fill": "#%02x%02x%02x" % mouth_ink, **(mouth_style or {})},
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
