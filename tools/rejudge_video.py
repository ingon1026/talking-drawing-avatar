"""이미 등록된 캐릭터들에 manifest 의 `"video"` 를 소급해서 적는다.

    PYTHONPATH= .venv/bin/python tools/rejudge_video.py            # 판정만 (기본, 안 고침)
    PYTHONPATH= .venv/bin/python tools/rejudge_video.py --apply    # manifest 에 실제로 쓴다
    PYTHONPATH= .venv/bin/python tools/rejudge_video.py --only u_080f18

`그림 추가` 로 만든 캐릭터는 원본(source.png)이 있으면 무조건 영상(JoyVASA) 경로를 탔다.
그런데 LivePortrait 는 실사 얼굴 포트레이트로 학습돼서 손그림 전신 낙서는 얼굴을 못 찾고
그림 전체가 늘어난다 — "원본이 있다" 와 "영상을 만들 수 있다" 는 다른 조건이다. 그래서
등록 시점에 얼굴 검출로 판정해 manifest 의 `"video"` 에 적는 방식으로 바뀌었는데, 그 전에
만들어진 캐릭터들에는 그 키가 아예 없다. **이 도구는 그 과거분을 한 번 메우는 일회성
마이그레이션이다** — 새로 등록되는 캐릭터는 서버가 알아서 적으므로 여기 올 일이 없다.

기본이 dry-run 인 건 일부러다. 남의 캐릭터 메타데이터를 스물몇 개 건드리는 일이라,
표를 먼저 눈으로 보고 납득한 뒤에 `--apply` 를 붙이게 했다. 어느 쪽이든 source.png 는
읽기만 한다 — 사용자 원본이고 지우면 복구가 안 된다.

source.png 가 없는 내장 캐릭터(default/girl/pig/stick)는 판정을 돌리지 않고 바로
`"video": false` 다. 영상 경로가 원본 그림을 입력으로 받으므로(app.py 의 char_id → source.png)
원본이 없으면 영상 자체가 불가능하다.
"""
import argparse
import json
import os
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CHARS = ROOT / "assets_characters"


def _width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _pad(s: str, w: int) -> str:
    return s + " " * max(0, w - _width(s))


def _write_manifest(path: Path, manifest: dict, keep_newline: bool):
    """같은 디렉터리 임시 파일에 쓰고 os.replace 로 원자적 교체.

    쓰기 형식은 character_builder.py 가 이 파일들을 만들 때 쓴 것과 같다
    (ensure_ascii=False, indent=2) — 안 맞추면 한글 이름이 \\uXXXX 로 바뀌면서
    바꾸지도 않은 줄이 전부 diff 에 뜬다. 끝 개행은 파일마다 달라서 원본을 따라간다.
    """
    text = json.dumps(manifest, ensure_ascii=False, indent=2) + ("\n" if keep_newline else "")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="manifest 에 실제로 쓴다 (기본은 판정만)")
    ap.add_argument("--only", metavar="ID", help="이 캐릭터 하나만 (디버깅용)")
    a = ap.parse_args()
    sys.argv = sys.argv[:1]   # JoyVASA ArgumentConfig 가 argv 를 다시 파싱한다

    dirs = [d for d in sorted(CHARS.iterdir()) if (d / "manifest.json").exists()]
    if a.only:
        dirs = [d for d in dirs if d.name == a.only]
        if not dirs:
            sys.exit(f"그런 캐릭터가 없다: {a.only}")

    # 판정 직전에 import 한다 — pipeline 이 sys.path 맨 앞에 JoyVASA 를 끼워 넣으므로
    # argv 를 비운 뒤여야 하고, 로더가 1초 넘게 걸려서 인자 오류는 그 전에 나는 게 낫다.
    from pipeline import can_animate  # noqa: E402

    rows, n_yes, n_changed = [], 0, 0
    for d in dirs:
        mf_path = d / "manifest.json"
        raw = mf_path.read_text(encoding="utf-8")
        manifest = json.loads(raw)
        src = d / "source.png"
        video = bool(can_animate(src)) if src.exists() else False
        changed = "video" not in manifest or bool(manifest["video"]) != video
        if changed and a.apply:
            manifest["video"] = video
            _write_manifest(mf_path, manifest, raw.endswith("\n"))
        n_yes += video
        n_changed += changed
        rows.append((d.name, manifest.get("name", d.name), src.exists(), video, changed))

    w_id = max([_width(r[0]) for r in rows] + [4])
    w_nm = max([_width(r[1]) for r in rows] + [4])
    print(f"{_pad('id', w_id)}  {_pad('이름', w_nm)}  원본  판정  변경")
    for cid, name, has_src, video, changed in rows:
        print(f"{_pad(cid, w_id)}  {_pad(name, w_nm)}  "
              f"{'있음' if has_src else '없음'}  "
              f"{'가능' if video else '불가'}  "
              f"{('적용' if a.apply else '필요') if changed else '변경 없음'}")
    print(f"\n영상 가능 {n_yes}개 / 불가 {len(rows) - n_yes}개 / 변경 {n_changed}개"
          + ("" if a.apply else "  — dry-run 이라 아무것도 안 고쳤다. 쓰려면 --apply"))


if __name__ == "__main__":
    main()
