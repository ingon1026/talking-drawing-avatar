"""문장별 감정 파이프라인 점검 — 텍스트 → 감정 → 목소리 톤 + 표정 채널을 한 표로 본다.

    PYTHONPATH= .venv/bin/python tools/emotion_pipeline_check.py
    PYTHONPATH= .venv/bin/python tools/emotion_pipeline_check.py "문장1. 문장2."

서버가 http://127.0.0.1:8000 에 떠 있어야 한다(그 뒤로 Ollama 도). CI 대상이 아니라
"바꾼 게 실제로 끝단까지 흐르는가"를 눈으로 확인하는 수동 도구다.

감정 라벨은 /api/emotion 을 라이브로 호출해 받는다 — 규칙 폴백(inferEmotion)이 아니라
실제 LLM 경로가 무엇을 내놓는지가 확인 대상이기 때문이다.

VOICE_STYLE/EMOTIONS 표는 static/avatar_core.js 를 파싱해 읽는다. 파이썬 쪽에 값을 다시
적으면 "고정 값과 고정 값을 비교"가 되어 JS 만 바뀌어도 이 도구가 옛날 숫자를 계속
그럴듯하게 출력한다 — tests/test_emotion_classify.py 의 _js_emotion_keys() 가 같은 이유로
EMOTIONS 키를 소스에서 뽑아 쓰며, 여기서는 그 정규식 모양을 값까지 읽도록 넓혔다.

표시 규칙:
  - 목소리: voiceProsody() 와 같이 rate/pitch/volume 에 intensity 를 곱한다. 0 인 항목은 생략
    (VOICE_STYLE.joy 에 volume 키가 없으면 voiceProsody 도 0 을 내므로 실제로 안 실린다).
  - 표정: followTrack() 이 크로스페이드(CROSSFADE_S=0.25s)를 끝낸 뒤(k=1) 정착하는 값,
    즉 EMOTIONS[emo][채널] * intensity. 발화 중에는 applyMax 의 hold 가 1 이라 그대로 실린다.
  - 채널 이름은 avatar_core.js 에 있는 그대로(소문자, 좌/우 분리) 쓴다 — 보기 좋으라고
    mouthSmile 같이 묶어 쓰면 소스에서 grep 되지 않는 이름이 생긴다.

주의: 여기 나오는 세그먼트 분할은 서버(split_sentences) 기준이다. 실제 재생 시
anim.emotionTrack 은 speakFlow 가 돌려준 문장 수와 segs 길이가 같을 때만 만들어진다
(avatar_core.js speakWithEmotion 참고).
"""
import argparse
import re
import sys
import unicodedata
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
CORE_JS = ROOT / "static" / "avatar_core.js"
API = "http://127.0.0.1:8000/api/emotion"

SAMPLES = [
    "오늘 정말 기뻤어! 근데 밤에는 좀 무서웠지.",
    "발표가 드디어 끝났어요. 사람들이 다 쳐다봐서 얼굴이 화끈거렸어요. 그래도 뿌듯하네요.",
    "빌린 물건을 망가뜨려 놓고 미안하단 말도 없어서 화가 나요. 세상에, 여기서 이런 걸 다 보네요."
    " 정든 동네를 떠난다고 생각하니 마음이 허전해요.",
]

TOP_CHANNELS = 4   # 표정 채널이 최대 7개라 상위 몇 개만 보이고 나머지는 개수로 접는다


def _js_object(name: str) -> dict[str, dict[str, float]]:
    """avatar_core.js 의 `const <name> = { key: {...}, ... };` 를 파이썬 dict 로 읽는다."""
    src = CORE_JS.read_text(encoding="utf-8")
    m = re.search(r"const %s = \{(.*?)\n  \};" % name, src, re.S)
    assert m, f"avatar_core.js 의 {name} 객체를 찾지 못했습니다 — 정의부 형태가 바뀌었으면 이 정규식도 함께 고치세요."
    out: dict[str, dict[str, float]] = {}
    for line in m.group(1).splitlines():
        line = re.sub(r"//.*$", "", line)          # 값 뒤 주석·단독 주석 줄 제거 (fear/shy 줄, shy 앞 설명)
        em = re.match(r"\s*(\w+):\s*\{(.*?)\},?\s*$", line)
        if not em:
            continue
        out[em.group(1)] = {k: float(v)            # neutral 은 본문이 비어 빈 dict 가 된다
                            for k, v in re.findall(r"(\w+):\s*(-?[\d.]+)", em.group(2))}
    assert out, f"{name} 블록에서 키를 하나도 못 뽑았습니다 — 정규식이 이 파일 서식과 안 맞습니다."
    return out


