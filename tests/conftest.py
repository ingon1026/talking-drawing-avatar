"""pytest 가 이 디렉터리를 수집하기 전에 자동으로 로드한다 — 프로젝트 루트를
sys.path 에 한 곳에서만 넣어, 개별 테스트 파일이 각자 sys.path.insert 를
반복하지 않게 한다."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
