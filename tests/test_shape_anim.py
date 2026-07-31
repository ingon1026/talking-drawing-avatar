"""shapeAnim 은 JS 함수라 헤드리스 브라우저에서 직접 호출해 검증한다.

avatar_core.js 는 window.AvatarCore 를 정의하는 평범한 전역 스크립트(모듈 아님)라
빈 페이지에 주입해 그대로 부를 수 있다 — 서버 기동 불필요.
"""
from pathlib import Path

import pytest

from conftest import ROOT

CORE = ROOT / "static" / "avatar_core.js"
CHROME = Path("/home/ingon/.cache/ms-playwright/chromium_headless_shell-1234"
              "/chrome-headless-shell-linux64/chrome-headless-shell")


@pytest.fixture(scope="module")
def page():
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as p:
        kw = {"executable_path": str(CHROME)} if CHROME.exists() else {}
        browser = p.chromium.launch(**kw)
        pg = browser.new_page()
        pg.set_content("<html><body></body></html>")
        pg.add_script_tag(content=CORE.read_text(encoding="utf-8"))
        yield pg
        browser.close()


def _run(page, frames, index, engine):
    return page.evaluate(
        """([frames, index, engine]) => {
             const anim = { fps: 60, frames, index, head: null };
             AvatarCore.shapeAnim(anim, engine);
             return anim.frames;
           }""",
        [frames, index, engine])


def test_removes_constant_bias(page):
    # 채널 0 이 발화 내내 0.2 로 켜져 있으면 편향이므로 0 이 되어야 한다.
    frames = [[0.2] for _ in range(20)]
    out = _run(page, frames, [["browinnerup", 0]], "neurosync")
    assert max(f[0] for f in out) == pytest.approx(0.0, abs=1e-6)


def test_amplifies_jaw_open(page):
    # 베이스라인 0, 최대 0.31 → 게인 2.8(Task 5 채널 프로브 재측정 후 상향) → 0.868
    # 별칭(aliasing) 의도적: [[0.0]] * 18 은 파이썬에서 동일 리스트 객체를 18번 참조하고,
    # playwright 는 이 객체 identity 를 JS 로 넘길 때도 보존한다(구조적 복제). 그 결과
    # shapeAnim 이 (스냅샷 없이) f[col] 을 제자리에서 읽고 쓰면 마지막 프레임 값이 두 번
    # 처리되어 게인이 중복 적용된다 — 이 테스트는 그 회귀를 잡기 위한 것이므로 리스트
    # 컴프리헨션으로 "고쳐서" 별칭을 없애면 안 된다.
    frames = [[0.0]] * 18 + [[0.31]] * 2
    out = _run(page, frames, [["jawopen", 0]], "neurosync")
    assert max(f[0] for f in out) == pytest.approx(0.868, abs=0.01)


def test_preserves_per_frame_order(page):
    # 회귀 대상: base 계산용 정렬이 vals 를 제자리(in-place) 정렬해버리면, 이후
    # forEach(vals[i]) 가 프레임 i 에 "정렬 후 i번째로 작은 값" 을 배정한다 — 즉 프레임끼리
    # 값이 뒤섞여 오름차순으로만 나오는 램프가 된다. max()/all() 만 보는 테스트는 이미
    # 오름차순인 픽스처에서는 이 버그를 못 잡으므로, 뒤섞인 순서의 값을 넣고 프레임별
    # 전체 결과를 원본 순서대로 검증한다.
    raw = [0.65, 0.05, 0.90, 0.20, 0.75, 0.00, 0.55, 0.35, 0.85, 0.10,
           0.60, 0.30, 0.95, 0.15, 0.70, 0.40, 0.80, 0.25, 0.50, 0.45]
    frames = [[v] for v in raw]  # 서로 다른 리스트 객체(별칭 아님) — 순서 보존이 검증 대상
    out = _run(page, frames, [["jawopen", 0]], "a2f")
    base, gain = 0.05, 2.3  # base = 오름차순 10번째 백분위(=두 번째로 작은 값 0.05), gain = SHAPE.a2f.gain.jawopen(Task 5 재측정 후 상향)
    expected = [min(1.0, max(0.0, (v - base) * gain)) for v in raw]
    assert [f[0] for f in out] == pytest.approx(expected, abs=1e-3)


def test_clamps_to_one(page):
    frames = [[0.0]] * 18 + [[0.9]] * 2
    out = _run(page, frames, [["jawopen", 0]], "a2f")
    assert max(f[0] for f in out) == pytest.approx(1.0, abs=1e-6)


def test_kills_jaw_sideways_channels_on_a2f(page):
    frames = [[0.4, 0.5] for _ in range(20)]
    out = _run(page, frames, [["jawright", 0], ["jawopen", 1]], "a2f")
    assert all(f[0] == 0 for f in out)


def test_unknown_engine_leaves_frames_untouched(page):
    frames = [[0.2, 0.9] for _ in range(20)]
    out = _run(page, frames, [["jawopen", 0], ["browinnerup", 1]], "made-up")
    assert out == frames


def test_empty_anim_is_safe(page):
    assert page.evaluate(
        "() => AvatarCore.shapeAnim({ frames: [], index: [] }, 'a2f').frames.length") == 0
