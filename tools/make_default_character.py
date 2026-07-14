"""기본 캐릭터 에셋 생성기 — 캐릭터 에셋 규격의 레퍼런스.

에셋 규격 (assets_characters/<이름>/ 폴더, 모든 PNG는 같은 캔버스 크기·좌표계, 투명 배경):
  manifest.json     튜닝 값 (아래 MANIFEST 참조)
  base.png          몸/머리/코/볼 등 움직이지 않는 전부 (눈·눈썹·입 제외)
  brow_L.png, brow_R.png            눈썹 (렌더러가 위로 이동시킴)
  eye_L_open.png, eye_R_open.png    뜬 눈 (흰자+테두리)
  eye_L_closed.png, eye_R_closed.png  감은 눈
  pupil_L.png, pupil_R.png          눈동자 (렌더러가 시선 방향으로 이동, 없으면 생략됨)
  mouth_closed.png, mouth_M.png, mouth_A.png, mouth_E.png,
  mouth_I.png, mouth_O.png, mouth_U.png   입모양(비짐) 세트

자기 그림으로 캐릭터를 만들려면: 같은 파일명으로 파츠 PNG를 잘라 넣으면 끝.
실행: .venv/bin/python tools/make_default_character.py
"""
import json
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = (512, 512)
OUT = Path(__file__).resolve().parent.parent / "assets_characters" / "default"

SKIN = "#ffe3c9"
LINE = "#4a3626"
HAIR = "#4a3728"
HAIR_HI = "#6a523c"
SHIRT = "#6d9eff"
SHIRT_DARK = "#5b87e0"
MOUTH_IN = "#a2453b"
TONGUE = "#e08a8a"
IRIS = "#6b4a33"

MANIFEST = {
    "name": "기본 캐릭터",
    "pupilRange": 8,   # 시선에 따른 눈동자 이동 px
    "browRange": 12,   # 눈썹 올림 최대 px
    "jawDrop": 9,      # JawOpen에 따른 입 전체 하강 px
    "mouthCenter": [256, 336],  # 입 기준점
    "proceduralMouth": True,    # True = 근육 채널로 입 윤곽을 직접 그림 (mouth_*.png 무시)
    "mouthStyle": {"line": LINE, "fill": MOUTH_IN, "tongue": TONGUE, "teeth": "#ffffff", "width": 30},
}

# 좌표 기준점
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
    # 뒷머리 (얼굴 뒤 레이어)
    ellipse_at(d, 256, 226, 296, 280, fill=HAIR)
    # 목·어깨
    d.rectangle((230, 372, 282, 424), fill=SKIN, outline=LINE, width=4)
    d.rounded_rectangle((132, 404, 380, 540), 46, fill=SHIRT, outline=LINE, width=6)
    d.arc((216, 388, 296, 442), 200, 340, fill=SHIRT_DARK, width=8)  # 옷깃
    # 얼굴 (턱이 살짝 갸름한 타원)
    d.ellipse((128, 104, 384, 396), fill=SKIN, outline=LINE, width=6)
    # 귀
    for cx in (132, 380):
        ellipse_at(d, cx, 268, 30, 44, fill=SKIN, outline=LINE, width=5)
    # 앞머리: 둥근 갈래 + 하이라이트 (눈썹 위에서 끝나게)
    d.chord((120, 96, 392, 290), 180, 360, fill=HAIR)
    for cx, cy, w, h in ((168, 186, 74, 46), (232, 190, 78, 48), (300, 188, 76, 46), (352, 180, 62, 42)):
        ellipse_at(d, cx, cy, w, h, fill=HAIR)
    d.arc((168, 126, 330, 212), 205, 320, fill=HAIR_HI, width=10)  # 머릿결 하이라이트
    # 볼 홍조
    ellipse_at(d, 172, 306, 44, 24, fill=(244, 148, 138, 96))
    ellipse_at(d, 340, 306, 44, 24, fill=(244, 148, 138, 96))
    # 코
    d.arc((249, 292, 264, 308), 300, 120, fill=LINE, width=4)
    return img