def _live_channels() -> set[str]:
    """2D 렌더가 실제로 읽는 채널 — 프리셋에만 있고 픽셀에 영향이 없는 채널을 가려내려는 것.

    표정 프리셋에 값을 적어도 drawFaceParts/워프 셰이더가 그 채널을 안 읽으면 화면은
    그대로다(화남이 이미지를 0픽셀 변형하던 게 이 경우였다). 소비 지점은 `W("name")`
    아니면 좌우를 묶어 읽는 `avgLR(W, "prefix")` 둘뿐이라 그 둘만 훑는다.
    """
    src = CORE_JS.read_text(encoding="utf-8")
    live = {m.lower() for m in re.findall(r'W\(\s*"([a-zA-Z]+)"', src)}
    for p in re.findall(r'avgLR\(\s*W\s*,\s*"([a-zA-Z]+)"', src):
        live |= {p.lower() + "left", p.lower() + "right"}
    return live


def _width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _pad(s: str, w: int) -> str:
    return s + " " * max(0, w - _width(s))


def _prosody(style: dict[str, float], intensity: float) -> str:
    """voiceProsody() 와 동일하게 곱하고, 0 인 항목은 실리지 않으므로 생략한다."""
    parts = [f"{label}{style.get(key, 0.0) * intensity:+.2f}"
             for key, label in (("rate", "rate"), ("pitch", "pitch"), ("volume", "vol"))
             if style.get(key, 0.0)]
    return " ".join(parts) if parts else "변화 없음"


def _face(preset: dict[str, float], intensity: float) -> str:
    ch = sorted(preset.items(), key=lambda kv: (-kv[1], kv[0]))   # 값 내림차순, 동점은 이름순(출력 고정)
    if not ch:
        return "변화 없음"
    head = ", ".join(f"{k} {v * intensity:.2f}" for k, v in ch[:TOP_CHANNELS])
    rest = len(ch) - TOP_CHANNELS
    return head + (f" 외 {rest}개" if rest > 0 else "")


def classify(text: str) -> list[dict]:
    try:
        r = requests.post(API, json={"text": text}, timeout=30)
    except requests.exceptions.RequestException as e:
        sys.exit(f"서버에 연결하지 못했습니다 ({API}) — 먼저 서버를 띄우세요.\n  {e}")
    if not r.ok:
        sys.exit(f"서버가 {r.status_code} 를 반환했습니다 — Ollama 가 떠 있는지 확인하세요.\n  {r.text[:200]}")
    return r.json()["segments"]


def main():
    ap = argparse.ArgumentParser(
        description="문장별 감정 → 목소리 톤 + 표정 채널 전체 사슬을 한 표로 확인한다.",
        epilog="인자가 없으면 내장 예시 문장을 쓴다. 서버(%s)가 떠 있어야 한다." % API)
    ap.add_argument("text", nargs="*", help="점검할 문장(여러 개 가능). 생략하면 내장 예시.")
    texts = ap.parse_args().text or SAMPLES

    voice_style = _js_object("VOICE_STYLE")
    emotions = _js_object("EMOTIONS")
    # 두 표의 키가 어긋나면 그 감정은 목소리나 표정 한쪽만 실린다 — 정규식이 반쪽만
    # 물었을 때도 여기서 걸린다(값이 0개인 감정은 neutral 뿐이어야 정상).
    assert set(voice_style) == set(emotions), (
        f"VOICE_STYLE 과 EMOTIONS 의 감정 키가 다릅니다: {set(voice_style) ^ set(emotions)}")
    print(f"감정 분류: {API}")
    print(f"목소리/표정 표: {CORE_JS.relative_to(ROOT)} 의 VOICE_STYLE({len(voice_style)}종)"
          f" / EMOTIONS({len(emotions)}종) 을 직접 파싱\n")

    for text in texts:
        segs = classify(text)
        print(f'입력: "{text}"')
        w = max(_width(f'"{s["text"]}"') for s in segs)
        for i, s in enumerate(segs):
            emo, inten = s["emo"], s["intensity"]
            miss = [n for n, tbl in (("VOICE_STYLE", voice_style), ("EMOTIONS", emotions))
                    if emo not in tbl]
            print(f'  [{i}] {_pad(chr(34) + s["text"] + chr(34), w)}  {emo:8s} {inten:.2f}'
                  f'  | 목소리 {_prosody(voice_style.get(emo, {}), inten)}'
                  f'  | 표정 {_face(emotions.get(emo, {}), inten)}')
            if miss:
                print(f"      경고: {emo} 프리셋이 {', '.join(miss)} 에 없습니다 — 실제로는 neutral 로 조용히 폴백합니다.")
        print()

    live = _live_channels()
    hit = tot = 0
    print("2D 렌더에 실제로 반영되는 채널 수 (프리셋에만 있는 채널은 화면에 안 나온다)")
    for emo, preset in emotions.items():
        if not preset:
            continue
        dead = sorted(c for c in preset if c not in live)
        hit += len(preset) - len(dead)
        tot += len(preset)
        print(f"  {emo:9s} {len(preset) - len(dead)}/{len(preset)}"
              f"  {'죽은 채널: ' + ', '.join(dead) if dead else ''}")
    print(f"  합계 {hit}/{tot}")


if __name__ == "__main__":
    main()
