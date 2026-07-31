# 텍스트 감정 연계 + 입모양 정상화 — 설계

작성 2026-07-31 · 대상 `~/face` (talking-drawing-avatar) · 상태: 승인됨

## 1. 배경 — 측정된 문제

`/3d`(NVIDIA 석고 헤드)에서 8초 발화를 구동해 채널값 210프레임을 실측했다.

### 1-1. 감정이 사실상 항상 중립

직접 입력("말하기") 경로는 `avatar_core.inferEmotion()` 정규식 키워드 매칭을 쓴다.
한국어 10문장 벤치에서 **적중 1/10**, 그마저 중립 대조군이었다.

| 입력 | 기대 | 실제 |
| --- | --- | --- |
| 이걸 지금 말이라고 하는 거야? | angry | neutral |
| 그냥... 아무것도 하기 싫다 | sad | **angry** (`싫다`만 보고) |
| 와 진짜 대박이다! | joy | surprise |
| 오늘 회의는 3시입니다 | neutral | neutral ✅ |

반면 **대화 모드(`/api/chat`)는 이미 LLM이 `{reply, emotion}`을 반환**해 정상 동작한다.
즉 고장난 것은 직접 입력 경로 하나다.

### 1-2. 입이 거의 벌어지지 않음

| 채널 | A2F-3D | NeuroSync | 기대 |
| --- | --- | --- | --- |
| `jawOpen` | 최대 0.399 / 평균 0.124 | 최대 0.310 / 평균 0.066 | 개모음에서 0.6~0.8 |
| `jawRight` | 평균 0.170 (상시 우측 쏠림) | — | ~0 |
| `mouthFunnel` | 0.398 | 최대 0.781 (편중) | 상황별 |
| `browInnerUp` | — | 평균 0.145 (상시) | 0 |

`weightsFromAnim()`이 엔진 출력을 게인 없이 그대로 모프에 전달한다 — 조정 지점이 없다.
A2F 쪽 세기 파라미터를 찾았으나 `model.json`은 경로 설정뿐이고 exporter 인자도
`<model.json> --serve <fps>`가 전부라, **엔진 재설정으로는 해결 불가**.

### 1-3. 감정 집합 불일치

`llm_source.EMOTIONS`는 5종(`neutral/joy/sad/angry/surprise`)인데
`avatar_core.EMOTIONS` 얼굴 프리셋은 7종(`fear`·`shy` 추가)이다. LLM은 두 표정을 영영 못 쓴다.

## 2. 목표 / 비목표

**목표**
- 텍스트의 감정을 LLM이 읽어 표정·목소리에 반영 (직접 입력 경로)
- 문장이 바뀌면 표정도 바뀜
- 발화 중 입이 실제로 벌어짐

**비목표** (이번 범위 아님)
- TTS 음성 엔진 교체·발화 속도 조정
- 새 감정 분류 모델 도입 (기존 Ollama LLM 재사용)
- 실시간 스트리밍 발화 구조 변경
- 2D 퍼펫 경로의 별도 수정 — 공용 코어(`avatar_core.js`) 변경분은 자동 적용되며, 그 이상은 손대지 않는다

## 3. 아키텍처

```
텍스트
  │
  ├─① POST /api/emotion  → segments[{text, emo, intensity}]      (+0.78초)
  │        │
  │        └→ 첫 문장 감정 → voiceProsody(JS) ─┐
  │                                            ↓
  └─② POST /api/speak_rt {text, prosody} → TTS(edge-tts, stream)
              ├→ WordBoundary → 문장 시작 시각 → sentences[{text, start}]
              └→ wav → A2F / NeuroSync → frames
                          ↓
              shapeAnim(정규화·증폭)     ← 클라, 발화당 1회
                          ↓
     렌더 루프: audio.currentTime 으로 문장 감정 전환 + 모프 적용
```

