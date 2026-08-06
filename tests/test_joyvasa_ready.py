"""`/api/health` 의 joyvasa_ready 판정 — 실제로 false 를 내는가.

예전엔 `(ROOT / "JoyVASA").exists()` 하나였다. 가중치가 없거나 패치가 안 걸려 있어도
true 라서 영상 라디오가 열리고, 사용자는 눌러 본 뒤에야 500 을 만났다.

여기서 보는 것은 "고쳤다" 가 아니라 **"각 고장 상황에서 실제로 false 가 나오는가"** 다.
검사를 넣어 놓고 아무것도 안 잡는 상태가 제일 나쁘다 — 화면은 똑같이 열려 있는데
고쳤다고 믿게 된다.

실제 가중치·소스는 건드리지 않는다. tempdir 에 구조만 흉내 내고 `app.ROOT` 와
`pipeline.JOYVASA` 를 잠깐 그쪽으로 돌린다.
"""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PIPE_SRC = "src/live_portrait_wmg_pipeline.py"


def _load_app():
    """`import app` 은 못 쓴다 — pipeline 이 sys.path 앞에 JoyVASA 를 넣어서
    JoyVASA/app.py 가 잡히고, 그게 gradio 파이프라인을 통째로 로드한다(실제로 밟았다)."""
    spec = importlib.util.spec_from_file_location("faceapp", ROOT / "app.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["faceapp"] = mod
    spec.loader.exec_module(mod)
    return mod


app = _load_app()
import pipeline  # noqa: E402  — app 뒤에 와야 한다(위 주석)

# functools.cache 를 벗겨야 매번 다시 판정한다
ready = app._joyvasa_ready.__wrapped__


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """가중치·소스가 전부 갖춰진 가짜 루트. 테스트가 하나씩 망가뜨린다."""
    jv = tmp_path / "JoyVASA"
    for p in app._JOYVASA_WEIGHTS:
        f = jv / "pretrained_weights" / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x")          # 존재만 본다 — 내용은 안 읽는다
    (jv / "src").mkdir(parents=True)
    (jv / PIPE_SRC).write_text((ROOT / "JoyVASA" / PIPE_SRC).read_text())
    monkeypatch.setattr(app, "ROOT", tmp_path)
    monkeypatch.setattr(pipeline, "JOYVASA", jv)
    return jv


def test_실제_환경은_준비됨():
    assert ready() is True


def test_전부_갖추면_true(fake):
    assert ready() is True


def test_가중치_하나만_없어도_false(fake):
    (fake / "pretrained_weights" / app._JOYVASA_WEIGHTS[4]).unlink()
    assert ready() is False


@pytest.mark.parametrize("marker", ["exp_delta_schedule", "eye_ratio_schedule", "images2video"])
def test_패치_마커가_빠지면_false(fake, marker):
    src = fake / PIPE_SRC
    src.write_text(src.read_text().replace(marker, "___"))
    assert ready() is False


def test_execute_밖에만_마커가_있으면_false(fake):
    """예전 검사(파일 전체 문자열)가 못 잡던 구멍.

    execute 는 클래스의 마지막 메서드라, 끝 경계를 `\\n    def ` 로만 잡으면 파일 끝까지
    삼킨다 — 뒤에 붙은 모듈 레벨 주석의 마커가 execute 안에 있는 것처럼 읽혔다.
    """
    src = fake / PIPE_SRC
    good = src.read_text()
    ex = re.search(r"\n    def execute\(.*?(?=\n    def |\n\S|\Z)", good, re.S).group(0)
    src.write_text(good.replace(ex, ex.replace("images2video", "___"))
                   + "\n# images2video exp_delta_schedule eye_ratio_schedule\n")
    assert ready() is False


def test_소스_파일이_없으면_false(fake):
    (fake / PIPE_SRC).unlink()
    assert ready() is False


def test_마커_목록이_pipeline_과_같은_출처(fake):
    """app 이 목록을 손으로 베껴 들면 갈라진다 — 증상은 '라디오는 열렸는데 누르면 500'."""
    from pipeline import PATCH_MARKERS, STREAM_MARKER
    assert app.PATCH_MARKERS is PATCH_MARKERS
    assert app.STREAM_MARKER is STREAM_MARKER
