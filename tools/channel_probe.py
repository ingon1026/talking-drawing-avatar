"""발화 중 3D 헤드에 실제로 들어가는 채널값을 측정한다.

    PYTHONPATH= .venv/bin/python tools/channel_probe.py

서버가 http://127.0.0.1:8000 에 떠 있어야 한다.

Task 7부터 `speakFlow`가 `shapeAnim(anim, engine)`을 프레임에 in-place로 1회 적용한다.
`weightsFromAnim`이 넘겨받는 `anim.frames`는 그 시점에 이미 운영 코드가 셰이핑을 끝낸
결과이므로, 그걸 다시 `AvatarCore.shapeAnim`에 태우면 이중 적용이 된다(과거 버전의 버그).
`AvatarCore.shapeAnim`을 외부에서 몽키패치해도 소용없다 — `speakFlow` 내부는 그 함수를
클로저로 직접 참조해 호출하므로 export 객체의 프로퍼티 교체가 내부 호출을 가로채지
못한다. 그래서 raw는 `/api/speak_rt` 네트워크 응답(셰이핑 이전의 서버 원본 JSON)을
가로채 얻고, shaped는 운영 경로가 실제로 남긴 `anim.frames`를 그대로 읽는다 — 손으로
재계산하거나 재적용하지 않는다. 두 값은 같은 `names`/컬럼 순서를 공유하므로(셰이핑은
값만 덮어쓸 뿐 컬럼을 재배열하지 않는다) `anim.index`로 raw 배열도 그대로 인덱싱된다.

두 문장(모음·무음 분포가 다름)에 대해 채널별 raw/shaped 를 나란히 출력한다.

수용 기준(전부 10% 마진 포함해 판정 — 예: >=0.7 기준은 실측이 0.77 이상이어야
"마진 있음"):
  - a2f      jawopen 최댓값 >= 0.7
  - a2f      jawright/jawleft 평균 <= 0.02 (SHAPE.a2f.kill 로 0 처리됨 — 이유를 함께 표기)
  - neurosync jawopen 최댓값 >= 0.7
  - neurosync browinnerup/browouterupleft/browouterupright/eyewideleft/eyewideright
    평균 <= 0.05 ("상시 놀란 표정" 결함 — browInnerUp 만이 아니라 눈썹·눈 전체가 대상)

clamp(%) 는 shaped 값이 상한 1.0 에 붙어있는 프레임의 비율이다 — 게인이 너무 세서
입이 이진(열림/닫힘)으로만 보이는지 가늠하는 지표. 수용 기준은 없고 참고용으로만 표기한다.
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
# 평균이 target 이하여야 통과. static/avatar_core.js 의 SHAPE 값을 바꿔도 이 스크립트는
# 그대로 두고 다시 돌리면 된다 — shaped 열은 항상 그 시점의 운영 코드가 실제로 남긴 값이다.
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


def _summarize(frames: list, index: list) -> dict:
    """frames: 프레임×컬럼 리스트. index: [[정규화된 이름, 컬럼번호], ...] (anim.index 그대로)."""
    out = {}
    n = len(frames)
    for name, col in index:
        v = [float(f[col]) if f[col] is not None else 0.0 for f in frames]
        sv = sorted(v)
        clamped = sum(1 for x in v if x >= 1 - 1e-9)
        out[name] = {
            "max": max(v), "mean": sum(v) / n,
            "p10": sv[max(0, (n - 1) * 10 // 100)],
            "clampPct": 100 * clamped / n,
        }
    return out


async def probe(engine: str, text: str) -> dict:
    async with async_playwright() as p:
        kw = {"executable_path": str(CHROME)} if CHROME.exists() else {}
        browser = await p.chromium.launch(
            args=["--autoplay-policy=no-user-gesture-required"], **kw)
        page = await browser.new_page()
        raw_holder: dict = {}

        # raw: 서버가 준 셰이핑 전 원본 JSON 을 네트워크 레벨에서 그대로 가로챈다.
        async def on_response(response):
            if response.url.endswith("/api/speak_rt") and response.ok:
                raw_holder["data"] = await response.json()
        page.on("response", on_response)

        await page.goto("http://127.0.0.1:8000/3d")
        await page.wait_for_function(
            "!document.getElementById('status').textContent.includes('로딩')", timeout=90000)
        await page.select_option("#engine", engine)
        # shaped: weightsFromAnim 이 넘겨받는 anim.frames — 운영 경로(speakFlow)가
        # shapeAnim 을 이미 1회 적용해놓은 실제 결과. 여기서 다시 셰이핑하지 않는다.
        await page.evaluate("""() => {
          const orig = AvatarCore.weightsFromAnim;
          AvatarCore.weightsFromAnim = (a, au) => { if (a && a.frames) window.__shaped = a; return orig(a, au); };
        }""")
        await page.fill("#text", text)
        await page.click("#send")
        await page.wait_for_function(
            "() => { const a = document.getElementById('audio');"
            "        return a.duration > 0 && a.ended; }", timeout=90000)
        shaped = await page.evaluate(
            "() => ({ index: window.__shaped.index, frames: window.__shaped.frames })")
        await browser.close()

        index = shaped["index"]
        shaped_stats = _summarize(shaped["frames"], index)
        raw_data = raw_holder.get("data")
        raw_stats = _summarize(raw_data["frames"], index) if raw_data else None
        return {"raw": raw_stats, "shaped": shaped_stats}


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
            if s["raw"] is None:
                print("  (raw 캡처 실패 — /api/speak_rt 응답을 못 받았다. raw 열 생략, shaped 만 표기)")
            for k in WATCH:
                if k not in s["shaped"]:
                    continue
                r, sh = (s["raw"] or {}).get(k), s["shaped"][k]
                raw_str = (f"raw(max={r['max']:.3f} mean={r['mean']:.3f} p10={r['p10']:.3f})  "
                           if r else "")
                print(f"  {k:18s} {raw_str}"
                      f"shaped(max={sh['max']:.3f} mean={sh['mean']:.3f} clamp={sh['clampPct']:.1f}%)")
                if k in TARGETS:
                    kind, target = TARGETS[k]
                    val, ok = verdict(kind, target, sh)
                    all_ok = all_ok and ok
                    reason = ""
                    if k in KILL[engine]:
                        reason = "  (SHAPE.*.kill 로 0 처리됨)"
                    elif kind == "max" and r is not None and r["max"] < 1e-9 and sh["mean"] < 1e-9:
                        reason = "  (엔진이 이 채널을 사실상 구동하지 않음 — raw 값 자체가 0)"
                    tag = "PASS" if ok else "FAIL"
                    cmp = ">=" if kind == "min" else "<="
                    margin_val = target * MARGIN if kind == "min" else target / MARGIN
                    print(f"    --> {k} {cmp} {target} (10% 마진: {margin_val:.4f}):"
                          f" {val:.3f}  [{tag}]{reason}")
    print("\n" + ("ALL CRITERIA PASS (10% margin)" if all_ok else "SOME CRITERIA FAIL"))


asyncio.run(main())
