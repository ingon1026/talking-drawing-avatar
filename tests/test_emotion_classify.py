"""감정 분류 서버측 로직 — Ollama 호출은 목으로 대체해 네트워크 의존 없이 돈다."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import llm_source


def test_emotion_set_is_seven_and_matches_face_presets():
    assert llm_source.EMOTIONS == (
        "neutral", "joy", "sad", "angry", "surprise", "fear", "shy")


def test_split_sentences_basic():
    assert llm_source.split_sentences("오늘 정말 힘들었어요. 그래도 끝나서 다행이에요!") == [
        "오늘 정말 힘들었어요.", "그래도 끝나서 다행이에요!"]


def test_split_sentences_merges_short_fragment_into_previous():
    # "네." 는 10자 미만이라 홀로 서지 못하고 다음 문장과 합쳐진다.
    assert llm_source.split_sentences("네. 오늘 정말 힘들었어요.") == ["네. 오늘 정말 힘들었어요."]


def test_split_sentences_caps_at_eight():
    text = " ".join(f"{i}번째 문장입니다." for i in range(12))
    out = llm_source.split_sentences(text)
    assert len(out) == 8
    # 초과분은 버리지 않고 마지막 문장에 흡수한다
    assert "11번째" in out[-1]


def test_split_sentences_empty():
    assert llm_source.split_sentences("   ") == []
