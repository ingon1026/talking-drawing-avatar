"""사용자 발화 → 아바타 응답 (LG EXAONE 3.5 2.4B, Ollama 로컬 추론).

    chat(text, history) -> {"reply": str, "emotion": str}

emotion 은 avatar_core.js 의 EMOTIONS 키와 같은 7종(neutral/joy/sad/angry/surprise/fear/shy)이라
클라이언트가 그대로 표정 프리셋으로 쓴다. reply 는 TTS 로 읽히므로 짧은 한국어 구어체
1~2문장이며, 이모지·마크다운은 프롬프트로 막고 남으면 _clean() 이 걷어낸다.

모델 선정: 같은 프롬프트로 qwen2.5:3b 와 비교했을 때 exaone3.5:2.4b 만 존댓말·사실
관계·공감이 모두 안정적이었고 VRAM 도 작았다(1.8GB vs 2.3GB). VRAM 은 face-avatar 의
NeuroSync·A2F-3D 와 12GB 를 나눠 쓰므로 num_ctx 2048 로 KV 캐시를 눌러 둔다.
keep_alive 로 10분 상주시켜 매 대화마다 로딩하지 않는다.
"""
import json
import re
import sys
import time
from functools import lru_cache

import requests

API = "http://localhost:11434/api/chat"
MODEL = "exaone3.5:2.4b"
KEEP_ALIVE = -1   # 상시 상주 — 유휴 언로드 후 첫 대화 ~3s 재로드 제거 (VRAM 1.8GB, 여유 충분)
NUM_CTX = 2048       # KV 캐시 = VRAM. 대화 히스토리 몇 턴에 충분하다.
HISTORY_TURNS = 6    # 최근 메시지 6개(=3턴)만 넘겨 컨텍스트·지연을 묶어 둔다.
TIMEOUT = 60

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
    # 마지막 조각도 짧으면 앞으로 접는다 (앞 병합 루프는 뒤로만 흡수하므로 꼬리는 못 잡는다)
    if len(merged) > 1 and len(merged[-1]) < MIN_SENTENCE_CHARS:
        tail = merged.pop()
        merged[-1] += " " + tail
    if len(merged) > MAX_SENTENCES:
        merged = merged[:MAX_SENTENCES - 1] + [" ".join(merged[MAX_SENTENCES - 1:])]
    return merged

# format 을 스키마로 주면 Ollama 가 문법 제약 디코딩을 건다. format:"json" 만 쓰면 2.4B 가
# 제약을 빈 객체 {} 로 만족시켜 버리는 일이 실측 8회 중 2회 나왔다(스키마로는 0회).
SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "emotion": {"type": "string", "enum": list(EMOTIONS)},
    },
    "required": ["reply", "emotion"],
}

# 캐릭터별 정체성(persona)은 manifest 에서 오고, 없으면 기본 정체성을 쓴다.
IDENTITY_DEFAULT = "너는 사용자가 그린 그림에서 태어난 친근한 아바타야. 사용자와 음성으로 대화한다."

# 정체성 뒤에 붙는 공통 규칙 — 캐릭터가 바뀌어도 말투 규칙·출력 형식은 고정.
RULES = (
    "\n규칙:\n"
    "- 위 성격을 대화 내내 일관되게 연기한다. 하지만 아래 규칙은 성격보다 우선한다.\n"
    "- 반드시 한국어 존댓말('~요', '~예요')로 답한다. 반말('~야', '~어')은 절대 쓰지 않는다.\n"
    "  (사용자가 반말로 물어도 아바타는 존댓말로 답한다.)\n"
    "- 답변은 1~2문장, 60자 이내로 매우 짧게 한다. 길게 설명하지 않는다.\n"
    "- 이모지, 마크다운, 목록, 특수기호를 절대 쓰지 않는다.\n"
    f"- 응답의 감정을 {', '.join(EMOTIONS)} 중 하나로 고른다.\n"   # EMOTIONS 단일 출처
    "  감정이 뚜렷하지 않으면 neutral 을 쓴다.\n"
    'JSON 한 개만 출력한다: {"reply": "<응답>", "emotion": "<감정>"}'
)


def _system(persona: str | None) -> str:
    return (persona.strip() if persona else IDENTITY_DEFAULT) + RULES

# TTS 가 읽을 수 없는 것들: 이모지/픽토그램(비BMP 대부분), 마크다운 기호.
_DROP = re.compile(r"[\U00010000-\U0010ffff☀-➿*_`#>\[\]]")


# 소형 모델(2.4B)이 가끔 존댓말 종결어미를 겹쳐 뱉는다("좋아해요요"). 문장 경계의 요/죠 중복만 접는다.
# ponytail: 낱말 '요요(장난감)'가 문장 끝일 때만 오검출 — 드물어 감수. '네네' 같은 맞장구는 제외해 보존.
_DUP_END = re.compile(r"([요죠])\1+(?=[.!?…\s\"'”’)]|$)")


def _clean(s: str) -> str:
    """프롬프트를 뚫고 나온 이모지·마크다운·줄바꿈을 걷어내 음성으로 읽을 수 있게 만든다."""
    s = re.sub(r"\s+", " ", _DROP.sub("", str(s))).strip()
    return _DUP_END.sub(r"\1", s)


