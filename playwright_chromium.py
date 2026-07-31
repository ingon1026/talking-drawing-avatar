"""Playwright chromium 실행 인자 — 캐시된 headless shell 경로를 한 곳에서만 관리한다.

tests/test_shape_anim.py 와 tools/channel_probe.py 가 같은 버전-고정 경로를 각자
하드코딩하고 있었다. 버전 번호(chromium_headless_shell-1234)가 경로에 박혀 있어
Playwright 업데이트 한 번이면 둘 다 깨진다 — 고칠 곳도 하나여야 한다.
"""
from pathlib import Path

CHROME = Path("/home/ingon/.cache/ms-playwright/chromium_headless_shell-1234"
              "/chrome-headless-shell-linux64/chrome-headless-shell")


def launch_kwargs() -> dict:
    """p.chromium.launch(**launch_kwargs()) 에 그대로 넘긴다. 캐시된 바이너리가 없으면
    빈 dict — Playwright 기본 설치 경로로 폴백한다."""
    return {"executable_path": str(CHROME)} if CHROME.exists() else {}
