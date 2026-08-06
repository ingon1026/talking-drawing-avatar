"""연속 사용 하네스 — 기능을 **여러 번 이어서** 썼을 때도 도는지 본다.

오늘 이걸 안 봐서 놓쳤다: 문장별 감정 전환과 판정/TTS 병렬화가 첫 발화는 되는데
두 번째부터 조용히 꺼졌다. 하네스가 전부 단발 시나리오였기 때문이다.
기존 4종(capability/outmode/photo/photo_discard)도 전부 "한 번 해보고 끝" 이다.

여기서 보는 것은 하나다 — **N회 이어서 했을 때 1회차와 같은 일이 일어나는가.**
"""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from playwright.sync_api import sync_playwright

from _harness import ROOT, TMP, Checks, open_puppet   # noqa: E402 — sys.path 삽입을 겸한다

OUT = ROOT / "output"
chk = Checks()


# 감정이 확실히 갈리는 세 문장 — 매 회차 다른 문장을 써서 LRU 캐시를 피한다
TEXTS = [
    "정말 축하드려요 오늘 발표 최고였어요",
    "옆집이 밤새 시끄러워서 참기가 어렵네요",
    "이제는 그냥 다 허전하고 쓸쓸하네요",
]


def speak_and_capture(pg, text, reqs):
    """말하기 → 완료까지. 그 사이 나간 /api/speak 요청 바디를 돌려준다."""
    reqs.clear()
    pg.fill("#text", text)
    pg.evaluate("() => { window.__t = {}; }")
    pg.click("#send")
    pg.wait_for_selector("#saveVid:not([hidden])", timeout=120000)
    body = next((r for r in reqs), None)
    href = pg.eval_on_selector("#saveVid", "a => a.getAttribute('href')")
    return body, href


def brow_band(mp4, frac):
    """눈썹 대역 프레임 한 장 — 표정이 살아 있는지 픽셀로 본다."""
    dur = float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(mp4)],
        capture_output=True, text=True).stdout or 0)
    n = max(1, int(dur * 25 * frac))
    p = TMP / "rep_frame.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp4),
                    "-vf", f"select=eq(n\\,{n}),scale=384:384", "-vframes", "1", str(p)], check=True)
    return np.asarray(Image.open(p).convert("L"), dtype=float)


