"""버튼 하이라이트가 '다음 발화에 보낼 감정'과 일치하는가."""
import json
import sys

from playwright.sync_api import sync_playwright

from _harness import Checks, open_puppet   # noqa: E402 — sys.path 삽입을 겸한다

chk = Checks()
def lit(pg):
    return pg.evaluate("""() => [...document.querySelectorAll('#emotions button')]
        .filter(b => b.style.background && b.style.background.includes('91, 140, 255'))
        .map(b => b.dataset.emo)""")
with sync_playwright() as p:
    b, pg, errs = open_puppet(p)
    reqs=[]
    pg.on("request", lambda r: reqs.append(json.loads(r.post_data or "{}"))
          if r.url.endswith("/api/speak") and r.method=="POST" else None)
    pg.select_option("#character", pg.evaluate("() => charList.find(c => c.video).id"))
    pg.wait_for_timeout(400); pg.check("#autoEmo")
    def say(t):
        reqs.clear(); pg.fill("#text", t); pg.click("#send")
        pg.wait_for_selector("#saveVid:not([hidden])", timeout=120000)
        return reqs[0] if reqs else {}
    say("워밍업입니다")
    print("\n[1] 자동 발화 뒤 — 버튼이 켜져 있으면 안 된다")
    body = say("정말 축하드려요 너무 멋졌어요")
    pg.wait_for_timeout(300)
    print(f"     auto_emo={body.get('auto_emo')} / 켜진 버튼={lit(pg)} / manualEmo={pg.evaluate('manualEmo')}")
    chk("자동인데 켜진 버튼 없음", lit(pg) == [], lit(pg))
    chk("manualEmo 는 null", pg.evaluate("manualEmo") is None)
    print("\n[2] 수동 버튼 — 켜지고, 그 감정이 나간다")
    pg.click('#emotions button[data-emo="angry"]'); pg.wait_for_timeout(200)
    chk("angry 만 켜짐", lit(pg) == ["angry"], lit(pg))
    body = say("수동 확인 문장")
    chk("angry 로 나감", body.get("emotion") == "angry" and body.get("auto_emo") is False, body.get("emotion"))
    pg.wait_for_timeout(300)
    chk("발화 뒤에도 angry 유지", lit(pg) == ["angry"], lit(pg))
    print("\n[3] 기본 버튼 → 자동 복귀, 하이라이트도 꺼짐")
    pg.click('#emotions button[data-emo="neutral"]'); pg.wait_for_timeout(200)
    chk("켜진 버튼 없음", lit(pg) == [], lit(pg))
    body = say("자동 복귀 확인 문장")
    chk("auto_emo=true 복귀", body.get("auto_emo") is True)
    pg.wait_for_timeout(300)
    chk("자동 발화 뒤에도 안 켜짐", lit(pg) == [], lit(pg))
    chk("JS 에러 0", not errs, str(errs))
    b.close()
sys.exit(chk.report())
