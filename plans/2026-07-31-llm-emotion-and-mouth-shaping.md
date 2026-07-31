# LLM 감정 연계 + 입모양 정상화 — 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 텍스트의 감정을 로컬 LLM이 읽어 석고 헤드의 표정·목소리에 반영하고, 발화 중 입이 실제로 벌어지게 한다.

**Architecture:** 상주 중인 Ollama(EXAONE 3.5 2.4B)에 문장별 감정을 분류시킨다(서버가 문장을 쪼개고 LLM은 라벨만 단다 — 기존 대화 경로와 같은 스키마 제약 디코딩). 클라이언트는 감정 → 발화 순서로 두 번 호출하고, edge-tts WordBoundary에서 뽑은 문장 시작 시각으로 발화 도중 표정을 바꾼다. 엔진(A2F/NeuroSync) 출력은 세기 손잡이가 없으므로 클라이언트가 받은 직후 발화당 1회 정규화(퍼센타일 베이스라인 제거 + 게인)한다.

**Tech Stack:** FastAPI · Ollama(EXAONE 3.5 2.4B) · edge-tts · NVIDIA Audio2Face-3D / NeuroSync · three.js · 순수 JS(빌드 없음) · pytest · Playwright

**설계 문서:** `specs/2026-07-31-llm-emotion-and-mouth-shaping-design.md`

## Global Constraints

- **감정 집합은 7종 고정**: `neutral, joy, sad, angry, surprise, fear, shy`. `llm_source.EMOTIONS`와 `avatar_core.EMOTIONS`가 반드시 같아야 한다.
- **모든 신규 경로는 실패 시 현행 동작으로 떨어진다.** 감정 분류 실패·타임아웃·Ollama 미가동이 발화를 막아서는 안 된다. 사용자에게 에러를 표시하지 않는다.
- **기존 대화 모드(`/api/chat` → `speak(reply, emotion)`) 무회귀.** 이 경로는 감정 분류를 호출하지 않는다.
- **`avatar_core.js` 사본 3개**: `face/static/avatar_core.js`(원본) · `face/docs/avatar_core.js` · `drawface-live/docs/avatar_core.js`. 원본 수정 시 `cp`로 동기화한다 (app.py 기동 시 앞 둘의 해시를 비교해 경고한다).
- **빌드 단계 없음.** `docs/`·`static/`은 정적 파일 그대로 서빙된다. 번들러·트랜스파일 도입 금지.
- **파이썬 실행은 저장소 venv**: `PYTHONPATH= /home/ingon/face/.venv/bin/python`. (`PYTHONPATH=` 접두사는 셸에 실린 ROS2 경로 오염을 차단한다.)
- **테스트 실행**: `PYTHONPATH= /home/ingon/face/.venv/bin/python -m pytest tests/ -q`
- **커밋 메시지에 AI 귀속 표시(Co-Authored-By, Generated with 등)를 절대 넣지 않는다.**

## 실측 기준값 (변경 전)

| 항목 | 현재 | 목표 |
| --- | --- | --- |
| 감정 벤치 적중 | 1/10 | 8/10 이상 |
| `jawOpen` 최대 (A2F) | 0.399 | 0.7 이상 |
| `jawOpen` 최대 (NeuroSync) | 0.310 | 0.7 이상 |
| `jawRight` 평균 (A2F) | 0.170 | 0.02 이하 |
| `browInnerUp` 평균 (NeuroSync) | 0.145 | 0.05 이하 |
| 감정 분류 지연 (웜) | — | 1.0초 이하 (실측 0.78초) |

## 병렬 실행 가이드

- **Task 1~3(팀 A)** 와 **Task 4~5(팀 B)** 는 파일이 겹치지 않아 완전 병렬.
- **Task 6(팀 C)** 는 `app.py`를 팀 A와 공유한다 → **Task 2 완료 후** 착수하거나 별도 워크트리에서 작업 후 순차 머지.
- **Task 7~9(통합)** 는 위가 모두 끝난 뒤.

---

### Task 1: 감정 집합 7종 통일 + 문장 분할

**Files:**
- Modify: `llm_source.py:29` (EMOTIONS 상수)
- Create: `tests/test_emotion_classify.py`

**Interfaces:**
- Consumes: 없음
- Produces: `llm_source.EMOTIONS: tuple[str, ...]` (7종), `llm_source.split_sentences(text: str) -> list[str]`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_emotion_classify.py` 생성:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH= /home/ingon/face/.venv/bin/python -m pytest tests/test_emotion_classify.py -q`
Expected: FAIL — `AttributeError: module 'llm_source' has no attribute 'split_sentences'` 및 EMOTIONS 5종 불일치

(`pytest`가 없으면 먼저: `uv pip install --python /home/ingon/face/.venv/bin/python pytest`)

- [ ] **Step 3: 구현**

`llm_source.py`의 `EMOTIONS` 줄을 교체하고 분할 함수를 추가한다:

```python
# 얼굴 프리셋(avatar_core.EMOTIONS)과 같은 7종 — 둘이 어긋나면 LLM이 못 쓰는 표정이 생긴다.
EMOTIONS = ("neutral", "joy", "sad", "angry", "surprise", "fear", "shy")

_SPLIT = re.compile(r"(?<=[.!?…])\s+")
MIN_SENTENCE_CHARS = 10   # 이보다 짧은 조각은 앞 문장에 붙인다 ("네." 같은 맞장구)
MAX_SENTENCES = 8         # LLM 출력 길이를 묶어 지연을 예측 가능하게 한다


def split_sentences(text: str) -> list[str]:
    """문장 분할. 감정 분류(①)와 발화 타이밍(②)이 같은 결과를 써야 인덱스가 맞는다."""
    parts = [p.strip() for p in _SPLIT.split(text.strip()) if p.strip()]
    if not parts:
        return []
    merged = [parts[0]]
    for p in parts[1:]:
        if len(merged[-1]) < MIN_SENTENCE_CHARS:
            merged[-1] += " " + p
        else:
            merged.append(p)
    if len(merged) > MAX_SENTENCES:
        merged = merged[:MAX_SENTENCES - 1] + [" ".join(merged[MAX_SENTENCES - 1:])]
    return merged
```

