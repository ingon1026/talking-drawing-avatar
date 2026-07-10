"""손그림 → 퍼펫 캐릭터 변환 (test_1 돼지, test_2 졸라맨). 공용 빌더 character_builder 사용.

실행: .venv/bin/python tools/make_drawn_characters.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from character_builder import build_character

CHARS = {
    "pig": dict(
        src=ROOT / "test_1.png", name="돼지",
        eyes={"L": (161, 78, 12), "R": (209, 73, 12)},
        mouth_box=(162, 126, 222, 153),
        mouth_center=(191, 138),
        mouth_style={"line": "#2b2b2b", "fill": "#c94f6d", "tongue": "#f2a0b5",
                     "teeth": "#ffffff", "width": 26},
        jaw_drop=6, closed_eye=("#1a1a1a", 4),
    ),
    "stick": dict(
        src=ROOT / "test_2.png", name="졸라맨",
        eyes={"L": (117, 27, 8), "R": (156, 26, 8)},
        mouth_box=(116, 33, 172, 62),  # 머리 윤곽선(x≈177) 침범 금지
        mouth_center=(145, 48),
        mouth_style={"line": "#2e2e2e", "fill": "#3a3a3a", "tongue": "#6a6a6a",
                     "teeth": "#e8e8e8", "width": 20},
        jaw_drop=5, closed_eye=("#1a1a1a", 3),
    ),
}

for cid, cfg in CHARS.items():
    src = cfg.pop("src")
    out = build_character(src, ROOT / "assets_characters" / cid, **cfg)
    print(f"OK: {out}")