①과 ②는 **직렬**이다(②가 ①의 결과인 prosody를 필요로 한다).
기존 대화 모드는 `/api/chat`이 이미 감정을 주므로 ①을 건너뛴다 — **추가 지연 0**.

`segments`(①)와 `sentences`(②)는 **같은 `split_sentences()`** 로 쪼개므로 인덱스가 일치한다.
길이가 다르면 문장별 전환을 포기하고 발화 전체에 첫 감정 하나를 쓴다.

## 4. 컴포넌트

### A. 서버 — 감정 분류

**`llm_source.py`**

```python
EMOTIONS = ("neutral", "joy", "sad", "angry", "surprise", "fear", "shy")  # 5종 → 7종

def classify(sentences: list[str]) -> list[dict]:
    """문장 리스트 → [{"emo": str, "intensity": float}] (입력과 같은 길이)"""
```

- 문장 분할은 **서버가 먼저** 하고 LLM에는 분류만 시킨다. LLM이 텍스트를 재작성해
  원문과 어긋나는 사고를 원천 차단한다.
- Ollama 스키마 제약 디코딩 — 기존 `chat()`에서 검증된 패턴을 그대로 쓴다:

```python
SCHEMA = {"type": "object", "properties": {"emotions": {"type": "array", "items": {
    "type": "object",
    "properties": {"emotion": {"type": "string", "enum": list(EMOTIONS)},
                   "intensity": {"type": "string", "enum": ["low", "mid", "high"]}},
    "required": ["emotion", "intensity"]}}}, "required": ["emotions"]}
```

- `intensity`는 **문자열 enum**으로 받는다. 2.4B 모델에 0~1 실수를 시키면 값이 튄다.
  서버에서 `low=0.45 / mid=0.70 / high=1.0`으로 매핑.
- 반환 길이가 입력 문장 수와 다르면 실패로 처리(폴백).
- 타임아웃 8초, 결과 LRU 캐시 64개.

**문장 분할** (`_split_sentences`): `(?<=[.!?…])\s+` 기준, 10자 미만 조각은 앞 문장에 병합,
최대 8문장(초과분은 마지막 문장에 병합) — LLM 출력 길이를 묶어 지연을 예측 가능하게 한다.

**`app.py`**

```
POST /api/emotion   {"text": str}
 200 {"segments": [{"text": str, "emo": str, "intensity": float}]}
 503 LLM 미가동 — 클라이언트가 규칙 폴백으로 진행 (에러 표시하지 않음)
```

### B. 클라이언트 — 감정 배선

**`avatar_core.js`**

```js
async function classifyEmotion(text, { timeoutMs = 1500 } = {})
// → [{text, emo, intensity}] | null   (null 이면 호출측이 inferEmotion 규칙 사용)
```

- `speakWithEmotion()`에서 `emotion` 인자가 없고 `autoEmo`가 켜져 있으면 호출한다.
- **`speakFlow`보다 먼저(직렬) 호출한다.** 목소리 톤(`prosody`)이 `/api/speak_rt`의 요청
  파라미터라 감정을 모르면 TTS를 만들 수 없다 — 병렬로 돌리면 얼굴에만 감정이 실리고
  목소리는 무표정으로 남는다.
- **측정된 비용: 발화 시작 +0.78초**(Ollama 웜, `keep_alive` 상주 기준. 콜드 첫 호출 3.5초).
  현재 발화 시작까지 ~1.5초 → ~2.3초가 된다. 목소리·표정을 함께 얻는 대가로 수용한다.
- 왕복이 2회(감정 → 발화)인 이유: 프로소디 계산(`voiceProsody`)이 JS에 있고, 서버리스
  데모 페이지(`docs/index.html`)도 같은 함수를 쓴다. 서버가 프로소디를 계산하게 하면
  같은 표를 파이썬에 복제해야 하므로, 왕복 1회를 더 쓰는 쪽이 변경이 작다
  (localhost 왕복 ≈ 5ms).
