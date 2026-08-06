"""통합 스모크 — 잃어버린 하네스 4종(capability/outmode/photo/photo_discard)의 핵심 계약만.

원래 4개 파일이었는데 스크래치 디렉터리 정리로 사라졌다. 계약 자체는 여전히 유효하므로
가장 중요한 것만 한 파일로 다시 세운다. **이건 리포 안(tests/)으로 옮겨야 한다** —
스크래치에 두면 또 사라진다.
"""
import json
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, "/home/ingon/face")
from playwright_chromium import launch_kwargs   # noqa: E402

URL = "http://localhost:8000/puppet"
IMG = "/home/ingon/face/JoyVASA/assets/examples/imgs/joyvasa_005.png"
ok, fail = [], []


def chk(n, c, e=""):
    (ok if c else fail).append(n)
    print(f"  {'PASS' if c else 'FAIL'}  {n}{'   ' + str(e) if e and not c else ''}")


def state(pg):
    return pg.evaluate("""() => ({
        char: document.getElementById('character').value,
        vidChecked: modeVideo.checked, vidDisabled: modeVideo.disabled,
        liveChecked: modeLive.checked, liveDisabled: modeLive.disabled,
        engine: getComputedStyle(document.getElementById('engine')).display,
        stage2d: document.getElementById('stage2d').style.display,
        stageVid: document.getElementById('stageVid').style.display,
        prevVisible: document.getElementById('photoPrev').style.display !== 'none',
        tempPhoto: !!tempPhoto, hasVideo: hasVideo(),
    })""")


with sync_playwright() as p:
    b = p.chromium.launch(**launch_kwargs())
    pg = b.new_page()
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(URL)
    pg.wait_for_function("() => document.querySelector('#character').options.length > 1")
    pg.wait_for_timeout(800)
    chars = pg.evaluate("() => charList")
    vid = next(c["id"] for c in chars if c["video"])
    novid = next(c["id"] for c in chars if not c["video"])

    print("\n[A] 출력 라디오 — 어떤 항목에서도 양쪽 다 잠기지 않는다")
    bad = []
    for cid in [c["id"] for c in chars] + ["__mark3d__"]:
        pg.select_option("#character", cid)
        pg.wait_for_timeout(250)
        s = state(pg)
        if s["vidDisabled"] and s["liveDisabled"]:
            bad.append(cid)
    chk(f"{len(chars) + 1}개 항목 모두 한쪽은 열림", not bad, bad)

    print("\n[B] 영상 가능/불가가 라디오에 반영된다")
    pg.select_option("#character", vid); pg.wait_for_timeout(400)
    s = state(pg)
    chk("영상 캐릭터: 영상 활성 + 엔진 셀렉트 숨김",
        not s["vidDisabled"] and s["engine"] == "none", s)
    pg.select_option("#character", novid); pg.wait_for_timeout(400)
    s = state(pg)
    chk("영상 불가 캐릭터: 영상 잠김 + 실시간 강제", s["vidDisabled"] and s["liveChecked"], s)
    chk("엔진 셀렉트 복귀", s["engine"] != "none", s["engine"])

    print("\n[C] 서버도 영상 불가 캐릭터를 막는다")
    req = urllib.request.Request(
        "http://localhost:8000/api/speak",
        json.dumps({"text": "안녕하세요", "char_id": novid}).encode(),
        {"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req); code = 200
    except urllib.error.HTTPError as e:
        code = e.code
    chk("video:false 캐릭터 영상 요청 → 4xx", code >= 400, code)

    print("\n[D] 임시 사진 — 영상 전용 + 떠나면 폐기")
    pg.set_input_files("#tempPhotoFile", IMG); pg.wait_for_timeout(1500)
    s = state(pg)
    chk("사진 모드 진입", s["char"] == "__photo__" and s["tempPhoto"], s)
    chk("미리보기 표시 + 실시간 잠김", s["prevVisible"] and s["liveDisabled"], s)
    pg.select_option("#character", vid); pg.wait_for_timeout(600)
    chk("떠나면 tempPhoto 폐기", pg.evaluate("tempPhoto") is None)
    chk("미리보기 src 제거", pg.evaluate(
        "() => !document.getElementById('photoPrev').getAttribute('src')"))

    print("\n[E] 스테이지가 한 번에 하나만 보인다")
    bad2 = []
    for cid in (vid, novid, "__mark3d__"):
        pg.select_option("#character", cid); pg.wait_for_timeout(400)
        n = pg.evaluate("""() => ['stage2d','stage3d','stageVid']
            .filter(id => getComputedStyle(document.getElementById(id)).display !== 'none').length""")
        if n != 1:
            bad2.append((cid, n))
    chk("2d/3d/vid 중 정확히 하나", not bad2, bad2)

    print("\n[F] JS 에러 0")
    chk("페이지 에러 없음", not errs, str(errs))
    b.close()

print(f"\n통과 {len(ok)} / 실패 {len(fail)}")
if fail:
    print("실패:", fail)
    sys.exit(1)