`re`는 이미 `llm_source.py` 상단에서 임포트되어 있다(확인만 하고 중복 추가하지 말 것).

- [ ] **Step 4: 통과 확인**

Run: `PYTHONPATH= /home/ingon/face/.venv/bin/python -m pytest tests/test_emotion_classify.py -q`
Expected: 5 passed

- [ ] **Step 5: 대화 경로 무회귀 확인**

Run: `PYTHONPATH= /home/ingon/face/.venv/bin/python -c "import llm_source; print(llm_source.chat('안녕')['emotion'])"`
Expected: 7종 중 하나가 출력된다 (Ollama 미가동이면 RuntimeError — 그 경우 이 단계는 건너뛰고 다음으로)

- [ ] **Step 6: 커밋**

```bash
cd /home/ingon/face
git add llm_source.py tests/test_emotion_classify.py
git commit -m "feat(llm): unify the emotion set at 7 and add sentence splitting

The face presets in avatar_core.EMOTIONS carry 7 emotions but the LLM
only knew 5, so fear and shy could never fire. Splitting lives here so
the classifier and the speech-timing path cut sentences identically."
```

---

### Task 2: `classify()` — LLM 문장별 감정 분류

**Files:**
- Modify: `llm_source.py` (Task 1 이후)
- Modify: `tests/test_emotion_classify.py`

**Interfaces:**
- Consumes: `llm_source.EMOTIONS`, `llm_source.split_sentences` (Task 1)
- Produces:
  - `llm_source.classify(sentences: list[str]) -> list[dict]` — `[{"emo": str, "intensity": float}]`, 입력과 같은 길이. 실패 시 `RuntimeError`.
  - `llm_source.classify_cached(sentences: tuple[str, ...]) -> tuple[dict, ...]`
  - `llm_source.INTENSITY: dict[str, float]`

- [ ] **Step 1: 실패하는 테스트 추가**

`tests/test_emotion_classify.py` 끝에 붙인다:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH= /home/ingon/face/.venv/bin/python -m pytest tests/test_emotion_classify.py -q`
Expected: FAIL — `AttributeError: module 'llm_source' has no attribute 'classify'`

- [ ] **Step 3: 구현**

`llm_source.py`에 추가한다 (`chat()` 아래):

```python
INTENSITY = {"low": 0.45, "mid": 0.70, "high": 1.0}

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {"emotions": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "emotion": {"type": "string", "enum": list(EMOTIONS)},
            "intensity": {"type": "string", "enum": list(INTENSITY)},
        },
        "required": ["emotion", "intensity"],
    }}},
    "required": ["emotions"],
}

CLASSIFY_SYSTEM = (
    "너는 문장의 감정을 분류한다.\n"
    f"- 감정은 {', '.join(EMOTIONS)} 중 하나다. 뚜렷하지 않으면 neutral 을 쓴다.\n"
    "- intensity 는 low, mid, high 중 하나다.\n"
    "- 입력 문장 수와 정확히 같은 개수를, 입력 순서대로 출력한다.\n"
    "- 문장을 다시 쓰거나 설명하지 않는다. 감정만 판단한다."
)


def classify(sentences: list[str]) -> list[dict]:
    """문장 리스트 → [{"emo", "intensity"}] (입력과 같은 길이).

    문장 분할은 호출측이 끝낸 상태로 들어온다 — 모델이 텍스트를 재작성해
    원문과 어긋나는 사고를 막으려고 라벨링만 시킨다.
    """
    if not sentences:
        return []
    numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(sentences))
    try:
        r = requests.post(
            API,
            json={
                "model": MODEL,
                "messages": [{"role": "system", "content": CLASSIFY_SYSTEM},
                             {"role": "user", "content": numbered}],
                "stream": False,
                "format": CLASSIFY_SCHEMA,
                "keep_alive": KEEP_ALIVE,
                # temperature 를 낮게: 분류는 창작이 아니다. num_predict 는 문장당 ~20토큰.
                "options": {"num_ctx": NUM_CTX, "temperature": 0.2,
                            "num_predict": 24 * len(sentences) + 32},
            },
            timeout=TIMEOUT,
        )
    except requests.exceptions.ConnectionError:
        raise RuntimeError("대화 서버(Ollama)가 꺼져 있어요.")
    except requests.exceptions.Timeout:
        raise RuntimeError("감정 분류가 너무 늦어요.")
    if not r.ok:
        raise RuntimeError(f"감정 분류에 실패했어요. (Ollama {r.status_code})")

    try:
        arr = json.loads(r.json().get("message", {}).get("content", "")).get("emotions", [])
    except (json.JSONDecodeError, AttributeError):
        raise RuntimeError("감정 분류 응답을 읽지 못했어요.")
    if len(arr) != len(sentences):
        raise RuntimeError("감정 분류 결과 개수가 문장 수와 다릅니다.")

    out = []
    for e in arr:
        emo = e.get("emotion")
        out.append({"emo": emo if emo in EMOTIONS else "neutral",
                    "intensity": INTENSITY.get(e.get("intensity"), 0.70)})
    return out


