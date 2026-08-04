"""기존 캐릭터의 감은 눈 색을 그 그림의 선 색으로 바꾼다.

    PYTHONPATH= .venv/bin/python tools/recolor_closed_eyes.py [--dry]

빌더가 감은 눈을 검정(#1a1a1a) 고정으로 그리던 시절의 캐릭터들이 대상. 연필 그림·갈색
선 캐릭터에서 그 획만 남의 것처럼 튄다. 원본 업로드 이미지는 생성 직후 지워지지만
eye_*_open.png 가 원본 눈 패치를 그대로 담고 있어 거기서 색을 뽑으면 된다.

**기하는 건드리지 않는다** — 기존 알파를 그대로 두고 RGB 만 갈아끼운다. 호를 새로 그리면
빌더 공식(두께 = 반지름 × 0.35)이 적용돼 default/girl 의 획이 11~15px 로 굵어진다.
그 둘은 빌더가 아니라 tools/make_*_character.py 가 다른 두께로 만든 캐릭터다.
"""
import argparse
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from character_builder import ink_color  # noqa: E402


def alpha_box(img):
    return img.split()[-1].getbbox()


def redraw(cdir: Path, dry: bool) -> str | None:
    mf_path = cdir / "manifest.json"
    if not mf_path.exists():
        return None
    mf = json.loads(mf_path.read_text(encoding="utf-8"))
    changed = []
    for side in ("L", "R"):
        op, cl = cdir / f"eye_{side}_open.png", cdir / f"eye_{side}_closed.png"
        if not (op.exists() and cl.exists()):
            return None
        open_img = Image.open(op).convert("RGBA")
        box = alpha_box(open_img)          # 알파 경계 = 빌더가 잘라낸 클릭 박스
        if not box:
            return None
        # 빌더는 base(눈이 아직 안 지워진 상태)에서 색을 뽑는다. 여기선 그 패치가 곧
        # eye_*_open 이므로 같은 픽셀을 보는 셈이다.
        color = ink_color(open_img, box)
        changed.append(f"{side}:rgb{color}")
        if dry:
            continue
        # 알파(=획 모양·두께)는 그대로 두고 RGB 세 채널만 갈아끼운다.
        closed = Image.open(cl).convert("RGBA")
        solid = [Image.new("L", closed.size, c) for c in color]
        Image.merge("RGBA", (*solid, closed.getchannel("A"))).save(cl)
    if not dry:
        for pv, state in (("preview.png", "open"), ("preview_blink.png", "closed")):
            if not (cdir / pv).exists():
                continue
            comp = Image.open(cdir / "base.png").convert("RGBA")
            for side in ("L", "R"):
                comp.alpha_composite(Image.open(cdir / f"eye_{side}_{state}.png"))
            comp.save(cdir / pv)
    return f"{mf.get('name', cdir.name)}  " + "  ".join(changed)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry", action="store_true", help="색만 출력하고 파일은 안 건드림")
    dry = ap.parse_args().dry
    for root in ("assets_characters", "docs/characters"):
        d = ROOT / root
        if not d.is_dir():
            continue
        for c in sorted(p for p in d.iterdir() if p.is_dir()):
            line = redraw(c, dry)
            print(f"  {root}/{c.name:12s} {line}" if line else f"  {root}/{c.name:12s} 건너뜀")


if __name__ == "__main__":
    main()
