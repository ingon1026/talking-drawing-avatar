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


def test_classify_schema_pins_array_length_to_sentence_count():
    # minItems/maxItems 가 문장 수와 정확히 같아야 Ollama(GBNF)가 개수를 강제한다 —
    # 실측(라이브 Ollama)으로 확인된 문법 제약을 회귀로부터 지키는 순수 유닛 테스트.
    schema = llm_source._classify_schema(5)
    emotions = schema["properties"]["emotions"]
    assert emotions["minItems"] == emotions["maxItems"] == 5


def test_split_sentences_basic():
    assert llm_source.split_sentences("오늘 정말 힘들었어요. 그래도 끝나서 다행이에요!") == [
        "오늘 정말 힘들었어요.", "그래도 끝나서 다행이에요!"]


def test_split_sentences_merges_short_fragment_into_previous():
    # "네." 는 10자 미만이라 홀로 서지 못하고 다음 문장과 합쳐진다.
    assert llm_source.split_sentences("네. 오늘 정말 힘들었어요.") == ["네. 오늘 정말 힘들었어요."]


def test_split_sentences_caps_at_six():
    text = " ".join(f"{i}번째 문장입니다." for i in range(12))
    out = llm_source.split_sentences(text)
    assert len(out) == 6
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
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.ok = status_code == 200

    def json(self):
        return {"message": {"content": json.dumps(self._payload)}}


def _mock_ollama(monkeypatch, payload, status_code=200):
    """post() 를 목으로 대체하고, 몇 번 불렸는지 셀 수 있게 호출 목록을 돌려준다
    (classify_cached 가 실제로 재요청을 건너뛰는지 확인하려면 호출 횟수가 필요하다)."""
    calls = []

    def _post(*a, **k):
        calls.append((a, k))
        return _FakeResp(payload, status_code)

    monkeypatch.setattr(llm_source.requests, "post", _post)
    return calls


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


def test_classify_retries_once_on_length_mismatch_then_succeeds(monkeypatch):
    # 1차 응답은 문장이 2개인데 1개만 옴(개수 불일치) → 재요청에서 2개가 오면 성공해야 한다.
    payloads = [
        {"emotions": [{"emotion": "sad", "intensity": "mid"}]},
        {"emotions": [{"emotion": "sad", "intensity": "mid"},
                       {"emotion": "joy", "intensity": "low"}]},
    ]
    calls = []

    def _post(*a, **k):
        calls.append((a, k))
        return _FakeResp(payloads[len(calls) - 1])

    monkeypatch.setattr(llm_source.requests, "post", _post)
    assert llm_source.classify(["문장 하나입니다.", "문장 둘입니다."]) == [
        {"emo": "sad", "intensity": 0.70},
        {"emo": "joy", "intensity": 0.45}]
    assert len(calls) == 2


def test_classify_raises_after_two_length_mismatches(monkeypatch):
    # 재요청에서도 개수가 또 틀리면(루프가 아니라 딱 한 번만 더 물으므로) 그대로 실패해야 한다.
    calls = _mock_ollama(monkeypatch, {"emotions": [{"emotion": "sad", "intensity": "mid"}]})
    with pytest.raises(RuntimeError):
        llm_source.classify(["문장 하나입니다.", "문장 둘입니다."])
    assert len(calls) == 2


def test_classify_falls_back_to_neutral_on_unknown_label(monkeypatch):
    _mock_ollama(monkeypatch, {"emotions": [{"emotion": "excited", "intensity": "weird"}]})
    assert llm_source.classify(["문장 하나입니다."]) == [{"emo": "neutral", "intensity": 0.70}]


def test_classify_empty_input_skips_the_model(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("빈 입력에는 모델을 부르지 않아야 한다")
    monkeypatch.setattr(llm_source.requests, "post", _boom)
    assert llm_source.classify([]) == []


def test_classify_rejects_non_list_emotions(monkeypatch):
    # 스키마를 걸어도 형태 방어는 필요하다 — emotions 가 배열이 아니면 len() 이
    # RuntimeError 밖에서 TypeError 로 터질 수 있었다.
    _mock_ollama(monkeypatch, {"emotions": 5})
    with pytest.raises(RuntimeError):
        llm_source.classify(["문장 하나입니다."])


def test_classify_rejects_non_dict_element(monkeypatch):
    # 원소가 객체가 아니면 e.get() 이 RuntimeError 밖에서 AttributeError 로 터질 수 있었다.
    _mock_ollama(monkeypatch, {"emotions": ["x"]})
    with pytest.raises(RuntimeError):
        llm_source.classify(["문장 하나입니다."])


def test_classify_rejects_non_ok_http_status(monkeypatch):
    # 페이로드를 문장 수와 정확히 맞춰서, 개수 불일치 검사가 아니라 상태 코드 분기
    # 만이 실패 원인이 되게 한다 — 그래야 이 테스트가 실제로 `if not r.ok:` 를 덮는다.
    _mock_ollama(monkeypatch, {"emotions": [{"emotion": "joy", "intensity": "mid"}]},
                 status_code=500)
    with pytest.raises(RuntimeError, match=r"\(Ollama 500\)"):
        llm_source.classify(["문장 하나입니다."])


def test_classify_wraps_other_request_exceptions_as_runtime_error(monkeypatch):
    # ConnectionError/Timeout 외의 RequestException(예: 응답 도중 스트림이 끊기는
    # ChunkedEncodingError)도 RuntimeError 로 감싸져야 한다. 이 장비는 Ollama·NeuroSync·
    # A2F-3D 가 12GB VRAM 을 나눠 쓰므로 Ollama 가 응답 중 OOM-kill 되면 이런 형태로 온다.
    def _boom(*a, **k):
        raise llm_source.requests.exceptions.ChunkedEncodingError("broken stream")
    monkeypatch.setattr(llm_source.requests, "post", _boom)
    with pytest.raises(RuntimeError):
        llm_source.classify(["문장 하나입니다."])


def test_classify_rejects_unhashable_intensity(monkeypatch):
    # intensity 가 문자열이 아니라 list 등으로 오면 INTENSITY.get() 이 RuntimeError 밖에서
    # TypeError: unhashable type 으로 터질 수 있었다 — /api/emotion 이 500 대신 503 을 내야 한다.
    _mock_ollama(monkeypatch, {"emotions": [{"emotion": "joy", "intensity": []}]})
    with pytest.raises(RuntimeError):
        llm_source.classify(["문장 하나입니다."])


def test_classify_cached_reuses_result_without_recalling_model(monkeypatch):
    llm_source.classify_cached.cache_clear()
    calls = _mock_ollama(monkeypatch, {"emotions": [{"emotion": "joy", "intensity": "mid"}]})
    sentences = ("캐시 재사용 확인용 문장입니다.",)
    first = llm_source.classify_cached(sentences)
    second = llm_source.classify_cached(sentences)
    assert first == second == ({"emo": "joy", "intensity": 0.70},)
    assert len(calls) == 1