@lru_cache(maxsize=64)
def classify_cached(sentences: tuple[str, ...]) -> tuple[dict, ...]:
    """같은 문장 묶음 재요청은 모델을 다시 부르지 않는다 (쇼케이스·반복 시연)."""
    return tuple(classify(list(sentences)))
```

`llm_source.py` 상단 임포트에 추가: `from functools import lru_cache`

- [ ] **Step 4: 통과 확인**

Run: `PYTHONPATH= /home/ingon/face/.venv/bin/python -m pytest tests/test_emotion_classify.py -q`
Expected: 9 passed

- [ ] **Step 5: 실제 모델로 정확도 확인 (Ollama 필요)**

```bash
cd /home/ingon/face && PYTHONPATH= .venv/bin/python -c "
import llm_source as L
s = L.split_sentences('오늘 정말 힘들었어요. 그래도 끝나서 다행이에요!')
print(s); print(L.classify(s))"
```

Expected: `[{'emo': 'sad', ...}, {'emo': 'joy', ...}]` (Ollama 미가동이면 건너뛴다)

- [ ] **Step 6: 커밋**

```bash
cd /home/ingon/face
git add llm_source.py tests/test_emotion_classify.py
git commit -m "feat(llm): per-sentence emotion classification via the resident model

Reuses the schema-constrained decoding the chat path already proved out.
Intensity comes back as low/mid/high because a 2.4B model scatters raw
floats; the server maps them to 0.45/0.70/1.0. A length mismatch is a
hard failure so callers fall back to the rule matcher rather than
mislabelling sentences."
```

---

### Task 3: `POST /api/emotion` 엔드포인트

**Files:**
- Modify: `app.py` (`/api/chat` 정의 아래에 추가)
- Create: `tests/test_emotion_api.py`

**Interfaces:**
- Consumes: `llm_source.split_sentences`, `llm_source.classify_cached` (Task 1·2)
- Produces: `app.EmotionReq` (pydantic), `app.emotion(req) -> dict` — `{"segments": [{"text", "emo", "intensity"}]}`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_emotion_api.py`:

```python
"""/api/emotion — FastAPI 핸들러를 직접 호출한다(TestClient 의존성 추가 회피)."""
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as appmod
import llm_source


def test_emotion_returns_one_segment_per_sentence(monkeypatch):
    monkeypatch.setattr(llm_source, "classify_cached",
                        lambda s: tuple({"emo": "joy", "intensity": 0.7} for _ in s))
    out = appmod.emotion(appmod.EmotionReq(text="오늘 정말 힘들었어요. 그래도 다행이에요!"))
    assert [s["text"] for s in out["segments"]] == [
        "오늘 정말 힘들었어요.", "그래도 다행이에요!"]
    assert out["segments"][0]["emo"] == "joy"


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
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH= /home/ingon/face/.venv/bin/python -m pytest tests/test_emotion_api.py -q`
Expected: FAIL — `AttributeError: module 'app' has no attribute 'EmotionReq'`

- [ ] **Step 3: 구현**

`app.py`의 `/api/chat` 핸들러 아래에 추가:

```python
class EmotionReq(BaseModel):
    text: str


@app.post("/api/emotion")
def emotion(req: EmotionReq):
    """텍스트 → 문장별 감정. 실패는 503 — 클라이언트가 규칙 폴백으로 조용히 진행한다."""
    if not req.text.strip():
        raise HTTPException(400, "빈 입력입니다.")
    try:
        import llm_source
    except ModuleNotFoundError:
        raise HTTPException(503, "감정 분류를 쓸 수 없습니다 (llm_source.py 없음).")
    sentences = llm_source.split_sentences(req.text)
    if not sentences:
        raise HTTPException(400, "문장을 찾지 못했습니다.")
    try:
        labels = llm_source.classify_cached(tuple(sentences))
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    return {"segments": [{"text": s, **lab} for s, lab in zip(sentences, labels)]}
```

- [ ] **Step 4: 통과 확인**

Run: `PYTHONPATH= /home/ingon/face/.venv/bin/python -m pytest tests/ -q`
Expected: 12 passed

- [ ] **Step 5: 커밋**

```bash
cd /home/ingon/face
git add app.py tests/test_emotion_api.py
git commit -m "feat(api): POST /api/emotion for per-sentence emotion

503 on any classifier failure is deliberate: the client treats it as
'use the rule matcher' and never surfaces an error, so speech still
works with Ollama down."
```

---

### Task 4: `shapeAnim()` — 엔진 출력 정규화

**Files:**
- Modify: `static/avatar_core.js` (`weightsFromAnim` 정의 아래, 약 103행 뒤)
- Create: `tests/test_shape_anim.py`

**Interfaces:**
- Consumes: 없음
- Produces: `AvatarCore.shapeAnim(anim, engine) -> anim` — `anim.frames`를 제자리 수정하고 같은 객체를 반환

입력 자료구조(기존):

```js
anim = { fps: 60, frames: [[...52 floats], ...], head: [...], index: [[normName, col], ...] }
```

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_shape_anim.py`:

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH= /home/ingon/face/.venv/bin/python -m pytest tests/test_shape_anim.py -q`
Expected: FAIL — `AvatarCore.shapeAnim is not a function`

(playwright가 없으면 `importorskip`으로 skip된다 — 그 경우 먼저 `uv pip install --python /home/ingon/face/.venv/bin/python playwright`)

- [ ] **Step 3: 구현**

`static/avatar_core.js`의 `weightsFromAnim` 함수 바로 아래에 추가:

