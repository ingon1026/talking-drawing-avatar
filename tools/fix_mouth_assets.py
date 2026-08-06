"""기존 캐릭터의 입 자산·manifest 를 지금 렌더가 기대하는 모습으로 한 번에 맞춘다.

    PYTHONPATH= .venv/bin/python tools/fix_mouth_assets.py            # 판정만 (기본, 안 고침)
    PYTHONPATH= .venv/bin/python tools/fix_mouth_assets.py --apply    # 실제로 쓴다
    PYTHONPATH= .venv/bin/python tools/fix_mouth_assets.py --only u_597a83

입 렌더는 세 갈래(벡터 입 / `mouth_lips` / `mouth_A~U`)를 다 빠져나가면 아무것도 안 그린다.
그래서 폴백이 생겼는데, 폴백이 벡터 입을 그려도 되는지는 **base 에 원본 입이 남아 있는지**에
달려 있다 — 남아 있는데 그리면 입이 두 개로 겹친다. 렌더는 자산만 봐서는 그걸 알 수 없어서
`manifest.mouthErased` 로 받는다(`puppet.html` 의 폴백 조건). 빌더는 이제 그 값을 적지만
기존 캐릭터에는 없다. **이 도구는 그 과거분을 한 번 메우는 일회성 마이그레이션이다.**

`mouthErased` 는 추측하지 않고 잰다 — `source.png` 를 빌더와 같은 방식으로 512 캔버스에
앉히면 지우기 전 base 가 결정적으로 재현되므로, 입 주변 창에서 지금 base 와 다른 픽셀을
세면 된다. 실측에서 두 무리가 확실히 갈렸다(안 지워진 쪽 0~22px, 지워진 쪽 342px 이상).
`source.png` 가 없는 내장 4개(default/girl/pig/stick)는 합성 그림이라 base 에 입이 아예
없다(눈으로 확인) — 잴 것 없이 true 다.

같이 치우는 것:

* **죽은 `mouth_upper.png`/`mouth_lower.png` 와 `lipSplit`** — 상·하 분리 렌더러는 커밋
  e19060a 에서 사라졌고 지금 리포에 읽는 코드가 한 줄도 없다(전체 grep 0건).
* **빈 `mouth_lips.png`** — 빌더의 `_ink_mask` 가 `INK_MAX=300`(거의 검정) 기준이라 연한
  살구·분홍 입술은 마스크가 통째로 비어 512² 가 전부 투명한 장이 저장됐다. `drawSplitLips`
  가 그 빈 장을 truthy 로 통과시켜 입이 오류도 경고도 없이 멎었다. 빌더는 이제 안 만들고,
  기존 것은 여기서 지운다 — `mouthErased: false` 가 폴백을 막으므로 화면은 그대로다
  (base 의 원본 입이 계속 보인다).
* **입 없는 preview 썸네일** — 빌더는 preview 를 base+눈으로만 합성한다. base 에서 입이
  지워진 캐릭터는 캐릭터 고르는 화면에 입 없는 얼굴이 떠 있다. 죽은 두 장이 지우기 전
  입 상자를 그대로 담고 있어(`base.crop`, 커밋 0149d02) 그걸 얹어 다시 합성한다.
* **`mouthStyle.line`** — 벡터 입이 획을 이 색으로 긋는다. 그 시절 빌더가 `fill` 만 그림에서
  뽑고 `line` 은 `DEFAULT_STYLE` 의 거의 검정 `#2b2b2b` 를 그대로 뒀다. 폴백이 실제로 벡터
  입을 그리게 된 캐릭터만 그림에서 실측한 색으로 고친다.

**`base.png` 와 `source.png` 는 안 고친다.** 지워진 입을 base 에 되돌리는 것도 재 봤지만
기각했다 — 폴백이 이미 그 자리에 벡터 입을 그려 주고 있어서, 되돌리면 오히려 이중 입이 된다.
`--apply` 로 지워지는 두 장의 픽셀은 `source.png` 의 결정적 변환으로 언제든 되살아난다.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from character_builder import CANVAS, _median_rgb, ink_color  # noqa: E402

CHARS = ROOT / "assets_characters"
DEAD = ("mouth_upper.png", "mouth_lower.png")   # 지울 파일은 이름을 박아 둔다 (source.png 근처에서 글롭 금지)
CHANNEL_EPS = 24    # 픽셀 하나가 '눈에 띄게 다르다'고 볼 채널 합 차이
WIN = (50, 35)      # mouthCenter 기준 반창 — A세대는 mouthBox 가 없어서 창으로 잰다
ERASED_PX = 100     # 창 안에서 이만큼 넘게 다르면 '지워졌다' (실측 두 무리: ≤22 vs ≥342)
LINE_TOL = 90       # 선 색 채널 합 거리 — 이 이상 어긋날 때만 고친다 (미세한 차이로 파일을 흔들지 않게)


def _hex_rgb(s):
    s = s.lstrip("#")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def _canvas_source(path):
    """source.png 를 빌더와 같은 방식으로 512 캔버스에 앉힌다 = 지우기 전 base."""
    s = Image.open(path).convert("RGB")
    k = min(CANVAS / s.width, CANVAS / s.height)
    w, h = int(s.width * k), int(s.height * k)
    edge = [s.getpixel((x, y)) for x in range(s.width) for y in (0, 1, s.height - 2, s.height - 1)] \
         + [s.getpixel((x, y)) for y in range(s.height) for x in (0, 1, s.width - 2, s.width - 1)]
    im = Image.new("RGB", (CANVAS, CANVAS), _median_rgb(edge))
    im.paste(s.resize((w, h), Image.LANCZOS), ((CANVAS - w) // 2, (CANVAS - h) // 2))
    return im


def _diff_px(a, b, box):
    """box 안에서 눈에 띄게 다른 픽셀 수."""
    pa, pb = a.convert("RGB").load(), b.convert("RGB").load()
    return sum(1 for y in range(box[1], box[3]) for x in range(box[0], box[2])
               if sum(abs(u - v) for u, v in zip(pa[x, y], pb[x, y])) > CHANNEL_EPS)


def _replace(path, save):
    """같은 디렉터리 임시 파일에 쓰고 os.replace 로 원자적 교체."""
    tmp = path.with_name(path.name + ".tmp")
    save(tmp)
    os.replace(tmp, path)


def _write_manifest(path, manifest, keep_newline):
    """쓰기 형식은 character_builder.py 가 이 파일들을 만들 때 쓴 것과 같다
    (ensure_ascii=False, indent=2) — 안 맞추면 한글 이름이 \\uXXXX 로 바뀌면서
    바꾸지도 않은 줄이 전부 diff 에 뜬다. 끝 개행은 파일마다 달라서 원본을 따라간다.
    """
    text = json.dumps(manifest, ensure_ascii=False, indent=2) + ("\n" if keep_newline else "")
    _replace(path, lambda p: p.write_text(text, encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="실제로 쓴다 (기본은 판정만)")
    ap.add_argument("--only", metavar="ID", help="이 캐릭터 하나만 (디버깅용)")
    a = ap.parse_args()

    dirs = [d for d in sorted(CHARS.iterdir()) if (d / "manifest.json").exists()]
    if a.only:
        dirs = [d for d in dirs if d.name == a.only]
        if not dirs:
            sys.exit(f"그런 캐릭터가 없다: {a.only}")

    warnings, n_changed = [], 0
    print(f"{'id':10}  {'창diff':>7}  {'지움':>4}  조치")
    for d in dirs:
        mf_path = d / "manifest.json"
        raw = mf_path.read_text(encoding="utf-8")
        manifest = json.loads(raw)
        base = Image.open(d / "base.png").convert("RGBA")
        src = d / "source.png"

        if src.exists():
            orig = _canvas_source(src)
            mcx, mcy = manifest["mouthCenter"]
            win = (max(0, mcx - WIN[0]), max(0, mcy - WIN[1]),
                   min(CANVAS, mcx + WIN[0]), min(CANVAS, mcy + WIN[1]))
            npx = _diff_px(base, orig, win)
            erased = npx > ERASED_PX
            shown = str(npx)
        else:
            # 내장 4개는 tools/make_*.py 가 그린 합성 그림이라 base 에 입이 아예 없다.
            orig, erased, shown = None, True, "내장"

        acts = []
        if manifest.get("mouthErased") != erased:
            acts.append(f"mouthErased={str(erased).lower()}")
        if "lipSplit" in manifest:
            acts.append("lipSplit 제거")
        has_dead = all((d / f).exists() for f in DEAD)
        if has_dead:
            acts.append("죽은 두 장 삭제")

        # 빈 mouth_lips.png — 512² 전부 투명이면 아무것도 안 그리면서 폴백만 막는다
        lips_path = d / "mouth_lips.png"
        empty_lips = lips_path.exists() and \
            Image.open(lips_path).convert("RGBA").getchannel("A").getbbox() is None
        if empty_lips:
            acts.append("빈 mouth_lips 삭제")

        # 벡터 입이 실제로 그려지는 캐릭터만 선 색을 실측값으로 고친다
        reline, line = False, None
        if has_dead and erased and manifest.get("mouthBox"):
            line = "#%02x%02x%02x" % ink_color(orig, tuple(manifest["mouthBox"]))
            cur = manifest["mouthStyle"].get("line", "")
            reline = bool(cur) and sum(abs(u - v) for u, v in
                                       zip(_hex_rgb(line), _hex_rgb(cur))) > LINE_TOL
            if reline:
                acts.append(f"mouthStyle.line {cur} → {line}")

        # preview 에 입이 빠진 캐릭터 — 죽은 두 장이 지우기 전 입을 담고 있어 되살릴 수 있다
        restored = None
        if has_dead and erased:
            lips = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
            for f in DEAD:
                lips.alpha_composite(Image.open(d / f).convert("RGBA"))
            cand = base.copy()
            cand.alpha_composite(lips)
            box = tuple(manifest["mouthBox"])
            # 두 장이 정말 지우기 전 원본인지, 얹으면 원본이 되는지 확인하고서만 쓴다
            if _diff_px(lips, orig, box) == 0 and _diff_px(cand, orig, box) == 0:
                restored = cand
                acts.append("preview 재합성(입 없는 썸네일)")

        if manifest.get("proceduralMouth") and not erased:
            warnings.append(f"  {d.name}: proceduralMouth=true 인데 base 에 원본 입이 남아 있다 "
                            f"(창diff {shown}px) — 벡터 입이 원본 입 위에 겹쳐 그려진다")

        n_changed += bool(acts)
        print(f"{d.name:10}  {shown:>7}  {('예' if erased else '아니오'):>4}  "
              + (("적용: " if a.apply else "필요: ") + ", ".join(acts) if acts else "변경 없음"))
        if not acts or not a.apply:
            continue

        if restored is not None:
            # 임시 파일 이름이 .png.tmp 라 PIL 이 형식을 못 읽는다 — 명시해서 넘긴다
            for pv, state in (("preview.png", "open"), ("preview_blink.png", "closed")):
                comp = restored.copy()
                for side in ("L", "R"):
                    comp.alpha_composite(Image.open(d / f"eye_{side}_{state}.png"))
                _replace(d / pv, lambda p, c=comp: c.save(p, "PNG"))
        manifest.pop("lipSplit", None)
        manifest["mouthErased"] = erased
        if reline:
            manifest["mouthStyle"]["line"] = line
        _write_manifest(mf_path, manifest, raw.endswith("\n"))
        for f in DEAD if has_dead else ():
            (d / f).unlink()
        if empty_lips:
            lips_path.unlink()

    if warnings:
        print("\n★ 자산과 manifest 가 어긋난 캐릭터 — 이 도구는 안 고친다(렌더 동작이 바뀐다):")
        print("\n".join(warnings))
    print(f"\n변경 필요 {n_changed}개 / 전체 {len(dirs)}개"
          + ("" if a.apply else "  — dry-run 이라 아무것도 안 고쳤다. 쓰려면 --apply"))


if __name__ == "__main__":
    main()
