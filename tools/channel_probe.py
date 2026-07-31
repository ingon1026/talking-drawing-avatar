"""발화 중 3D 헤드에 실제로 들어가는 채널값을 측정한다.

    PYTHONPATH= .venv/bin/python tools/channel_probe.py

서버가 http://127.0.0.1:8000 에 떠 있어야 한다. 수용 기준(jawOpen >= 0.7,
jawRight 평균 <= 0.02, browInnerUp 평균 <= 0.05) 확인용.

p10(하위 10백분위)도 함께 출력한다. shapeAnim 의 게인은 (peak - p10) * gain
으로 적용되므로, 각 엔진의 jawopen p10 이 얼마나 낮아야 0.7 목표를 넘는지
바로 검증할 수 있다.

p10/max/mean 은 두 모집단에서 각각 구해 나란히 출력한다:
  - "렌더" 열: smoothStep 을 가로채 렌더 루프가 실제로 적용한 프레임(오디오 재생 중,
    rAF 로 샘플링됨) — 화면에 보이는 값.
  - "anim" 열: weightsFromAnim 을 가로채 anim.frames 원본 전체 — shapeAnim(Task 7 예정)
    이 base(p10)를 계산할 때 쓰는 실제 모집단. 재생 중 rAF 샘플과 달리 무음 패딩·재생
    전후 프레임까지 포함되어 p10 이 더 낮게 나올 수 있다.
"""
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

CHROME = Path("/home/ingon/.cache/ms-playwright/chromium_headless_shell-1234"
              "/chrome-headless-shell-linux64/chrome-headless-shell")
TEXT = "안녕하세요. 오늘 날씨가 정말 좋네요. 같이 산책이라도 갈까요?"
WATCH = ("jawopen", "jawright", "jawleft", "mouthfunnel", "browinnerup")
# static/avatar_core.js SHAPE 의 jawopen 게인과 동일 — (peak - p10) * gain 검산용
GAIN = {"a2f": 1.9, "neurosync": 2.4}


async def probe(engine: str) -> dict:
    async with async_playwright() as p:
        kw = {"executable_path": str(CHROME)} if CHROME.exists() else {}
        browser = await p.chromium.launch(
            args=["--autoplay-policy=no-user-gesture-required"], **kw)
        page = await browser.new_page()
        await page.goto("http://127.0.0.1:8000/3d")
        await page.wait_for_function(
            "!document.getElementById('status').textContent.includes('로딩')", timeout=90000)
        await page.select_option("#engine", engine)
        # 렌더 루프가 매 프레임 넘기는 가중치(rAF 재생 중 샘플)와, shapeAnim 이 실제로 볼
        # anim.frames 원본(전체 프레임, base=p10 계산 모집단)을 둘 다 가로챈다
        await page.evaluate("""() => {
          window.__w = [];
          const origStep = AvatarCore.smoothStep;
          AvatarCore.smoothStep = (s, w) => { if (w) window.__w.push(w); return origStep(s, w); };
          const origAnim = AvatarCore.weightsFromAnim;
          AvatarCore.weightsFromAnim = (a, au) => { if (a && a.frames) window.__anim = a; return origAnim(a, au); };
        }""")
        await page.fill("#text", TEXT)
        await page.click("#send")
        await page.wait_for_function(
            "() => { const a = document.getElementById('audio');"
            "        return a.duration > 0 && a.ended; }", timeout=90000)
        stats = await page.evaluate("""() => {
          function summarize(rows) {
            const out = {};
            const keys = new Set(); rows.forEach(w => Object.keys(w).forEach(k => keys.add(k)));
            for (const k of keys) {
              const v = rows.map(w => +w[k] || 0);
              const sorted = v.slice().sort((a, b) => a - b);
              const p10 = sorted[Math.floor((sorted.length - 1) * 10 / 100)];
              out[k] = { max: Math.max(...v), mean: v.reduce((a, b) => a + b, 0) / v.length, p10 };
            }
            return out;
          }
          const rendered = summarize(window.__w.filter(w => Object.keys(w).length));
          let animPop = {};
          if (window.__anim && window.__anim.frames && window.__anim.index) {
            const rows = window.__anim.frames.map(f => {
              const w = {};
              for (const [name, col] of window.__anim.index) w[name] = f[col];
              return w;
            });
            animPop = summarize(rows);
          }
          return { rendered, animPop };
        }""")
        await browser.close()
        return stats


async def main():
    for engine in ("a2f", "neurosync"):
        s = await probe(engine)
        rendered, anim_pop = s["rendered"], s["animPop"]
        print(f"\n===== {engine} =====")
        print("  [렌더 — 재생 중 rAF 샘플, 화면에 보이는 값]")
        for k in WATCH:
            if k in rendered:
                r = rendered[k]
                print(f"    {k:14s} max={r['max']:.3f}  mean={r['mean']:.3f}  p10={r['p10']:.3f}")
        print("  [anim — anim.frames 원본, shapeAnim 이 base 계산에 쓰는 모집단]")
        for k in WATCH:
            if k in anim_pop:
                a = anim_pop[k]
                print(f"    {k:14s} max={a['max']:.3f}  mean={a['mean']:.3f}  p10={a['p10']:.3f}")
        # shapeAnim 은 anim.frames 전체에서 base(p10)를 구하므로 검산은 anim 열 기준
        if "jawopen" in anim_pop:
            peak, p10 = anim_pop["jawopen"]["max"], anim_pop["jawopen"]["p10"]
            gain = GAIN[engine]
            shaped = (peak - p10) * gain
            verdict = "PASS" if shaped >= 0.7 else "FAIL"
            print(f"  --> shapeAnim jawopen(anim 기준) = ({peak:.3f} - {p10:.3f}) * {gain} = {shaped:.3f}  [{verdict} >= 0.7]")


asyncio.run(main())
