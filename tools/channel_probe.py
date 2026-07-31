"""발화 중 3D 헤드에 실제로 들어가는 채널값을 측정한다.

    PYTHONPATH= .venv/bin/python tools/channel_probe.py

서버가 http://127.0.0.1:8000 에 떠 있어야 한다.

두 문장(모음·무음 분포가 다름)에 대해 매 엔진의 anim.frames 원본을 캡처하고,
그 자리에서 실제 AvatarCore.shapeAnim(운영 코드 그대로)을 돌려 채널별 shaped
결과를 얻는다 — (peak-p10)*gain 을 손으로 재계산하지 않고, 셰이핑이 실제로
적용될 코드 경로를 그대로 태워서 검증한다.

수용 기준(전부 10% 마진 포함해 판정 — 예: >=0.7 기준은 실측이 0.77 이상이어야
"마진 있음"):
  - a2f      jawopen 최댓값 >= 0.7
  - a2f      jawright/jawleft 평균 <= 0.02 (SHAPE.a2f.kill 로 0 처리됨 — 이유를 함께 표기)
  - neurosync jawopen 최댓값 >= 0.7
  - neurosync browinnerup/browouterupleft/browouterupright/eyewideleft/eyewideright
    평균 <= 0.05 ("상시 놀란 표정" 결함 — browInnerUp 만이 아니라 눈썹·눈 전체가 대상)
"""
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

CHROME = Path("/home/ingon/.cache/ms-playwright/chromium_headless_shell-1234"
              "/chrome-headless-shell-linux64/chrome-headless-shell")
# 문장 1: 원래 발화(자음·무음 섞임). 문장 2: 개모음 위주·무음 적음 — 다른 모음/무음
# 분포에서도 게인이 유효한지 교차 확인한다.
TEXTS = {
    "s1": "안녕하세요. 오늘 날씨가 정말 좋네요. 같이 산책이라도 갈까요?",
    "s2": "아, 오늘 하루 정말 길었어요. 아아, 배고파요.",
}
WATCH = ("jawopen", "jawright", "jawleft", "mouthfunnel",
         "browinnerup", "browouterupleft", "browouterupright",
         "eyewideleft", "eyewideright")
# mouthfunnel 은 참고용으로만 출력한다 — SHAPE.neurosync.gain 에 게인은 있지만
# 계획서에 수용 기준(target)이 정의되어 있지 않아 TARGETS 에는 넣지 않는다.
# (channel: (kind, target)) — kind="min"은 shaped 최댓값이 target 이상, "max"는 shaped
# 평균이 target 이하여야 통과. static/avatar_core.js 의 SHAPE 값을 바꿔도 이 표는 그대로
# 두고, 실제 shapeAnim 출력을 다시 돌려 재검증한다.
TARGETS = {
    "jawopen": ("min", 0.7),
    "jawright": ("max", 0.02),
    "jawleft": ("max", 0.02),
    "browinnerup": ("max", 0.05),
    "browouterupleft": ("max", 0.05),
    "browouterupright": ("max", 0.05),
    "eyewideleft": ("max", 0.05),
    "eyewideright": ("max", 0.05),
}
MARGIN = 1.10  # min 기준은 target*1.10 이상, max 기준은 target/1.10 이하여야 "마진 있음"
# static/avatar_core.js SHAPE.*.kill 과 동일 — 값이 0이라고 kill 로 단정하지 않고
# 실제 kill 목록에 있는 채널만 그 이유를 표기한다(엔진이 애초에 안 쓰는 채널과 구분).
KILL = {"a2f": {"jawright", "jawleft"}, "neurosync": set()}