- 실패·타임아웃·503 → 기존 `inferEmotion()` 결과 사용. **오프라인에서 현재보다 나빠지지 않는다.**
- 목소리 톤은 **첫 문장** 감정으로 결정한다. TTS를 한 번에 생성하므로 문장별 톤 변화는
  범위 밖(표정은 문장별로 바뀐다 — §4C).
- **대화 모드는 영향 없다** — `/api/chat`이 이미 감정을 주므로 이 경로를 타지 않는다(추가 0초).

### C. 문장별 감정 전환

**`app.py` — TTS를 stream 으로 전환**

`edge_tts.Communicate(...).save()` → `.stream()`으로 바꾸고 `WordBoundary` 청크를 수집한다.

```python
# chunk: {"type": "WordBoundary", "offset": <100ns 단위>, "duration": ..., "text": "..."}
```

문장 시작 시각은 각 문장의 첫 단어 offset(초 = `offset / 1e7`). 단어와 문장의 대응은
누적 문자 길이로 매칭한다.

`/api/speak_rt` 응답에 추가 (감정은 ①의 `segments`에 있으므로 여기엔 시각만 싣는다):

```json
"sentences": [{"text": "오늘 정말 힘들었어요.", "start": 0.0},
              {"text": "그래도 끝나서 다행이에요!", "start": 2.4}]
```

문장 분할은 `llm_source.split_sentences()`를 그대로 쓴다 — ①과 같은 함수라 인덱스가 맞는다.
`llm_source` 임포트가 실패하면 `sentences`를 생략한다(발화는 정상 진행).

**`avatar_core.js` — 시간축 적용**

렌더 루프에서 `audio.currentTime`으로 현재 문장을 찾아 감정을 적용하고,
경계에서 **0.25초 크로스페이드**로 표정을 섞는다(팝 방지). 문장 정보가 없으면
기존처럼 발화 전체에 감정 하나(현행 동작 유지).

### D. 입 셰이핑 레이어

**`avatar_core.js`**

```js
function shapeAnim(anim, engine)   // anim.frames 를 제자리 정규화하고 anim 을 반환
```

`speakFlow`가 `anim`을 받은 **직후 1회** 실행한다(발화 전체 프레임을 미리 받는 구조라 가능).
8초 × 60fps × 52채널 = 25k 연산으로 즉시 끝난다.

입력 자료구조(기존 그대로):

```js
anim = { fps, frames: [[...52개 float], ...], head, index: [[normalizedName, col], ...] }
```

채널 이름 → 열 번호는 `anim.index`로 찾는다. 이름은 이미 `norm()`을 거친 소문자
(`jawopen`·`browinnerup` …)이므로 프로파일 키도 소문자로 쓴다.

채널별 처리 순서:

1. **베이스라인 제거** — 그 채널 전체 프레임의 10퍼센타일을 빼고 0에서 클램프.
   "발화 내내 켜져 있는" 편향만 사라진다(NeuroSync 눈썹·눈 들림, A2F 턱 쏠림).
2. **게인** — 엔진별 프로파일을 곱하고 0~1 클램프.
3. **kill 채널** — 0으로 고정.

```js
const SHAPE = {
  a2f:       { gain: { jawopen: 1.9 }, kill: ["jawright", "jawleft"] },
  neurosync: { gain: { jawopen: 2.4, mouthfunnel: 0.7 }, kill: [] },
};
const BASELINE_PCT = 10;
```

게인 근거(실측): A2F `0.399 × 1.9 = 0.76`, NeuroSync `0.310 × 2.4 = 0.74` — 둘 다 목표 0.7 이상.
NeuroSync의 과한 오므림은 `0.781 × 0.7 = 0.55`로 완화.

## 5. 에러 처리