def make_brow(cx):
    img, d = canvas()
    d.arc((cx - 30, 226, cx + 30, 254), 200, 340, fill=LINE, width=8)
    return img


def make_eye_open(c):
    img, d = canvas()
    ellipse_at(d, c[0], c[1], EYE_W, EYE_H, fill="white", outline=LINE, width=5)
    d.arc((c[0] - EYE_W // 2, c[1] - EYE_H // 2, c[0] + EYE_W // 2, c[1] + EYE_H // 2),
          200, 340, fill=LINE, width=7)  # 윗라인 강조 (속눈썹 느낌)
    return img


def make_eye_closed(c):
    img, d = canvas()
    d.arc((c[0] - EYE_W // 2, c[1] - 6, c[0] + EYE_W // 2, c[1] + EYE_H // 2 + 10),
          25, 155, fill=LINE, width=7)
    d.arc((c[0] + EYE_W // 2 - 8, c[1] + 10, c[0] + EYE_W // 2 + 6, c[1] + 22), 300, 60,
          fill=LINE, width=4)  # 속눈썹 한 가닥
    return img


def make_pupil(c):
    img, d = canvas()
    ellipse_at(d, c[0], c[1] + 2, 26, 26, fill=IRIS)          # 홍채
    ellipse_at(d, c[0], c[1] + 3, 13, 13, fill="#241a12")     # 동공
    ellipse_at(d, c[0] + 6, c[1] - 4, 8, 8, fill="white")     # 하이라이트
    ellipse_at(d, c[0] - 5, c[1] + 8, 5, 5, fill=(255, 255, 255, 150))
    return img


def make_mouth(kind):
    img, d = canvas()
    cx, cy = MOUTH_C
    if kind == "closed":
        d.arc((cx - 30, cy - 18, cx + 30, cy + 12), 20, 160, fill=LINE, width=5)
    elif kind == "M":
        d.rounded_rectangle((cx - 26, cy - 3, cx + 26, cy + 3), 3, fill=LINE)
    elif kind == "A":
        d.ellipse((cx - 34, cy - 25, cx + 34, cy + 28), fill=MOUTH_IN, outline=LINE, width=5)
        d.ellipse((cx - 20, cy + 8, cx + 20, cy + 26), fill=TONGUE)
    elif kind == "E":
        d.rounded_rectangle((cx - 34, cy - 15, cx + 34, cy + 17), 14, fill=MOUTH_IN, outline=LINE, width=5)
    elif kind == "I":
        d.rounded_rectangle((cx - 38, cy - 10, cx + 38, cy + 10), 10, fill=MOUTH_IN, outline=LINE, width=5)
    elif kind == "O":
        d.ellipse((cx - 22, cy - 22, cx + 22, cy + 22), fill=MOUTH_IN, outline=LINE, width=5)
    elif kind == "U":
        d.ellipse((cx - 16, cy - 16, cx + 16, cy + 16), fill=MOUTH_IN, outline=LINE, width=5)
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
    for kind in ("closed", "M", "A", "E", "I", "O", "U"):
        parts[f"mouth_{kind}.png"] = make_mouth(kind)
    for name, img in parts.items():
        img.save(OUT / name)
    (OUT / "manifest.json").write_text(json.dumps(MANIFEST, ensure_ascii=False, indent=2))

    # 미리보기 2종 (규격 검증용): 평상시 / 깜빡임+아 발음
    for pv, eyes, mouth in (("preview.png", "open", "closed"), ("preview_blink_A.png", "closed", "A")):
        comp = Image.new("RGBA", SIZE, "white")
        for layer in ("base.png", "brow_L.png", "brow_R.png",
                      f"eye_L_{eyes}.png", f"eye_R_{eyes}.png",
                      *(["pupil_L.png", "pupil_R.png"] if eyes == "open" else []),
                      f"mouth_{mouth}.png"):
            comp.alpha_composite(Image.open(OUT / layer))
        comp.save(OUT / pv)
    print(f"OK: {OUT} ({len(parts)} parts + manifest + previews)")


if __name__ == "__main__":
    main()
