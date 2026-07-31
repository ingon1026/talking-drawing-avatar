"""/api/emotion — FastAPI 핸들러를 직접 호출한다(TestClient 의존성 추가 회피)."""
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as appmod
import llm_source


def test_emotion_returns_one_segment_per_sentence(monkeypatch):
    # 라벨을 인덱스별로 구별되게 만들어야 한다 — 모든 문장에 같은 라벨을 주면
    # 핸들러가 순서를 뒤집거나 labels[0] 을 방송해도 테스트가 못 잡는다.
    emos = ["sad", "joy"]
    monkeypatch.setattr(
        llm_source, "classify_cached",
        lambda s: tuple({"emo": emos[i], "intensity": 0.1 * (i + 1)} for i in range(len(s))))
    out = appmod.emotion(appmod.EmotionReq(text="오늘 정말 힘들었어요. 그래도 다행이에요!"))
    segs = out["segments"]
    assert [s["text"] for s in segs] == ["오늘 정말 힘들었어요.", "그래도 다행이에요!"]
    assert segs[0]["emo"] == "sad" and segs[0]["intensity"] == pytest.approx(0.1)
    assert segs[1]["emo"] == "joy" and segs[1]["intensity"] == pytest.approx(0.2)


def test_emotion_rejects_blank():
    with pytest.raises(HTTPException) as e:
        appmod.emotion(appmod.EmotionReq(text="   "))
    assert e.value.status_code == 400


def test_emotion_returns_503_when_model_unavailable(monkeypatch):
    def _down(_):
        raise RuntimeError("대화 서버(Ollama)가 꺼져 있어요.")
    monkeypatch.setattr(llm_source, "classify_cached", _down)
    with pytest.raises(HTTPException) as e:
        appmod.emotion(appmod.EmotionReq(text="문장 하나입니다."))
    assert e.value.status_code == 503