| 상황 | 동작 |
| --- | --- |
| Ollama 꺼짐 / 모델 없음 | `/api/emotion` 503 → 클라 규칙 폴백, **사용자에게 에러 표시 안 함** |
| LLM 응답 길이 불일치·파싱 실패 | 서버가 503, 위와 동일 |
| 감정 호출 1.5초 초과 | 규칙 폴백으로 즉시 진행 (발화를 지연시키지 않는다) |
| WordBoundary 미수집 | `sentences` 생략 → 클라는 발화 전체 감정 1개 (현행 동작) |
| 알 수 없는 엔진 이름 | `shapeAnim`은 원본 그대로 반환 (무변형) |

원칙: **모든 신규 기능은 실패 시 현행 동작으로 떨어진다.** 어떤 실패도 발화를 막지 않는다.

## 6. 테스트

이 저장소에는 아직 `tests/`가 없다. 이번에 새로 만들고 `pytest`로 돌린다
(`PYTHONPATH= .venv/bin/python -m pytest tests/ -q`; `pytest`가 없으면 venv에 추가).

- `tests/test_emotion_classify.py` — 문장 분할 규칙, intensity 매핑, 길이 불일치 폴백,
  LLM 미가동 시 503 (Ollama 호출은 목으로 대체 — 네트워크 의존 없음)
- `tests/test_shape_anim.py` — 베이스라인 제거·게인·클램프·kill·미지 엔진 무변형.
  JS 함수이므로 Playwright로 브라우저에서 `AvatarCore.shapeAnim()`을 직접 호출해 검증한다
  (헤드리스 크로미움 경로는 `~/.cache/ms-playwright/chromium_headless_shell-1234/...`).
- **감정 벤치 스크립트** `tools/emotion_bench.py` — 위 10문장 + 확장 세트로 적중률 출력.
  회귀 감시용으로 남긴다 (Ollama 필요, CI 대상 아님)
- **채널 측정 스크립트** `tools/channel_probe.py` — 발화 구동 후 채널 최대·평균 출력.
  `jawOpen` 목표 달성 확인용

## 7. 수용 기준

- [ ] 감정 벤치 10문장 **8/10 이상** (현재 1/10)
- [ ] 직접 입력 발화의 감정 분류 추가 지연 **1.0초 이하** (웜 실측 0.78초)
- [ ] `jawOpen` 최대 **0.7 이상** — A2F·NeuroSync 양쪽
- [ ] NeuroSync `browInnerUp` 평균 **0.05 이하** (현재 0.145)
- [ ] A2F `jawRight` 평균 **0.02 이하** (현재 0.170)
- [ ] 문장이 2개 이상인 발화에서 표정이 문장 경계에서 바뀐다
- [ ] Ollama를 끈 상태에서도 발화가 정상 동작한다 (규칙 폴백)
- [ ] 기존 대화 모드 무회귀 — `/api/chat` 감정 경로는 그대로
- [ ] `static/avatar_core.js`와 `docs/avatar_core.js` 사본 동기화 (기동 시 해시 검사가 있음)

## 8. 작업 분담

| 팀 | 범위 | 파일 |
| --- | --- | --- |
| A | 감정 분류 서버 — `classify()`, 7종 확장, `/api/emotion`, 문장 분할, 캐시, 테스트 | `llm_source.py` · `app.py` · `tests/` |
| B | 입 셰이핑 — `shapeAnim()` + 프로파일 + 측정 스크립트 + 테스트 | `static/avatar_core.js` · `tools/` · `tests/` |
| C | 문장 타이밍 — edge-tts stream 전환, WordBoundary 수집, `speak_rt` 응답 확장 | `app.py` |
| 통합 | 클라 배선(`classifyEmotion`·병렬 호출·크로스페이드), `docs/` 사본 동기화, 전체 검증 | `avatar_core.js` · `studio3d.html` |

A·B·C는 서로 다른 파일을 만지므로 병렬 가능하다. C와 A는 `app.py`를 공유하므로
**C는 A 완료 후 착수**하거나 별도 워크트리에서 작업 후 순차 머지한다.
