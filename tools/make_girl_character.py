"""소녀 캐릭터 에셋 생성기 — make_default_character(소년)와 같은 좌표계·규격, 스타일만 변형.

소년과 동일한 EYE_L/R·MOUTH_C 정합을 그대로 써서 렌더러 무수정으로 교체 가능.
차이: 긴 머리(옆·뒤), 앞머리 가르마, 속눈썹, 분홍 팔레트. 입은 proceduralMouth(벡터) 공유.

실행: .venv/bin/python tools/make_girl_character.py   → assets_characters/girl/
"""
import json
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = (512, 512)
OUT = Path(__file__).resolve().parent.parent / "assets_characters" / "girl"

SKIN = "#ffe0cf"
LINE = "#5a3a2e"
HAIR = "#6a4b34"
HAIR_HI = "#8a6a4c"
SHIRT = "#ff9fc0"
SHIRT_DARK = "#e87ea6"
MOUTH_IN = "#b5504a"
TONGUE = "#e79aa0"
IRIS = "#7a533a"

MANIFEST = {
    "name": "소녀",
    "persona": "너는 사용자가 그린 그림에서 태어난 밝고 다정한 아바타야. 호기심 많고 친구처럼 편하게 대화한다.",
    "pupilRange": 8,
    "browRange": 12,
    "jawDrop": 9,
    "mouthCenter": [256, 336],
    "proceduralMouth": True,
    # 코드로 그린 캐릭터라 base 에 입 자체가 없다 = 지운 것과 같다.
    # 이 키가 없으면 렌더가 벡터 입을 못 그려 입이 정지한다(로더가 console.error).
    "mouthErased": True,
    "mouthStyle": {"line": LINE, "fill": MOUTH_IN, "tongue": TONGUE, "teeth": "#ffffff", "width": 30},
}

EYE_L, EYE_R = (198, 258), (314, 258)
EYE_W, EYE_H = 62, 50
MOUTH_C = (256, 336)


def canvas():
    img = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def ellipse_at(d, cx, cy, w, h, **kw):
    d.ellipse((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), **kw)


def make_base():
    img, d = canvas()
    # 긴 뒷머리 (어깨까지 내려오는 큰 타원)
    d.ellipse((96, 120, 416, 470), fill=HAIR)
    # 옆머리 갈래 (얼굴 양옆을 감싸 어깨로)
    d.ellipse((100, 210, 190, 460), fill=HAIR)
    d.ellipse((322, 210, 412, 460), fill=HAIR)
    # 목·어깨·옷
    d.rectangle((230, 372, 282, 424), fill=SKIN, outline=LINE, width=4)
    d.rounded_rectangle((132, 404, 380, 540), 46, fill=SHIRT, outline=LINE, width=6)
    d.arc((216, 388, 296, 442), 200, 340, fill=SHIRT_DARK, width=8)
    # 얼굴
    d.ellipse((128, 104, 384, 396), fill=SKIN, outline=LINE, width=6)
    # 귀
    for cx in (132, 380):
        ellipse_at(d, cx, 268, 28, 42, fill=SKIN, outline=LINE, width=5)
    # 앞머리: 가운데 가르마 + 양갈래 뱅
    d.chord((116, 92, 396, 300), 180, 360, fill=HAIR)
    d.polygon([(256, 150), (206, 232), (256, 214)], fill=HAIR)   # 가르마 왼쪽 갈래
    d.polygon([(256, 150), (306, 232), (256, 214)], fill=HAIR)   # 오른쪽 갈래
    for cx, cy, w, h in ((176, 196, 78, 54), (336, 194, 76, 52)):
        ellipse_at(d, cx, cy, w, h, fill=HAIR)
    d.arc((170, 128, 342, 220), 205, 320, fill=HAIR_HI, width=9)  # 머릿결 하이라이트
    # 볼 홍조
    ellipse_at(d, 172, 306, 46, 26, fill=(246, 150, 160, 110))
    ellipse_at(d, 340, 306, 46, 26, fill=(246, 150, 160, 110))
    # 코
    d.arc((249, 292, 264, 308), 300, 120, fill=LINE, width=4)
    return img


def make_brow(cx):
    img, d = canvas()
    d.arc((cx - 28, 228, cx + 28, 252), 200, 340, fill=LINE, width=7)
    return img


def make_eye_open(c):
    img, d = canvas()
    ellipse_at(d, c[0], c[1], EYE_W, EYE_H, fill="white", outline=LINE, width=5)
    # 윗라인 굵게 + 바깥쪽 속눈썹 3가닥 (소녀 포인트)
    d.arc((c[0] - EYE_W // 2, c[1] - EYE_H // 2, c[0] + EYE_W // 2, c[1] + EYE_H // 2),
          195, 345, fill=LINE, width=8)
    ox = 1 if c[0] > 256 else -1   # 바깥쪽 방향
    bx = c[0] + ox * (EYE_W // 2 - 4)
    for dx, dy in ((0, 0), (ox * 8, 2), (ox * 14, 8)):
        d.line((bx, c[1] - EYE_H // 2 + 6, bx + ox * 10 + dx, c[1] - EYE_H // 2 - 6 + dy), fill=LINE, width=3)
    return img


def make_eye_closed(c):
    img, d = canvas()
    d.arc((c[0] - EYE_W // 2, c[1] - 6, c[0] + EYE_W // 2, c[1] + EYE_H // 2 + 10),
          25, 155, fill=LINE, width=7)
    ox = 1 if c[0] > 256 else -1
    d.arc((c[0] + ox * (EYE_W // 2) - 8, c[1] + 10, c[0] + ox * (EYE_W // 2) + 6, c[1] + 22),
          300, 60, fill=LINE, width=4)   # 속눈썹 한 가닥
    return img


def make_pupil(c):
    img, d = canvas()
    ellipse_at(d, c[0], c[1] + 2, 28, 28, fill=IRIS)          # 홍채 (소녀는 살짝 큼)
    ellipse_at(d, c[0], c[1] + 3, 14, 14, fill="#2a1c14")     # 동공
    ellipse_at(d, c[0] + 6, c[1] - 4, 9, 9, fill="white")     # 하이라이트
    ellipse_at(d, c[0] - 5, c[1] + 8, 5, 5, fill=(255, 255, 255, 150))
    return img


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    parts = {
        "base.png": make_base(),
        "brow_L.png": make_brow(EYE_L[0]),
        "brow_R.png": make_brow(EYE_R[0]),
        "eye_L_open.png": make_eye_open(EYE_L),
        "eye_R_open.png": make_eye_open(EYE_R),
        "eye_L_closed.png": make_eye_closed(EYE_L),
        "eye_R_closed.png": make_eye_closed(EYE_R),
        "pupil_L.png": make_pupil(EYE_L),
        "pupil_R.png": make_pupil(EYE_R),
    }
    for name, img in parts.items():
        img.save(OUT / name)
    (OUT / "manifest.json").write_text(json.dumps(MANIFEST, ensure_ascii=False, indent=2))

    for pv, eyes in (("preview.png", "open"), ("preview_blink.png", "closed")):
        comp = Image.new("RGBA", SIZE, "white")
        layers = ["base.png", "brow_L.png", "brow_R.png", f"eye_L_{eyes}.png", f"eye_R_{eyes}.png"]
        if eyes == "open":
            layers += ["pupil_L.png", "pupil_R.png"]
        for layer in layers:
            comp.alpha_composite(Image.open(OUT / layer))
        comp.save(OUT / pv)
    print(f"OK: {OUT} ({len(parts)} parts + manifest + previews)")


if __name__ == "__main__":
    main()