def chat(text: str, history: list[dict] | None = None, persona: str | None = None) -> dict:
    """사용자 발화 → {"reply": 한국어 응답, "emotion": EMOTIONS 중 하나}.

    persona: 선택된 캐릭터의 성격 한 줄(manifest.persona). 없으면 기본 정체성.
    """
    msgs = [{"role": "system", "content": _system(persona)}]
    for m in (history or [])[-HISTORY_TURNS:]:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            msgs.append({"role": m["role"], "content": str(m["content"])})
    msgs.append({"role": "user", "content": text})

    try:
        r = requests.post(
            API,
            json={
                "model": MODEL,
                "messages": msgs,
                "stream": False,
                "format": SCHEMA,
                "keep_alive": KEEP_ALIVE,
                "options": {"num_ctx": NUM_CTX, "temperature": 0.7, "num_predict": 120},
            },
            timeout=TIMEOUT,
        )
    except requests.exceptions.ConnectionError:
        raise RuntimeError("대화 서버(Ollama)가 꺼져 있어요. `ollama serve` 로 켜 주세요.")
    except requests.exceptions.Timeout:
        raise RuntimeError("대화 모델의 응답이 너무 늦어요. 잠시 뒤 다시 말 걸어 주세요.")

    if r.status_code == 404:
        raise RuntimeError(f"대화 모델이 없어요. `ollama pull {MODEL}` 로 받아 주세요.")
    if not r.ok:
        raise RuntimeError(f"대화 모델이 응답하지 못했어요. (Ollama {r.status_code})")

    content = r.json().get("message", {}).get("content", "")

    # 스키마를 걸어도 파싱은 방어한다 → 실패하면 본문을 그대로 읽어 주는 쪽으로 폴백.
    # 단 num_predict 로 잘린 JSON 은 본문이 '{"reply": "...' 라 그대로 읽히면 안 된다.
    try:
        d = json.loads(content)
        reply, emotion = _clean(d.get("reply", "")), d.get("emotion")
    except (json.JSONDecodeError, AttributeError):
        reply = "" if content.lstrip().startswith("{") else _clean(content)
        emotion = None

    if not reply:
        reply = "잘 못 알아들었어요. 다시 말해 줄래요?"
    return {"reply": reply, "emotion": emotion if emotion in EMOTIONS else "neutral"}


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
    "- 문장을 다시 쓰거나 설명하지 않는다. 감정만 판단한다.\n"
    # 아래 세 줄은 벤치(tools/emotion_bench.py) 8/10 미달 원인이었던 오분류 패턴에 대한 힌트다.
    "- 반문·추궁('~라고?', '~겠니')은 angry 로 본다.\n"
    "- 의욕·기력을 잃은 상태('그냥...', '아무것도 하기 싫다')는 sad 로 본다. shy 는 칭찬이나"
    " 관심을 받아 겸연쩍을 때만 쓴다.\n"
    "- 정체를 알 수 없는 존재·위협을 의심하는 불안('~것 같아', 인기척·기척)은 fear 로 본다."
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
    except requests.exceptions.RequestException:
        # 위 두 예외의 상위 클래스 — 반드시 이 자리(마지막)에 둔다. 이 GPU 는 Ollama·
        # NeuroSync·A2F-3D 가 12GB VRAM 을 나눠 쓰므로 Ollama 가 응답 도중 OOM-kill
        # 되면 ConnectionError 가 아니라 ChunkedEncodingError 같은 형태로 나온다.
        raise RuntimeError("감정 분류 요청이 실패했어요.")
    if not r.ok:
        raise RuntimeError(f"감정 분류에 실패했어요. (Ollama {r.status_code})")

    try:
        content = r.json().get("message", {}).get("content", "")
        if not isinstance(content, str):
            raise TypeError
        arr = json.loads(content).get("emotions", [])
    except (json.JSONDecodeError, AttributeError, TypeError):
        raise RuntimeError("감정 분류 응답을 읽지 못했어요.")
    # 스키마를 걸어도 형태를 방어한다 — 배열이 아니거나 원소가 객체가 아니면
    # 아래 len()/e.get() 이 RuntimeError 밖에서 TypeError/AttributeError 로 터진다.
    if not isinstance(arr, list):
        raise RuntimeError("감정 분류 응답을 읽지 못했어요.")
    if len(arr) != len(sentences):
        raise RuntimeError("감정 분류 결과 개수가 문장 수와 다릅니다.")

    out = []
    for e in arr:
        if not isinstance(e, dict):
            raise RuntimeError("감정 분류 응답을 읽지 못했어요.")
        emo = e.get("emotion")
        try:
            intensity = INTENSITY.get(e.get("intensity"), 0.70)
        except TypeError:
            # intensity 가 list/dict 등 해시 불가 타입으로 오면 dict.get() 이 여기서
            # TypeError 로 터진다 — RuntimeError 로 감싸야 /api/emotion 이 500 이 아니라
            # 503(폴백)으로 나간다.
            raise RuntimeError("감정 분류 응답을 읽지 못했어요.")
        out.append({"emo": emo if emo in EMOTIONS else "neutral", "intensity": intensity})
    return out


@lru_cache(maxsize=64)
def classify_cached(sentences: tuple[str, ...]) -> tuple[dict, ...]:
    """같은 문장 묶음 재요청은 모델을 다시 부르지 않는다 (쇼케이스·반복 시연)."""
    return tuple(classify(list(sentences)))


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "안녕?"
    persona = sys.argv[2] if len(sys.argv) > 2 else None   # python llm_source.py "질문" "성격"
    t0 = time.perf_counter()
    out = chat(q, persona=persona)
    print(f"Q: {q}\nA: {out['reply']}\n   emotion={out['emotion']}  "
          f"{len(out['reply'])}자  {time.perf_counter() - t0:.2f}s")
