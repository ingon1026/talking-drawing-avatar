"""사용자 발화 → 아바타 응답 (LG EXAONE 3.5 2.4B, Ollama 로컬 추론).

    chat(text, history) -> {"reply": str, "emotion": str}

emotion 은 avatar_core.js 의 EMOTIONS 키와 같은 5종(neutral/joy/sad/angry/surprise)이라
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

import requests

API = "http://localhost:11434/api/chat"
MODEL = "exaone3.5:2.4b"
KEEP_ALIVE = "10m"   # 첫 호출 후 상주 (대화 도중 언로드 방지)
NUM_CTX = 2048       # KV 캐시 = VRAM. 대화 히스토리 몇 턴에 충분하다.
HISTORY_TURNS = 6    # 최근 메시지 6개(=3턴)만 넘겨 컨텍스트·지연을 묶어 둔다.
TIMEOUT = 60

EMOTIONS = ("neutral", "joy", "sad", "angry", "surprise")

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


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "안녕?"
    persona = sys.argv[2] if len(sys.argv) > 2 else None   # python llm_source.py "질문" "성격"
    t0 = time.perf_counter()
    out = chat(q, persona=persona)
    print(f"Q: {q}\nA: {out['reply']}\n   emotion={out['emotion']}  "
          f"{len(out['reply'])}자  {time.perf_counter() - t0:.2f}s")
