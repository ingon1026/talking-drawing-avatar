"""감정 분류 정확도 벤치 — 규칙(1/10) 대비 LLM 이 얼마나 맞히는지.

    PYTHONPATH= .venv/bin/python tools/emotion_bench.py

Ollama 가 떠 있어야 한다. CI 대상이 아니라 회귀 감시용 수동 실행 도구다.

Task-9 리뷰에서 발견된 문제: 원래 CLASSIFY_SYSTEM 힌트 3줄이 이 파일의 원본 10문장을
글자 그대로 인용하고 있었다 — "정답을 프롬프트에 박아 넣은" 것과 같아서, 벤치가 튜닝
셋이자 채점 기준을 동시에 겸하고 있었다. 힌트에서 인용구만 빼면 6/10 으로 떨어지고,
같은 감정을 다른 말로 쓴 held-out 문장 7개는 4/7·3/7 로 흔들렸다 — 8/10 이 "한국어
감정문 80% 를 맞힌다"는 뜻이 아니었다는 증거.

그래서 이 파일은 원본 10개(tag=ORIG, 절대 안 바뀜 — 과거 6~8/10 기록과 비교 가능해야
한다)에 held-out 7개(tag=HELD, 감정마다 1개씩, 원본과 다른 표현)와 추가 신규 12개
(tag=NEW, 감정마다 여러 표현)를 더해 총 29개로 늘렸다. 신규 19개(HELD+NEW)는 감정별로
묶어서 나열하지 않고 라운드로빈으로 섞었다 — 같은 감정이 연달아 나오면 모델이 "직전
문장과 같은 라벨"로 찍는 인접성 크러치를 쓸 수 있어서다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import llm_source

ORIG = "orig"   # 원본 10개 — 과거 기록(6~8/10)과 비교하려면 이 10개는 절대 손대면 안 된다
HELD = "held"   # held-out 7개 — 감정마다 1개, 원본과 다른 말투/소재로 다시 씀
NEW = "new"     # 추가 신규 12개 — 감정마다 여러 표현으로 커버리지를 넓힘

# 원본 10개 — tools/emotion_bench.py 최초본(브리프) 그대로. 순서·문장 모두 고정.
_ORIGINAL = [
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

# held-out 7개 + 신규 12개, 감정별 라운드로빈으로 미리 섞어 둔 순서.
# (문장, 정답감정, tag)
_HELD_AND_NEW = [
    ("다음 정류장은 시청역입니다.", "neutral", HELD),
    ("이렇게 기쁜 날이 다시 올까 싶을 정도로 행복해요.", "joy", HELD),
    ("마음이 무너져서 아무것도 손에 안 잡혀요.", "sad", HELD),
    ("정말 화가 나서 참을 수가 없어요.", "angry", HELD),
    ("세상에, 여기서 이런 걸 다 보네요.", "surprise", HELD),
    ("혼자 있는데 자꾸 등골이 오싹해져요.", "fear", HELD),
    ("다들 쳐다보니까 얼굴이 화끈거려요.", "shy", HELD),
    ("이 서류는 팀장님께 전달하면 됩니다.", "neutral", NEW),
    ("오랜만에 여행을 가게 되어 마음이 들떠요.", "joy", NEW),
    ("정든 동네를 떠난다고 생각하니 마음이 허전해요.", "sad", NEW),
    ("새치기하는 사람을 보니까 저도 모르게 화가 났어요.", "angry", NEW),
    ("생일도 아닌데 케이크를 준비해줘서 놀랐어요.", "surprise", NEW),
    ("밤늦게 혼자 주차장을 걸을 때마다 오싹해요.", "fear", NEW),
    ("모두 앞에서 발표를 하려니 목소리가 떨려요.", "shy", NEW),
    ("다음 회차 방송은 다음 주 화요일입니다.", "neutral", NEW),
    ("빌린 물건을 망가뜨려 놓고 미안하단 말도 없어서 화가 나요.", "angry", NEW),
    ("엘리베이터 문이 열리자마자 사람들이 다 나를 보고 있어서 놀랐어요.", "surprise", NEW),
    ("높은 다리 위에서 아래를 내려다보니 다리가 풀려요.", "fear", NEW),
    ("선물을 받으니까 뭐라고 해야 할지 몰라 머쓱해요.", "shy", NEW),
]

CASES = [(text, want, ORIG) for text, want in _ORIGINAL] + _HELD_AND_NEW

# classify() 를 29문장 한 번에 부르면 두 가지가 실사용과 어긋난다: (1) 실제 서비스는
# 발화 하나(1~3문장, llm_source.MAX_SENTENCES 개)당 한 번만 부르므로 29개를 한 번에 넣는 것 자체가
# 다른 과제(위치 드리프트, 옆 문장 라벨 베끼기)를 측정하게 된다. (2) 청크를 9~10개로
# 실측했더니 모델이 가끔 요청한 문장 수보다 한 개 많은(또는 적은) 라벨을 내놓는
# 불안정성이 나왔다(done_reason 은 "stop" — 컨텍스트 잘림이 아니라 모델 자체의 개수
# 실수). 5개 이하로 자르면 같은 실측에서 불일치가 0 이었다.
#
# 그래서 원본 10개는 과거 기록(6~8/10)과 그대로 비교하려고 예전처럼 한 번에 부르고
# (실측 5회 모두 개수 일치), held+new 19개는 5개씩 잘라 부른다.
CHUNK_SIZE = 5


RETRIES = 2   # 개수 불일치는 실측상 컨텍스트 잘림이 아니라 모델이 가끔 문장 하나를
              # 더/덜 세는 실수였다(done_reason=stop) — 같은 청크를 한 번 더 물어보면
              # 대개 맞는다. 라벨을 바꾸는 게 아니라 실패한 호출을 다시 하는 것뿐이라
              # "정답에 맞춰 벤치를 고친다"는 금지 규칙과는 다른 문제다.


def _classify_chunk(sentences: list[str]) -> list[dict]:
    last_err: RuntimeError | None = None
    for _ in range(RETRIES + 1):
        try:
            return llm_source.classify(sentences)
        except RuntimeError as e:
            last_err = e
    raise last_err


def _classify_all(cases: list[tuple[str, str, str]]) -> list[dict]:
    # cases 는 이미 [원본 10개] + [held+new 19개] 순서로 고정돼 있다(CASES 정의부 참고) —
    # 청크로 나눠 불러도 이 순서 그대로 이어붙이면 main() 의 zip(CASES, labels) 이 맞는다.
    original = [c for c in cases if c[2] == ORIG]
    rest = [c for c in cases if c[2] != ORIG]
    out: list[dict] = []
    if original:
        out.extend(_classify_chunk([t for t, _, _ in original]))
    for i in range(0, len(rest), CHUNK_SIZE):
        chunk = rest[i:i + CHUNK_SIZE]
        out.extend(_classify_chunk([t for t, _, _ in chunk]))
    return out


def main():
    labels = _classify_all(CASES)

    # 감정 x {원본, 신규(held+new)} 교차표 — 합계만 보면 "angry 신규는 잘 맞는데
    # 원본 2개는 여전히 0/2" 같은 상황이 평균 뒤에 숨는다.
    per_emotion: dict[str, dict[str, list[int]]] = {
        e: {"orig": [0, 0], "new": [0, 0]} for e in llm_source.EMOTIONS
    }
    held_hit = held_total = 0   # held는 per_emotion의 "new" 버킷(held+new)에 섞여 있어 따로 셀 수밖에 없다

    for (text, want, tag), got in zip(CASES, labels):
        ok = int(got["emo"] == want)
        print(f"{'O' if ok else 'X'}  [{tag:4s}] 기대={want:9s} 결과={got['emo']:9s} "
              f"({got['intensity']:.2f}) | {text}")

        bucket = "orig" if tag == ORIG else "new"
        per_emotion[want][bucket][0] += ok
        per_emotion[want][bucket][1] += 1

        if tag == HELD:
            held_hit += ok
            held_total += 1

    print("\n감정별 적중 (원본 / 신규=held+new):")
    for e in llm_source.EMOTIONS:
        o_h, o_t = per_emotion[e]["orig"]
        n_h, n_t = per_emotion[e]["new"]
        print(f"  {e:9s} 원본 {o_h}/{o_t}   신규 {n_h}/{n_t}")

    # orig/new 합계는 per_emotion 교차표에서 그대로 파생된다 — 별도 누산기로 중복 셀 필요 없다.
    orig_hit = sum(per_emotion[e]["orig"][0] for e in llm_source.EMOTIONS)
    orig_total = sum(per_emotion[e]["orig"][1] for e in llm_source.EMOTIONS)
    new_hit = sum(per_emotion[e]["new"][0] for e in llm_source.EMOTIONS)
    new_total = sum(per_emotion[e]["new"][1] for e in llm_source.EMOTIONS)

    total_hit = orig_hit + new_hit
    total = orig_total + new_total
    print(f"\n전체            {total_hit}/{total}")
    print(f"원본 10개       {orig_hit}/{orig_total}")
    print(f"신규 19개       {new_hit}/{new_total}  (그 중 held-out 7개 {held_hit}/{held_total})")


if __name__ == "__main__":
    main()
