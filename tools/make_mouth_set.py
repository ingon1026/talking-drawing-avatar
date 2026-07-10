"""AI 편집(또는 재촬영) 이미지들 → 캐릭터 입모양 스프라이트 세트.

전제: 각 비짐 이미지는 원본 그림과 같은 구도(전체 이미지 편집본).
입력 폴더에 A.png E.png I.png O.png U.png M.png (일부 생략 가능, closed.png 선택).

사용: .venv/bin/python tools/make_mouth_set.py <char_id> <원본이미지> <비짐폴더> x0 y0 x1 y1
     (x0..y1 = 원본 픽셀 기준 입 영역 — 캐릭터 만들 때 쓴 mouth_box와 동일 값)
예:  .venv/bin/python tools/make_mouth_set.py pig test_1.png mouths_pig 162 126 222 153
완료 후 manifest가 스프라이트 모드(proceduralMouth: false)로 전환된다.
"""
import json
import sys
from pathlib import Path

from PIL import Image

CANVAS = 512
VISEMES = ["closed", "M", "A", "E", "I", "O", "U"]
PAD = 6  # 입 박스 여유 px (원본 기준)


def main(char_id, src_path, viseme_dir, x0, y0, x1, y1):
    root = Path(__file__).resolve().parent.parent
    out = root / "assets_characters" / char_id
    manifest_path = out / "manifest.json"
    manifest = json.loads(manifest_path.read_text())

    src = Image.open(src_path)
    s = min(CANVAS / src.width, CANVAS / src.height)
    ox, oy = (CANVAS - src.width * s) / 2, (CANVAS - src.height * s) / 2
    box = (max(0, x0 - PAD), max(0, y0 - PAD), min(src.width, x1 + PAD), min(src.height, y1 + PAD))

    made = []
    for v in VISEMES:
        f = Path(viseme_dir) / f"{v}.png"
        if not f.exists():
            continue
        img = Image.open(f).convert("RGB")
        if img.size != src.size:
            img = img.resize(src.size, Image.LANCZOS)
        patch = img.crop(box)
        cw, ch = round(patch.width * s), round(patch.height * s)
        sprite = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
        sprite.paste(patch.resize((cw, ch), Image.LANCZOS).convert("RGBA"),
                     (round(ox + box[0] * s), round(oy + box[1] * s)))
        sprite.save(out / f"mouth_{v}.png")
        made.append(v)

    missing = [v for v in VISEMES if v not in made]
    if "closed" in missing:
        # closed는 벡터 입이 그리던 다문 입 — base가 이미 지워져 있으므로 원본에서 크롭해 사용
        patch = Image.open(src_path).convert("RGB").crop(box)
        cw, ch = round(patch.width * s), round(patch.height * s)
        sprite = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
        sprite.paste(patch.resize((cw, ch), Image.LANCZOS).convert("RGBA"),
                     (round(ox + box[0] * s), round(oy + box[1] * s)))
        sprite.save(out / "mouth_closed.png")
        made.append("closed(원본)")
        missing.remove("closed")
    for v in missing:  # 빠진 비짐은 가장 가까운 것으로 대체 링크
        near = {"M": "closed", "E": "A", "I": "E", "U": "O", "O": "A", "A": "E"}[v]
        nf = out / f"mouth_{near}.png"
        if nf.exists():
            Image.open(nf).save(out / f"mouth_{v}.png")

    manifest["proceduralMouth"] = False
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"OK: {out} — 생성 {made}, 대체 {missing}, 스프라이트 모드 전환 완료")


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0], a[1], a[2], *map(int, a[3:7]))
