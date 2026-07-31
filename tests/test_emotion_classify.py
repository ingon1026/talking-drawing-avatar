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


def test_split_sentences_merges_trailing_short_fragment_backward():
    # 꼬리에 남은 짧은 조각("네.")도 앞으로 접혀야 한다 — 전방 병합 루프만으로는 못 잡는다.
    assert llm_source.split_sentences("감사합니다. 정말 고마워요. 네.") == [
        "감사합니다. 정말 고마워요. 네."]


def test_split_sentences_lone_short_fragment_has_nothing_to_merge_into():
    assert llm_source.split_sentences("네.") == ["네."]


def test_split_sentences_no_terminal_punctuation_returns_whole_string():
    assert llm_source.split_sentences("오늘 정말 힘들었어요") == ["오늘 정말 힘들었어요"]


class _FakeResp:
    status_code = 200
    ok = True

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return {"message": {"content": json.dumps(self._payload)}}


def _mock_ollama(monkeypatch, payload):
    monkeypatch.setattr(llm_source.requests, "post", lambda *a, **k: _FakeResp(payload))


def test_classify_maps_intensity_words_to_numbers(monkeypatch):
    _mock_ollama(monkeypatch, {"emotions": [
        {"emotion": "sad", "intensity": "high"},
        {"emotion": "joy", "intensity": "low"}]})
    assert llm_source.classify(["가나다라마바사아자차", "카타파하가나다라마바"]) == [
        {"emo": "sad", "intensity": 1.0},
        {"emo": "joy", "intensity": 0.45}]


def test_classify_rejects_length_mismatch(monkeypatch):
    _mock_ollama(monkeypatch, {"emotions": [{"emotion": "sad", "intensity": "mid"}]})
    with pytest.raises(RuntimeError):
        llm_source.classify(["문장 하나입니다.", "문장 둘입니다."])


def test_classify_falls_back_to_neutral_on_unknown_label(monkeypatch):
    _mock_ollama(monkeypatch, {"emotions": [{"emotion": "excited", "intensity": "weird"}]})
    assert llm_source.classify(["문장 하나입니다."]) == [{"emo": "neutral", "intensity": 0.70}]


def test_classify_empty_input_skips_the_model(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("빈 입력에는 모델을 부르지 않아야 한다")
    monkeypatch.setattr(llm_source.requests, "post", _boom)
    assert llm_source.classify([]) == []