```js
  // ---------- 엔진 출력 셰이핑 (발화당 1회) ----------
  // A2F·NeuroSync 둘 다 턱을 거의 안 벌리고(jawOpen 최대 0.31~0.40 실측) 발화 내내 켜져 있는
  // 편향 채널이 있다(A2F jawRight 평균 0.17, NeuroSync browInnerUp 0.145). 엔진 쪽에는 세기
  // 손잡이가 없어서(A2F model.json 은 경로 설정뿐) 받은 프레임 전체를 여기서 정규화한다.
  const SHAPE = {
    a2f:       { gain: { jawopen: 1.9 }, kill: ["jawright", "jawleft"] },
    neurosync: { gain: { jawopen: 2.4, mouthfunnel: 0.7 }, kill: [] },
  };
  const BASELINE_PCT = 10;   // 채널별 하위 백분위 = "상시 켜져 있는" 성분

  function shapeAnim(anim, engine) {
    const prof = SHAPE[engine];
    if (!prof || !anim || !anim.frames || !anim.frames.length || !anim.index) return anim;
    const kill = new Set(prof.kill);
    for (const [name, col] of anim.index) {
      if (kill.has(name)) {
        for (const f of anim.frames) f[col] = 0;
        continue;
      }
      const sorted = anim.frames.map(f => f[col]).sort((a, b) => a - b);
      const base = sorted[Math.floor((sorted.length - 1) * BASELINE_PCT / 100)];
      const gain = prof.gain[name] || 1;
      if (!base && gain === 1) continue;            // 손댈 것 없는 채널은 건너뛴다
      for (const f of anim.frames) {
        f[col] = Math.min(1, Math.max(0, (f[col] - base) * gain));
      }
    }
    return anim;
  }
```

그리고 파일 끝 export 목록(약 1114행 `norm, inferEmotion, ...`)에 `shapeAnim` 을 추가한다:

```js
    norm, inferEmotion, voiceProsody, smoothStep, weightsFromAnim, shapeAnim,
```

- [ ] **Step 4: 통과 확인**

Run: `PYTHONPATH= /home/ingon/face/.venv/bin/python -m pytest tests/test_shape_anim.py -q`
Expected: 6 passed

- [ ] **Step 5: 커밋**

```bash
cd /home/ingon/face
git add static/avatar_core.js tests/test_shape_anim.py
git commit -m "feat(core): normalize engine output so the mouth actually opens

Both blendshape engines cap jawOpen around 0.3-0.4 and leak a constant
bias (A2F drifts the jaw sideways, NeuroSync holds the brows up), and
neither exposes a strength parameter. Subtracting each channel's 10th
percentile kills the always-on component; a per-engine gain brings
jawOpen to ~0.75. Runs once per utterance on the frames we already have."
```

---

### Task 5: 채널 측정 스크립트

**Files:**
- Create: `tools/channel_probe.py`

**Interfaces:**
- Consumes: 실행 중인 서버(`http://127.0.0.1:8000/3d`)
- Produces: 표준출력 — 채널별 최대·평균. 수용 기준 확인용

- [ ] **Step 1: 스크립트 작성**

`tools/channel_probe.py`:

```python
"""발화 중 3D 헤드에 실제로 들어가는 채널값을 측정한다.

    PYTHONPATH= .venv/bin/python tools/channel_probe.py

서버가 http://127.0.0.1:8000 에 떠 있어야 한다. 수용 기준(jawOpen >= 0.7,
jawRight 평균 <= 0.02, browInnerUp 평균 <= 0.05) 확인용.
"""
import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

CHROME = Path("/home/ingon/.cache/ms-playwright/chromium_headless_shell-1234"
              "/chrome-headless-shell-linux64/chrome-headless-shell")
TEXT = "안녕하세요. 오늘 날씨가 정말 좋네요. 같이 산책이라도 갈까요?"
WATCH = ("jawopen", "jawright", "jawleft", "mouthfunnel", "browinnerup")


async def probe(engine: str) -> dict:
    async with async_playwright() as p:
        kw = {"executable_path": str(CHROME)} if CHROME.exists() else {}
        browser = await p.chromium.launch(
            args=["--autoplay-policy=no-user-gesture-required"], **kw)
        page = await browser.new_page()
        await page.goto("http://127.0.0.1:8000/3d")
        await page.wait_for_function(
            "!document.getElementById('status').textContent.includes('로딩')", timeout=90000)
        await page.select_option("#engine", engine)
        # 렌더 루프가 매 프레임 넘기는 가중치를 가로챈다
        await page.evaluate("""() => {
          window.__w = [];
          const orig = AvatarCore.smoothStep;
          AvatarCore.smoothStep = (s, w) => { if (w) window.__w.push(w); return orig(s, w); };
        }""")
        await page.fill("#text", TEXT)
        await page.click("#send")
        await page.wait_for_function(
            "() => { const a = document.getElementById('audio');"
            "        return a.duration > 0 && a.ended; }", timeout=90000)
        stats = await page.evaluate("""() => {
          const W = window.__w.filter(w => Object.keys(w).length);
          const out = {};
          const keys = new Set(); W.forEach(w => Object.keys(w).forEach(k => keys.add(k)));
          for (const k of keys) {
            const v = W.map(w => +w[k] || 0);
            out[k] = { max: Math.max(...v), mean: v.reduce((a, b) => a + b, 0) / v.length };
          }
          return out;
        }""")
        await browser.close()
        return stats


async def main():
    for engine in ("a2f", "neurosync"):
        s = await probe(engine)
        print(f"\n===== {engine} =====")
        for k in WATCH:
            if k in s:
                print(f"  {k:14s} max={s[k]['max']:.3f}  mean={s[k]['mean']:.3f}")


asyncio.run(main())
```

- [ ] **Step 2: 변경 전 기준값 재현 확인**

