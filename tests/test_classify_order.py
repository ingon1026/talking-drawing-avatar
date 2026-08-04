"""감정 라벨이 문장 위치와 어긋나지 않는지 — 기존 벤치가 못 잡던 축.

emotion_bench.py 는 정확도와 개수만 본다. 개수가 맞으면서 라벨만 뒤집힌 경우
(실측: angry 가 문장 위치와 무관하게 첫 슬롯으로 끌려나옴)는 통과해버렸다.
여기서는 응답 순서를 일부러 흐트러뜨려 classify() 가 index 로 복원하는지 본다.
"""
import json
from unittest.mock import patch

import llm_source


def _resp(items):
    return json.dumps({"emotions": items})


def test_index_로_뒤섞인_응답을_입력_순서로_복원한다():
    sentences = ["회의는 3시에 시작합니다.", "늦게 온 사람이 사과도 안 해서 화가 나요."]
    # 모델이 순서를 뒤집어 냈지만 index 는 올바른 상황
    shuffled = [
        {"index": 2, "emotion": "angry", "intensity": "high"},
        {"index": 1, "emotion": "neutral", "intensity": "mid"},
    ]
    with patch.object(llm_source, "_ollama", return_value=_resp(shuffled)):
        got = llm_source.classify(sentences)
    assert [g["emo"] for g in got] == ["neutral", "angry"]


def test_index_가_온전하지_않으면_배열_순서를_그대로_쓴다():
    sentences = ["가.", "나."]
    broken = [   # index 가 중복 — 신뢰할 수 없으므로 원래 순서 유지
        {"index": 1, "emotion": "joy", "intensity": "high"},
        {"index": 1, "emotion": "sad", "intensity": "low"},
    ]
    with patch.object(llm_source, "_ollama", return_value=_resp(broken)):
        got = llm_source.classify(sentences)
    assert [g["emo"] for g in got] == ["joy", "sad"]


def test_index_가_없어도_동작한다():
    sentences = ["가.", "나."]
    no_idx = [
        {"emotion": "fear", "intensity": "mid"},
        {"emotion": "neutral", "intensity": "low"},
    ]
    with patch.object(llm_source, "_ollama", return_value=_resp(no_idx)):
        got = llm_source.classify(sentences)
    assert [g["emo"] for g in got] == ["fear", "neutral"]
