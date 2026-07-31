"""문장 시작 시각 — WordBoundary 마크를 문장에 배분하는 순수 로직."""
import pytest

import app as appmod


def _marks(pairs):
    return [{"offset": t, "text": w} for t, w in pairs]


def test_two_sentences_get_their_first_word_offset():
    sentences = ["오늘 정말 힘들었어요.", "그래도 다행이에요!"]
    marks = _marks([(0.0, "오늘"), (0.4, "정말"), (0.9, "힘들었어요"),
                    (2.4, "그래도"), (3.0, "다행이에요")])
    assert appmod.sentence_starts(sentences, marks) == [0.0, 2.4]


def test_first_sentence_always_starts_at_its_first_mark():
    assert appmod.sentence_starts(["한 문장뿐입니다."], _marks([(0.7, "한"), (1.1, "문장뿐입니다")])) == [0.7]


def test_missing_marks_degrade_without_raising():
    # 마크가 부족하면 마지막으로 알던 시각을 그대로 쓴다 (발화를 막지 않는다)
    out = appmod.sentence_starts(["첫 문장입니다.", "둘째 문장입니다."], _marks([(0.0, "첫")]))
    assert len(out) == 2 and out[0] == 0.0


def test_no_marks_returns_zeros():
    assert appmod.sentence_starts(["첫 문장입니다.", "둘째 문장입니다."], []) == [0.0, 0.0]
