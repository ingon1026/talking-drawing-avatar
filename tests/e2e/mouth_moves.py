"""고친 캐릭터의 입이 실제로 움직이는가 — 발화 중 입 대역 프레임 차분."""
import io
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

from _harness import Checks, open_puppet   # noqa: E402 — sys.path 삽입을 겸한다
# 예전엔 캐릭터 5개를 손으로 박아뒀는데 중복 정리로 4개가 사라져 하네스가 통째로 죽었다.
# 있는 캐릭터를 전부 훑고, **입이 움직여야 하는지를 manifest 에서 계산해** 기대값을 만든다.
# 게이트는 로더(puppet.html)와 같은 ?? 규칙이어야 한다 — 다르면 하네스가 통과해도 화면은 빈 입이다.
CHARS = Path(__file__).resolve().parent.parent.parent / "assets_characters"
TARGETS = []
for _d in sorted(CHARS.iterdir()):
    _m = json.loads((_d / "manifest.json").read_text())
    _gate = _m.get("mouthErased")
    if _gate is None:
        _gate = _m.get("proceduralMouth")
    _sprite = (_d / "mouth_A.png").exists()
    # ROI 는 manifest 의 입 좌표(512 캔버스 기준)에서 잡는다. 예전엔 화면의 36~64% × 42~60%
    # 로 박아뒀는데 **얼굴 클로즈업을 가정한 값**이었다 — 돼지·졸라맨은 전신 그림이라 그
    # 상자가 몸통에 떨어져서, 입이 멀쩡히 벌어지는데도 "정지" 로 읽혔다.
    _mc = _m.get("mouthCenter") or [256, 256]
    TARGETS.append((_d.name, bool(_gate) or _sprite, _mc))   # True = 입이 움직여야 한다
chk = Checks()

with sync_playwright() as p:
    b, pg, errs = open_puppet(p)
    for cid, should_move, mc in TARGETS:
        pg.select_option("#character", cid); pg.wait_for_timeout(1200)
        pg.click("#modeLive"); pg.wait_for_timeout(500)
        # 입만 강제로 벌렸다 닫으며 픽셀이 바뀌는지 본다 (TTS 없이 결정적으로)
        shots = []
        for jaw in (0.0, 0.8):
            # 한 번만 대입하면 렌더 루프의 smoothStep 이 되당겨 측정이 흔들린다(4.66 → 1.06 실측).
            # rAF 마다 다시 박아 값을 고정한 뒤 찍는다.
            pg.evaluate("""(v) => {
                if (window.__pin) cancelAnimationFrame(window.__pin);
                const tick = () => { smooth['jawopen'] = v; window.__pin = requestAnimationFrame(tick); };
                tick();
            }""", jaw)
            pg.wait_for_timeout(500)
            png = pg.locator("#stage2d").screenshot()
            im = Image.open(io.BytesIO(png)).convert("L")
            w, h = im.size
            cx, cy = mc[0] / 512 * w, mc[1] / 512 * h      # manifest 는 512 캔버스 좌표계
            rx, ry = 0.14 * w, 0.11 * h                     # 벌어진 입이 다 들어갈 만큼
            shots.append(np.asarray(im.crop((int(cx - rx), int(cy - ry), int(cx + rx), int(cy + ry))),
                                    dtype=float))
        d = np.abs(shots[0] - shots[1]).mean()
        moved = d > 0.8
        print(f"     {cid:10s} 입 대역 차분 {d:6.2f}  → {'움직임' if moved else '정지'}"
              f"  (기대: {'움직임' if should_move else '정지 허용'})")
        if should_move:
            chk(f"{cid} 입이 움직인다", moved, d)
        else:
            # base 에 입이 남아 있는 설계 — 정지가 정상이다. 움직이면 이중 입이라 그것도 실패다.
            chk(f"{cid} 입 정지 (설계대로, 이중 입 없음)", not moved, d)
    chk("JS 에러 0", not errs, str(errs))
    b.close()
sys.exit(chk.report())