with sync_playwright() as p:
    b, pg, errs = open_puppet(p)
    reqs, cls = [], []
    pg.on("request", lambda r: cls.append(r.url) if "/api/emotion" in r.url else None)
    pg.on("request", lambda r: reqs.append(json.loads(r.post_data or "{}"))
          if r.url.endswith("/api/speak") and r.method == "POST" else None)
    vid = pg.evaluate("() => charList.find(c => c.video).id")
    pg.select_option("#character", vid)
    pg.wait_for_timeout(500)
    pg.check("#autoEmo")

    print("\n[워밍업] 모델 로드")
    speak_and_capture(pg, "워밍업 문장입니다", reqs)

    print("\n[1] 감정 자동이 연속 3회 모두 서버로 간다")
    rows = []
    for i, t in enumerate(TEXTS, 1):
        body, href = speak_and_capture(pg, t, reqs)
        st = json.load(__import__("urllib.request", fromlist=["x"]).urlopen(
            f"http://localhost:8000/api/jobs/{href.rsplit('/', 1)[-1].replace('.mp4', '')}"))
        rows.append((i, body.get("auto_emo"), body.get("emotion"),
                     body.get("intensity"), st.get("emotion"), href))
        print(f"     {i}회차: auto_emo={body.get('auto_emo')} "
              f"보낸emotion={body.get('emotion')} intensity={body.get('intensity')} "
              f"→ 서버판정={st.get('emotion')}")
    chk("3회 모두 auto_emo=true", all(r[1] is True for r in rows),
        [r[1] for r in rows])
    chk("3회 모두 서버가 감정을 판정", all(r[4] for r in rows), [r[4] for r in rows])
    chk("문장이 다르니 판정도 갈린다", len({r[4] for r in rows}) > 1, [r[4] for r in rows])

    print("\n[2] 표정 강도가 회차마다 죽지 않는다")
    base = None
    diffs = []
    for i, *_rest, href in rows:
        mp4 = OUT / href.rsplit("/", 1)[-1]
        if not mp4.exists():
            chk(f"{i}회차 mp4 존재", False, mp4)
            continue
        f = brow_band(mp4, 0.5)
        if base is None:
            base = f
        else:
            diffs.append(np.abs(f - base).mean())
    chk("회차 간 프레임이 서로 다르다(무표정 수렴 없음)",
        all(d > 1.5 for d in diffs), [round(d, 2) for d in diffs])

    print("\n[3] 수동 버튼은 연속으로도 계속 이긴다")
    pg.click('#emotions button[data-emo="angry"]')
    man = []
    for i in range(2):
        body, _ = speak_and_capture(pg, f"수동 버튼 확인 문장 {i}", reqs)
        man.append((body.get("auto_emo"), body.get("emotion")))
        print(f"     {i+1}회차: auto_emo={body.get('auto_emo')} emotion={body.get('emotion')}")
    chk("수동이면 auto_emo=false", all(m[0] is False for m in man), man)
    chk("2회 모두 angry 유지", all(m[1] == "angry" for m in man), man)

    print("\n[3b] 실시간 경로에서도 수동 버튼이 이긴다 (예전엔 정반대였다)")
    # 영상은 버튼을 존중하는데 실시간은 감정 자동이 켜져 있으면 버튼을 무시하고 재분류했다.
    # 같은 버튼이 캐릭터에 따라 다르게 동작하던 자리다 — 여기만 영상이 아닌 캐릭터로 본다.
    novid = pg.evaluate("() => charList.find(c => !c.video).id")
    pg.select_option("#character", novid)
    pg.wait_for_timeout(600)
    pg.click('#emotions button[data-emo="angry"]')
    n0 = len(cls)
    for i in range(2):
        pg.evaluate("t => speak(t)", f"실시간 수동 버튼 확인 문장 {i}")
        pg.wait_for_timeout(300)
    cur = pg.evaluate("() => emo.current()")
    print(f"     /api/emotion 호출 {len(cls) - n0}회, 현재 표정={cur}")
    chk("버튼이 걸려 있으면 재분류하지 않는다", len(cls) == n0, len(cls) - n0)
    # current() 는 {key, level} 을 준다. level 1 = 감쇠 안 됨 = sticky 가 살아 있다.
    chk("2회 발화 뒤에도 angry 고정(sticky)",
        cur.get("key") == "angry" and cur.get("level") == 1, cur)

    print("\n[4] 자동으로 돌아오면 다시 서버 판정")
    pg.select_option("#character", vid)
    pg.wait_for_timeout(600)
    pg.click('#emotions button[data-emo="neutral"]')
    body, _ = speak_and_capture(pg, "다시 자동으로 돌아왔는지 보는 문장", reqs)
    print(f"     auto_emo={body.get('auto_emo')} emotion={body.get('emotion')}")
    chk("중립 버튼 후 auto_emo=true 복귀", body.get("auto_emo") is True, body.get("auto_emo"))

    print("\n[5] 임시 사진도 연속 2회 (감정이 최대치로 얼지 않는다)")
    pg.set_input_files("#tempPhotoFile",
                       str(ROOT / "JoyVASA/assets/examples/imgs/joyvasa_005.png"))
    pg.wait_for_timeout(1500)
    ph = []
    for i, t in enumerate(TEXTS[:2], 1):
        body, _ = speak_and_capture(pg, t, reqs)
        ph.append((body.get("auto_emo"), body.get("intensity")))
        print(f"     {i}회차: auto_emo={body.get('auto_emo')} intensity={body.get('intensity')}")
    chk("사진 모드도 2회 모두 auto_emo=true", all(x[0] is True for x in ph), ph)

    print("\n[6] JS 에러 0")
    chk("페이지 에러 없음", not errs, str(errs))
    b.close()

sys.exit(chk.report())
