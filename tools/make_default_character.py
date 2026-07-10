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

SKIN = "#ffdfc4"
LINE = "#3a2e2a"
HAIR = "#5a4033"
SHIRT = "#7fa8ff"
MOUTH_IN = "#8a3535"
TONGUE = "#d97b7b"

MANIFEST = {
    "name": "기본 캐릭터",
    "size": SIZE,
    "pupilRange": 7,   # 시선에 따른 눈동자 이동 px
    "browRange": 10,   # 눈썹 올림 최대 px
    "jawDrop": 8,      # JawOpen에 따른 입 전체 하강 px
    "mouthCenter": [256, 340],  # 입 기준점
    "proceduralMouth": True,    # True = 근육 채널로 입 윤곽을 직접 그림 (mouth_*.png 무시)
    "mouthStyle": {"line": LINE, "fill": MOUTH_IN, "tongue": TONGUE, "teeth": "#ffffff", "width": 34},
}

# 좌표 기준점
EYE_L, EYE_R = (200, 260), (312, 260)
EYE_W, EYE_H = 56, 44
MOUTH_C = (256, 340)


def canvas():
    img = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def ellipse_at(d, cx, cy, w, h, **kw):
    d.ellipse((cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), **kw)


def make_base():
    img, d = canvas()
    d.rectangle((232, 370, 280, 420), fill=SKIN, outline=LINE, width=4)  # 목
    d.rounded_rectangle((140, 402, 372, 540), 40, fill=SHIRT, outline=LINE, width=6)  # 몸
    d.ellipse((126, 100, 386, 400), fill=SKIN, outline=LINE, width=6)  # 얼굴
    d.chord((118, 92, 394, 330), 180, 360, fill=HAIR)  # 머리 윗부분
    for cx in (170, 256, 342):  # 앞머리 스캘럽
        ellipse_at(d, cx, 214, 74, 60, fill=HAIR)
    ellipse_at(d, 168, 305, 42, 22, fill=(240, 150, 150, 110))  # 볼
    ellipse_at(d, 344, 305, 42, 22, fill=(240, 150, 150, 110))
    d.arc((249, 292, 263, 306), 300, 120, fill=LINE, width=4)  # 코
    return img


def make_brow(cx):
    img, d = canvas()
    d.rounded_rectangle((cx - 26, 208, cx + 26, 218), 5, fill=LINE)
    return img


def make_eye_open(c):
    img, d = canvas()
    ellipse_at(d, c[0], c[1], EYE_W, EYE_H, fill="white", outline=LINE, width=5)
    return img


def make_eye_closed(c):
    img, d = canvas()
    d.arc((c[0] - EYE_W // 2, c[1] - 8, c[0] + EYE_W // 2, c[1] + EYE_H // 2 + 6),
          20, 160, fill=LINE, width=6)
    return img


def make_pupil(c):
    img, d = canvas()
    ellipse_at(d, c[0], c[1] + 2, 22, 22, fill="#2b1d16")
    ellipse_at(d, c[0] + 4, c[1] - 3, 7, 7, fill="white")
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
