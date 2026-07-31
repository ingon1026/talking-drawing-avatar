"""감정 분류 정확도 벤치 — 규칙(1/10) 대비 LLM 이 얼마나 맞히는지.

    PYTHONPATH= .venv/bin/python tools/emotion_bench.py

Ollama 가 떠 있어야 한다. CI 대상이 아니라 회귀 감시용 수동 실행 도구다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import llm_source

CASES = [
    ("이걸 지금 말이라고 하는 거야?", "angry"),
    ("몇 번을 얘기해야 알아듣겠니", "angry"),
    ("와 진짜 대박이다!", "joy"),
    ("드디어 합격했어요", "joy"),
    ("그냥... 아무것도 하기 싫다", "sad"),
    ("이번에도 떨어졌네요", "sad"),
    ("어? 저게 왜 저기 있지", "surprise"),
    ("뒤에 누가 서 있는 것 같아", "fear"),
    ("칭찬해주시니까 좀 그렇네요", "shy"),
    ("오늘 회의는 3시입니다", "neutral"),
]


def main() -> int:
    labels = llm_source.classify([t for t, _ in CASES])
    hit = 0
    for (text, want), got in zip(CASES, labels):
        ok = got["emo"] == want
        hit += ok
        print(f"{'O' if ok else 'X'}  기대={want:9s} 결과={got['emo']:9s} "
              f"({got['intensity']:.2f}) | {text}")
    print(f"\n적중 {hit}/{len(CASES)}")
    return 0 if hit >= 8 else 1


raise SystemExit(main())