async def probe(engine: str, text: str) -> dict:
    async with async_playwright() as p:
        kw = {"executable_path": str(CHROME)} if CHROME.exists() else {}
        browser = await p.chromium.launch(
            args=["--autoplay-policy=no-user-gesture-required"], **kw)
        page = await browser.new_page()
        await page.goto("http://127.0.0.1:8000/3d")
        await page.wait_for_function(
            "!document.getElementById('status').textContent.includes('로딩')", timeout=90000)
        await page.select_option("#engine", engine)
        # anim.frames 원본(shapeAnim 이 base=p10 을 계산하는 실제 모집단)을 가로챈다
        await page.evaluate("""() => {
          const orig = AvatarCore.weightsFromAnim;
          AvatarCore.weightsFromAnim = (a, au) => { if (a && a.frames) window.__anim = a; return orig(a, au); };
        }""")
        await page.fill("#text", text)
        await page.click("#send")
        await page.wait_for_function(
            "() => { const a = document.getElementById('audio');"
            "        return a.duration > 0 && a.ended; }", timeout=90000)
        stats = await page.evaluate(
            """(engine) => {
                 function summarize(rows, keys) {
                   const out = {};
                   for (const k of keys) {
                     const v = rows.map(r => +r[k] || 0);
                     const sorted = v.slice().sort((a, b) => a - b);
                     out[k] = {
                       max: Math.max(...v), mean: v.reduce((a, b) => a + b, 0) / v.length,
                       p10: sorted[Math.floor((sorted.length - 1) * 10 / 100)],
                     };
                   }
                   return out;
                 }
                 const anim = window.__anim;
                 const keys = anim.index.map(([name]) => name);
                 const toRows = frames => frames.map(f => {
                   const r = {}; for (const [name, col] of anim.index) r[name] = f[col]; return r;
                 });
                 const raw = summarize(toRows(anim.frames), keys);
                 // 실제 운영 함수를 그대로 호출 — 손으로 (peak-p10)*gain 을 다시 계산하지 않는다
                 const clone = { fps: anim.fps, index: anim.index, frames: anim.frames.map(r => r.slice()) };
                 AvatarCore.shapeAnim(clone, engine);
                 const shaped = summarize(toRows(clone.frames), keys);
                 return { raw, shaped };
               }""",
            engine)
        await browser.close()
        return stats


def verdict(kind: str, target: float, shaped: dict) -> tuple:
    val = shaped["max"] if kind == "min" else shaped["mean"]
    ok = (val >= target * MARGIN) if kind == "min" else (val <= target / MARGIN)
    return val, ok


async def main():
    all_ok = True
    for engine in ("a2f", "neurosync"):
        for label, text in TEXTS.items():
            s = await probe(engine, text)
            print(f"\n===== {engine} / {label} =====")
            for k in WATCH:
                if k not in s["raw"]:
                    continue
                r, sh = s["raw"][k], s["shaped"][k]
                print(f"  {k:18s} raw(max={r['max']:.3f} mean={r['mean']:.3f} p10={r['p10']:.3f})"
                      f"  shaped(max={sh['max']:.3f} mean={sh['mean']:.3f})")
                if k in TARGETS:
                    kind, target = TARGETS[k]
                    val, ok = verdict(kind, target, sh)
                    all_ok = all_ok and ok
                    reason = ""
                    if k in KILL[engine]:
                        reason = "  (SHAPE.*.kill 로 0 처리됨)"
                    elif kind == "max" and r["max"] < 1e-9 and sh["mean"] < 1e-9:
                        reason = "  (엔진이 이 채널을 사실상 구동하지 않음 — raw 값 자체가 0)"
                    tag = "PASS" if ok else "FAIL"
                    cmp = ">=" if kind == "min" else "<="
                    margin_val = target * MARGIN if kind == "min" else target / MARGIN
                    print(f"    --> {k} {cmp} {target} (10% 마진: {margin_val:.4f}):"
                          f" {val:.3f}  [{tag}]{reason}")
    print("\n" + ("ALL CRITERIA PASS (10% margin)" if all_ok else "SOME CRITERIA FAIL"))


asyncio.run(main())
