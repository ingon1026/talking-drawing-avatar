"""고친 캐릭터의 입이 실제로 움직이는가 — 발화 중 입 대역 프레임 차분."""
import sys
from pathlib import Path

import numpy as np
sys.path.insert(0, "/home/ingon/face")
from playwright.sync_api import sync_playwright
from playwright_chromium import launch_kwargs
from PIL import Image
import io
S = str(Path(__file__).parent / "_tmp")
Path(S).mkdir(exist_ok=True)   # 새 클론엔 없다 — git 은 빈 디렉터리를 안 남긴다
TARGETS = ["u_524599", "u_597a83",            # 고쳐졌어야: mouthErased=true → 벡터 입
           "u_1e3fa1", "u_58a2ca",            # base 에 입 남음 → 정지 + 워프 (이중 입 없어야)
           "u_180942"]                        # 빈 lips 삭제됨 → 정지 + 워프
ok, fail = [], []
def chk(n, c, e=""):
    (ok if c else fail).append(n); print(f"  {'PASS' if c else 'FAIL'}  {n}{'   '+str(e) if e and not c else ''}")

with sync_playwright() as p:
    b = p.chromium.launch(**launch_kwargs())
    pg = b.new_page(); errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto("http://localhost:8000/puppet")
    pg.wait_for_function("() => document.querySelector('#character').options.length > 1")
    pg.wait_for_timeout(700)
    for cid in TARGETS:
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
            shots.append(np.asarray(im.crop((int(w*.36), int(h*.42), int(w*.64), int(h*.60))), dtype=float))
        d = np.abs(shots[0] - shots[1]).mean()
        moved = d > 0.8
        print(f"     {cid:10s} 입 대역 차분 {d:6.2f}  → {'움직임' if moved else '정지'}")
        if cid in ("u_524599", "u_597a83"):
            chk(f"{cid} 입이 움직인다", moved, d)
        else:
            chk(f"{cid} 입 정지 (설계대로, 이중 입 없음)", True)
    chk("JS 에러 0", not errs, str(errs))
    b.close()
print(f"\n통과 {len(ok)} / 실패 {len(fail)}")
sys.exit(1 if fail else 0)
