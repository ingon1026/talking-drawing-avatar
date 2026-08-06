"""e2e 하네스 공용 부품.

4종(smoke/repeat/btn/mouth)이 같은 15줄을 각자 들고 있었다 — sys.path 삽입, 브라우저
기동, 페이지 열고 캐릭터 목록 대기, PASS/FAIL 집계, 마지막 요약과 종료 코드.
새 하네스를 만들 때마다 그 15줄부터 베끼게 되고, 베끼다 한 줄 빠지면(예: pageerror
리스너) 그 하네스만 조용히 덜 본다.

**서버는 이미 떠 있어야 한다** — 여기서 띄우지 않는다. 하네스는 실제로 돌고 있는
서버를 보는 게 목적이고, 띄우는 쪽까지 맡으면 "테스트용으로만 뜨는 서버" 를 검증하게 된다.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from playwright_chromium import launch_kwargs   # noqa: E402

URL = "http://localhost:8000/puppet"
TMP = Path(__file__).parent / "_tmp"
TMP.mkdir(exist_ok=True)   # 새 클론엔 없다 — git 은 빈 디렉터리를 안 남긴다


class Checks:
    """PASS/FAIL 집계. `report()` 가 종료 코드까지 정한다."""

    def __init__(self):
        self.ok, self.fail = [], []

    def __call__(self, name, cond, ev=""):
        (self.ok if cond else self.fail).append(name)
        print(f"  {'PASS' if cond else 'FAIL'}  {name}{'   ' + str(ev) if ev and not cond else ''}")

    def report(self):
        print(f"\n통과 {len(self.ok)} / 실패 {len(self.fail)}")
        if self.fail:
            print("실패:", self.fail)
        return 1 if self.fail else 0


def open_puppet(p, **page_kw):
    """브라우저 → /puppet → 캐릭터 목록이 찰 때까지. (browser, page, errs) 를 준다.

    errs 는 pageerror 를 담는 리스트다. **모든 하네스가 마지막에 이걸 확인해야 한다** —
    기능 검사는 통과하는데 콘솔에 예외가 쌓이는 상태를 여기서만 잡을 수 있다.
    """
    b = p.chromium.launch(**launch_kwargs())
    pg = b.new_page(**page_kw)
    errs = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    pg.goto(URL)
    pg.wait_for_function("() => document.querySelector('#character').options.length > 1")
    pg.wait_for_timeout(800)
    return b, pg, errs
