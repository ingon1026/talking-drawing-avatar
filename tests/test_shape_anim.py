"""shapeAnim 은 JS 함수라 헤드리스 브라우저에서 직접 호출해 검증한다.

avatar_core.js 는 window.AvatarCore 를 정의하는 평범한 전역 스크립트(모듈 아님)라
빈 페이지에 주입해 그대로 부를 수 있다 — 서버 기동 불필요.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
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
    # 베이스라인 0, 최대 0.31 → 게인 2.4 → 0.744
    frames = [[0.0]] * 18 + [[0.31]] * 2
    out = _run(page, frames, [["jawopen", 0]], "neurosync")
    assert max(f[0] for f in out) == pytest.approx(0.744, abs=0.01)


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