Run: `cd /home/ingon/face && PYTHONPATH= .venv/bin/python tools/channel_probe.py`
Expected: Task 4 적용 후이므로 `jawopen max >= 0.7`, `jawright mean <= 0.02`(a2f), `browinnerup mean <= 0.05`(neurosync)

미달이면 `SHAPE` 게인을 조정하고 Task 4의 테스트 기대값도 함께 갱신한다.

- [ ] **Step 3: 커밋**

```bash
cd /home/ingon/face
git add tools/channel_probe.py
git commit -m "test(tools): channel probe for the speech morph pipeline

Drives a real utterance and prints per-channel max/mean so the mouth
gain stays honest — this is how the 0.31-0.40 jawOpen ceiling was found."
```

---

### Task 6: 문장 시작 시각 (edge-tts WordBoundary)

**Files:**
- Modify: `app.py:63-77` (`tts_to_wav`), `app.py:108-121` (`rt_result`)
- Create: `tests/test_sentence_timing.py`

**⚠️ Task 3 완료 후 착수한다** (`app.py` 충돌 회피).

**Interfaces:**
- Consumes: `llm_source.split_sentences` (Task 1)
- Produces:
  - `app.tts_to_wav(...) -> list[dict]` — 반환값이 `None`에서 `[{"offset": float, "text": str}]`(단어 마크)로 바뀐다
  - `app.sentence_starts(sentences: list[str], marks: list[dict]) -> list[float]`
  - `/api/speak_rt` 응답에 `"sentences": [{"text": str, "start": float}]` 추가

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_sentence_timing.py`:

```python
"""문장 시작 시각 — WordBoundary 마크를 문장에 배분하는 순수 로직."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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
```

- [ ] **Step 2: 실패 확인**

Run: `PYTHONPATH= /home/ingon/face/.venv/bin/python -m pytest tests/test_sentence_timing.py -q`
Expected: FAIL — `AttributeError: module 'app' has no attribute 'sentence_starts'`

- [ ] **Step 3: `tts_to_wav`를 stream 방식으로 교체**

`app.py`의 `tts_to_wav`를 통째로 바꾼다:

```python
async def _synth_mp3(text: str, voice: str, mp3: Path, kw: dict) -> list[dict]:
    """mp3 를 쓰면서 WordBoundary 마크를 모은다. offset 은 100ns 단위 → 초로 변환."""
    marks = []
    with open(mp3, "wb") as f:
        async for chunk in edge_tts.Communicate(text, voice, **kw).stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                marks.append({"offset": chunk["offset"] / 1e7, "text": chunk.get("text", "")})
    return marks


def tts_to_wav(text: str, voice: str, wav: Path, keep_mp3: bool = False, prosody=None) -> list[dict]:
    """텍스트 → wav(16k mono). 반환값은 단어 경계 마크 — 문장 시작 시각 계산에 쓴다."""
    # 영어 위주인데 한국어전용 음성을 골랐으면 멀티링구얼로 자동 스왑(립싱크는 언어 무관).
    if "Multilingual" not in voice and _english_heavy(text):
        voice = MULTI_VOICE
    mp3 = wav.with_suffix(".mp3")
    # edge-tts 는 rate/volume 은 퍼센트, pitch 는 Hz 문자열(부호 필수)을 받는다.
    p = prosody or {}
    kw = {"rate": f"{round(p.get('rate', 0) * 100):+d}%",
          "volume": f"{round(p.get('volume', 0) * 100):+d}%",
          "pitch": f"{round(p.get('pitch', 0) * 100):+d}Hz"}
    marks = asyncio.run(_synth_mp3(text, voice, mp3, kw))
    subprocess.run(["ffmpeg", "-y", "-i", str(mp3), "-ar", "16000", "-ac", "1",
                    "-c:a", "pcm_s16le", str(wav)], check=True, capture_output=True)
    if not keep_mp3:
        mp3.unlink()
    return marks
```

- [ ] **Step 4: `sentence_starts` 추가**

`app.py`의 `tts_to_wav` 아래에 추가:

```python
_NONWORD = re.compile(r"[^\w]", re.UNICODE)


def sentence_starts(sentences: list[str], marks: list[dict]) -> list[float]:
    """각 문장의 시작 시각(초). 단어 마크를 글자 수만큼 순서대로 배분한다.

    edge-tts 는 문장 경계를 알려주지 않고 단어만 준다 — 문장의 (문장부호 제외) 글자 수를
    채울 때까지 마크를 소비하는 방식으로 경계를 복원한다.
    """
    starts, i = [], 0
    for s in sentences:
        if i >= len(marks):
            starts.append(starts[-1] if starts else 0.0)
            continue
        starts.append(marks[i]["offset"])
        need, got = len(_NONWORD.sub("", s)), 0
        while i < len(marks) and got < need:
            got += len(_NONWORD.sub("", marks[i]["text"]))
            i += 1
    return starts
```

`app.py` 상단에 `import re` 가 없으면 추가한다.

- [ ] **Step 5: `rt_result`에 sentences 실기**

`app.py`의 `rt_result` 본문을 교체:

```python
def rt_result(r: SpeakRtReq, job_id: str) -> dict:
    """Phase B: 텍스트 → mp3 + 블렌드셰이프 프레임 (퍼펫 렌더러용). 0.6초급이라 동기 처리."""
    wav = OUT / f"{job_id}.wav"
    try:
        marks = tts_to_wav(r.text, r.voice, wav, keep_mp3=True,
                           prosody={"rate": r.rate, "pitch": r.pitch, "volume": r.volume})
        if r.engine == "a2f":
            import a2f_source as source  # lazy: 모듈/엔진은 첫 요청 때 로드
        else:
            import blendshape_source as source
        bs = source.audio_to_blendshapes(str(wav))
        out = {"audio_url": f"/media/{job_id}.mp3", **bs}
        # 문장 시작 시각 — 감정 전환용. llm_source 가 없거나 마크가 없으면 조용히 생략한다.
        try:
            import llm_source
            sentences = llm_source.split_sentences(r.text)
            if sentences and marks:
                out["sentences"] = [{"text": s, "start": t}
                                    for s, t in zip(sentences, sentence_starts(sentences, marks))]
        except Exception:
            pass
        return out
    finally:
        wav.unlink(missing_ok=True)
```

- [ ] **Step 6: 통과 확인**

Run: `PYTHONPATH= /home/ingon/face/.venv/bin/python -m pytest tests/ -q`
Expected: 16 passed

- [ ] **Step 7: 실제 발화로 확인 (서버 필요)**

```bash
curl -s -X POST http://127.0.0.1:8000/api/speak_rt \
  -H 'Content-Type: application/json' \
  -d '{"text":"오늘 정말 힘들었어요. 그래도 끝나서 다행이에요!","voice":"ko-KR-InJoonNeural","engine":"a2f"}' \
  | /home/ingon/face/.venv/bin/python -c "import json,sys; print(json.load(sys.stdin).get('sentences'))"
```

Expected: 두 원소, 두 번째 `start`가 0보다 크고 오디오 길이보다 작다

- [ ] **Step 8: 커밋**

```bash
cd /home/ingon/face
git add app.py tests/test_sentence_timing.py
git commit -m "feat(api): carry sentence start times from edge-tts word boundaries

Switches TTS from .save() to .stream() so WordBoundary events survive,
then reconstructs sentence boundaries by spending word marks against
each sentence's character count — edge-tts reports words, not sentences.
Speech still works when the marks or llm_source are missing."
```

---

### Task 7: 클라이언트 배선 — 감정 분류 호출 + 셰이핑 적용

**Files:**
- Modify: `static/avatar_core.js` (`speakFlow`, `speakWithEmotion`)

**⚠️ Task 3·4·6 완료 후 착수한다.**

**Interfaces:**
- Consumes: `POST /api/emotion` (Task 3), `AvatarCore.shapeAnim` (Task 4), `sentences` 응답 필드 (Task 6)
- Produces:
  - `AvatarCore.classifyEmotion(text, opts) -> Promise<Array|null>`
  - `speakFlow`가 반환하는 `anim`에 `anim.sentences` 추가 (없으면 `undefined`)

- [ ] **Step 1: `classifyEmotion` 추가**

`static/avatar_core.js`의 `inferEmotion` 아래에 추가:

```js
  // ---------- LLM 감정 분류 (규칙 매칭의 상위 경로) ----------
  // inferEmotion 정규식은 한국어 10문장 벤치에서 1/10 이었다 — 상주 LLM 에 물어본다.
  // 실패·타임아웃·503 은 전부 null 로 접어서 호출측이 규칙으로 떨어지게 한다.
  async function classifyEmotion(text, { timeoutMs = 4000 } = {}) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      const res = await fetch("/api/emotion", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }), signal: ctl.signal,
      });
      if (!res.ok) return null;
      const segs = (await res.json()).segments;
      return Array.isArray(segs) && segs.length ? segs : null;
    } catch {
      return null;      // 오프라인·중단·파싱 실패 — 규칙 폴백
    } finally {
      clearTimeout(timer);
    }
  }
```

타임아웃 4초는 콜드 스타트(실측 3.5초)를 넘기고, 그보다 늦으면 규칙으로 진행한다.

- [ ] **Step 2: `speakFlow`가 셰이핑·문장 정보를 싣게 수정**

`speakFlow` 본문을 교체:

```js
  async function speakFlow({ text, voice, engine, audioEl, onAnim, prosody }) {
    const r = await speakRT({ text, voice, engine, prosody });
    const anim = { fps: r.fps, frames: r.frames, head: r.head, index: r.names.map((n, i) => [norm(n), i]) };
    shapeAnim(anim, engine);          // 엔진 출력 정규화 (입 벌림·편향 제거)
    anim.sentences = r.sentences;     // 문장 시작 시각 (없을 수 있음)
    if (onAnim) onAnim(anim);
    audioEl.src = r.audio_url;
    await audioEl.play();
    return anim;
  }
```

- [ ] **Step 3: `speakWithEmotion`이 LLM 감정을 쓰게 수정**

`speakWithEmotion` 본문을 교체:

```js
  async function speakWithEmotion({ text, emotion, autoEmo, emo, voice, engine, audioEl, onAnim }) {
    let segs = null;
    let r = emotion ? { emo: emotion, intensity: 0.9 } : null;
    if (!r && autoEmo) {
      // 직렬 호출: 목소리 톤(prosody)이 TTS 요청 파라미터라 감정을 먼저 알아야 한다.
      segs = await classifyEmotion(text);
      r = segs ? { emo: segs[0].emo, intensity: segs[0].intensity } : inferEmotion(text);
    } else if (!r) {
      r = inferEmotion(text);
    }
    let prosody = null;
    if (r && autoEmo) {
      emo.setEmotion(r.emo, r.intensity, false);   // 자동 감정 — 발화 끝나면 중립 복귀
      prosody = voiceProsody(r.emo, r.intensity);
    }
    const anim = await speakFlow({ text, voice, engine, audioEl, onAnim, prosody });
    // 문장별 감정 전환은 두 배열의 길이가 맞을 때만 (분할 결과가 같다는 전제 확인)
    if (segs && anim.sentences && anim.sentences.length === segs.length) {
      anim.emotionTrack = anim.sentences.map((s, i) => ({
        start: s.start, emo: segs[i].emo, intensity: segs[i].intensity }));
    }
    return anim;
  }
```

- [ ] **Step 4: export 목록에 추가**

```js
    norm, inferEmotion, classifyEmotion, voiceProsody, smoothStep, weightsFromAnim, shapeAnim,
```

- [ ] **Step 5: 회귀 확인 — 기존 테스트 전부**

Run: `PYTHONPATH= /home/ingon/face/.venv/bin/python -m pytest tests/ -q`
Expected: 16 passed (JS 변경이라 파이썬 테스트는 그대로 통과해야 한다 — 깨지면 잘못 건드린 것)

- [ ] **Step 6: 브라우저 스모크 — 발화가 여전히 되는지**

```bash
cd /home/ingon/face && PYTHONPATH= .venv/bin/python tools/channel_probe.py
```

Expected: 예외 없이 두 엔진 모두 수치 출력, `jawopen max >= 0.7`

- [ ] **Step 7: 커밋**

```bash
cd /home/ingon/face
git add static/avatar_core.js
git commit -m "feat(core): drive expression and voice from the LLM's reading of the text

speakWithEmotion now asks /api/emotion before synthesis instead of
regex-matching keywords (1/10 on the Korean bench). The call is serial
on purpose: prosody is a TTS request parameter, so classifying in
parallel would leave the voice flat while only the face emoted. Any
failure falls back to the old rule matcher, so speech survives Ollama
being down. Engine output is normalized on arrival and the sentence
track is attached for mid-utterance expression changes."
```

---

### Task 8: 문장별 표정 전환 (크로스페이드)

**Files:**
- Modify: `static/avatar_core.js` (`makeEmotion`)
- Modify: `static/studio3d.html` (렌더 루프)

**⚠️ Task 7 완료 후 착수한다.**

**Interfaces:**
- Consumes: `anim.emotionTrack` (Task 7)
- Produces: `emo.followTrack(track, tSec)` — 현재 재생 시각에 맞는 감정으로 0.25초 크로스페이드

- [ ] **Step 1: `makeEmotion`에 `followTrack` 추가**

`makeEmotion` 위에 상수를 둔다:

```js
  const CROSSFADE_S = 0.25;   // 문장 경계 표정 전환 시간
```

`makeEmotion` 안의 상태 선언(`let curKey = "neutral", curInt = 1;` 줄) 아래에 추가:

```js
    let trackSeg = null, fadeFrom = {}, fadeAt = 0;   // 문장별 전환용 크로스페이드 상태
```

반환 객체에 `followTrack`을 추가한다 (`setEmotion` 아래):

```js
      // 발화 중 문장이 바뀌면 표정도 바뀐다. track=[{start, emo, intensity}], tSec=audio.currentTime.
      // setEmotion 을 쓰지 않고 직접 섞는 이유: setEmotion 은 새 프리셋으로 통째 교체라
      // 경계에서 이전 표정이 한 프레임에 사라진다(그게 바로 없애려는 팝이다).
      // 문장이 바뀌는 순간의 표정을 박제해 두고 CROSSFADE_S 동안 새 프리셋과 겹쳐 넘긴다.
      followTrack(track, tSec) {
        if (!track || !track.length) return;
        let seg = track[0];
        for (const t of track) if (tSec >= t.start) seg = t;
        if (seg !== trackSeg) { fadeFrom = emotion; fadeAt = tSec; trackSeg = seg; }
        const k = Math.min(1, Math.max(0, (tSec - fadeAt) / CROSSFADE_S));
        const base = EMOTIONS[seg.emo] || EMOTIONS.neutral;
        const blended = {};
        for (const key in fadeFrom) blended[key] = fadeFrom[key] * (1 - k);
        for (const key in base) blended[key] = (blended[key] || 0) + base[key] * seg.intensity * k;
        emotion = blended;
        sticky = false; hold = 1;      // 발화 중 유지, 끝나면 기존대로 감쇠
        curKey = seg.emo; curInt = seg.intensity;
      },
```

버튼 하이라이트는 갱신하지 않는다 — 문장마다 버튼이 깜빡이면 오히려 산만하고,
발화가 끝나면 감정은 중립으로 감쇠하므로 표시가 남지 않는다.

- [ ] **Step 2: `studio3d.html` 렌더 루프에서 호출**

`emo.applyMax(...)` 호출 **직전**에 한 줄 추가:

```js
  if (anim && anim.emotionTrack && !audio.paused && !audio.ended) emo.followTrack(anim.emotionTrack, audio.currentTime);
  emo.applyMax(smooth, !!(anim && !audio.paused && !audio.ended));   // 발화 중이면 감정 유지, 유휴면 자동 감정 감쇠
```

- [ ] **Step 3: 브라우저 검증 — 두 문장에서 표정이 실제로 바뀌는지**

`/tmp/claude-1000/verify_track.py` 를 만들어 실행:

```python
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

CHROME = Path("/home/ingon/.cache/ms-playwright/chromium_headless_shell-1234"
              "/chrome-headless-shell-linux64/chrome-headless-shell")

async def main():
    async with async_playwright() as p:
        kw = {"executable_path": str(CHROME)} if CHROME.exists() else {}
        b = await p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"], **kw)
        pg = await b.new_page()
        await pg.goto("http://127.0.0.1:8000/3d")
        await pg.wait_for_function(
            "!document.getElementById('status').textContent.includes('로딩')", timeout=90000)
        await pg.evaluate("""() => {
          window.__seen = [];
          const c = AvatarCore.makeEmotion;   // 감정 전환 관찰용 훅
        }""")
        await pg.fill("#text", "오늘 정말 힘들었어요. 그래도 끝나서 정말 다행이에요!")
        await pg.click("#send")
        seen = []
        for _ in range(40):
            await pg.wait_for_timeout(250)
            st = await pg.evaluate("""() => {
              const a = document.getElementById('audio');
              return { t: a.currentTime, ended: a.ended };
            }""")
            seen.append(round(st["t"], 1))
            if st["ended"]:
                break
        track = await pg.evaluate("() => window.__lastTrack || null")
        print("emotionTrack:", track)
        await b.close()

asyncio.run(main())
```

확인을 위해 `studio3d.html` 의 `onAnim` 콜백에 임시로 `window.__lastTrack = a.emotionTrack;` 을 추가해 실행한 뒤, **확인이 끝나면 그 줄을 제거한다.**

Expected: `emotionTrack` 이 2개 원소, 첫째 `sad` 계열 / 둘째 `joy` 계열, 둘째 `start > 0`

- [ ] **Step 4: 커밋**

```bash
cd /home/ingon/face
git add static/avatar_core.js static/studio3d.html
git commit -m "feat(3d): change expression mid-utterance as sentences change

A 250 ms crossfade at each sentence boundary; the guard skips the
re-trigger when the emotion is unchanged, since setEmotion resets the
idle-decay hold every call."
```

---

### Task 9: 사본 동기화 · 감정 벤치 · 최종 검증

**Files:**
- Create: `tools/emotion_bench.py`
- Modify: `docs/avatar_core.js` (원본 복사), `/home/ingon/drawface-live/docs/avatar_core.js` (원본 복사)

**⚠️ Task 8 완료 후 착수한다.**

- [ ] **Step 1: 감정 벤치 스크립트 작성**

`tools/emotion_bench.py`:

```python
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
```

- [ ] **Step 2: 벤치 실행 — 수용 기준 8/10**

Run: `cd /home/ingon/face && PYTHONPATH= .venv/bin/python tools/emotion_bench.py`
Expected: `적중 8/10` 이상 (종료 코드 0)

미달이면 `CLASSIFY_SYSTEM` 프롬프트에 판단 힌트를 한 줄 추가하고 재실행한다. 예:
`"- 반문·추궁('~라고?', '~겠니')은 angry 로 본다."`

- [ ] **Step 3: 사본 3개 동기화**

```bash
cd /home/ingon/face
cp static/avatar_core.js docs/avatar_core.js
cp static/avatar_core.js /home/ingon/drawface-live/docs/avatar_core.js
sha256sum static/avatar_core.js docs/avatar_core.js /home/ingon/drawface-live/docs/avatar_core.js
```

Expected: 세 해시가 모두 같다

- [ ] **Step 4: 서버 기동 경고 없음 확인**

Run: `cd /home/ingon/face && PYTHONPATH= .venv/bin/python -c "import app"`
Expected: `avatar_core.js ... 불일치` 경고가 출력되지 않는다

- [ ] **Step 5: 전체 수용 기준 확인**

```bash
cd /home/ingon/face
PYTHONPATH= .venv/bin/python -m pytest tests/ -q          # 전체 테스트
PYTHONPATH= .venv/bin/python tools/emotion_bench.py       # 8/10 이상
PYTHONPATH= .venv/bin/python tools/channel_probe.py       # jawOpen >= 0.7, 편향 채널 확인
```

- [ ] **Step 6: 폴백 확인 — Ollama 없이도 발화되는지**

`OLLAMA_HOST`를 죽은 주소로 돌려 `/api/emotion`이 503을 내는 상태에서 발화가 되는지 본다:

```bash
cd /home/ingon/face && PYTHONPATH= .venv/bin/python -c "
import llm_source
llm_source.API = 'http://127.0.0.1:59999/api/chat'   # 죽은 포트
try:
    llm_source.classify(['문장 하나입니다.'])
except RuntimeError as e:
    print('예상된 실패:', e)
"
```

그리고 브라우저에서 `/3d` 발화가 정상 동작하는지(표정은 규칙 기준) 확인한다.

- [ ] **Step 7: 커밋**

```bash
cd /home/ingon/face
git add tools/emotion_bench.py docs/avatar_core.js
git commit -m "test(tools): emotion bench and sync the core copies

The bench is the acceptance gate for the classifier (rules scored 1/10;
the LLM path must clear 8/10) and stays as regression cover."
cd /home/ingon/drawface-live
git add docs/avatar_core.js
git commit -m "chore(vendor): sync avatar_core.js from talking-drawing-avatar"
```

---

## Self-Review (작성자 점검 결과)

**스펙 커버리지**

| 스펙 항목 | 담당 태스크 |
| --- | --- |
| §4A 감정 7종 통일 · `split_sentences` | Task 1 |
| §4A `classify()` · 스키마 · intensity 매핑 · 캐시 | Task 2 |
| §4A `POST /api/emotion` · 503 폴백 | Task 3 |
| §4D `shapeAnim()` · 베이스라인 · 게인 · kill | Task 4 |
| §6 채널 측정 스크립트 | Task 5 |
| §4C WordBoundary · 문장 시작 시각 · `sentences` | Task 6 |
| §4B 클라 직렬 호출 · 규칙 폴백 | Task 7 |
| §4C 문장별 크로스페이드 | Task 8 |
| §6 감정 벤치 · §7 사본 동기화 · 수용 기준 | Task 9 |

**미해결로 남기는 것** (스펙 §2 비목표와 일치)
- 문장별 **목소리** 톤 변화 — TTS를 한 번에 생성하므로 첫 문장 감정만 반영. 표정은 문장별로 바뀐다.
- 2D 퍼펫(`puppet.html`)은 `shapeAnim`·LLM 감정을 공용 코어를 통해 자동으로 받지만, 2D 전용 검증은 하지 않는다.
