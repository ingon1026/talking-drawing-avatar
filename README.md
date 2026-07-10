---
title: Talking Drawing Avatar
emoji: 🎨
colorFrom: blue
colorTo: pink
sdk: docker
app_port: 7860
pinned: false
---

# 말하는 그림 아바타

텍스트를 입력하면 그림 캐릭터가 한국어로 말하고(립싱크 + 얼굴 움직임), 눈깜빡임을 자유롭게 제어할 수 있다. 두 모드:

- **`/` 영상 생성 모드 (Phase A)**: 그림 원본을 AI(JoyVASA)가 통째로 애니메이션 → mp4. 문장당 10~20초.
- **`/puppet` 실시간 퍼펫 모드 (Phase B)**: 파츠 에셋 기반 캔버스 렌더링. 텍스트 후 ~0.5초 만에 발화, 깜빡임 버튼/슬라이더 즉시 반응.

## 실행

**systemd 서비스로 상시 실행 중** — WSL이 켜지면 자동 기동, 크래시 시 자동 재시작. 수동 실행 불필요.

```bash
systemctl --user status face-avatar    # 상태 확인
systemctl --user restart face-avatar   # 코드 수정 후 재시작
journalctl --user -u face-avatar -f    # 로그
```

브라우저: http://localhost:8000 (영상 모드) / **http://localhost:8000/puppet (퍼펫 모드, 메인)**
※ `wsl --shutdown` 하면 꺼지고, WSL 다시 열면 자동 기동.

## 아바타 그림 교체

**영상 모드**: 이 폴더 최상위에 PNG/JPG를 넣으면 자동 사용 (없으면 JoyVASA 샘플). 정면 얼굴, 눈·입이 잘 보이는 그림일수록 잘 동작.

**퍼펫 모드 (커스텀 캐릭터)** — 두 가지 방법:
1. **드래그앤드랍 (권장)**: 퍼펫 페이지의 캐릭터 화면에 그림 파일을 끌어다 놓기 (또는 ➕ 그림 추가 버튼)
   → 왼눈 클릭 → 오른눈 클릭 → 입 중심 클릭 → 입 영역 드래그 → 이름 입력 → 완성 (ESC 취소).
   서버가 눈·입을 지우고 스프라이트/벡터 입으로 대체한 캐릭터를 자동 생성 (`character_builder.py`).
2. 수동: `assets_characters/<이름>/` 폴더에 파츠 PNG 직접 구성 — 규칙은 `tools/make_default_character.py` 참조.

## 음성→표정 엔진 (퍼펫 모드에서 선택 가능)

- **NeuroSync** (기본, 235M, 웜 ~0.3초): 32채널 활성. 깜빡임·시선 채널은 의도적으로 0 → 웹 렌더러가 자체 처리.
  가중치(HF convaitech/NEUROSYNC, 게이트) 없으면 폴백(음성 크기→입 벌림만) 자동 전환.
- **NVIDIA Audio2Face-3D** (mark v2.3, 호출당 ~3초): SDK를 WSL2에서 로컬 빌드(subprocess 방식). 자연 깜빡임 포함.
  출력이 오디오보다 ~0.4초 늦어 서버에서 트랙을 당겨 보정함. 감정 모델(A2E)은 HF 게이트라 neutral 고정.
  재빌드 절차: `Audio2Face-3D-SDK/_project_build/SETUP.md`.

## 구성

- `app.py` — FastAPI 서버 (잡 큐 + edge-tts)
- `pipeline.py` — JoyVASA 인프로세스 추론 + 눈깜빡임 스케줄 주입 (`python pipeline.py`로 스케줄 자체 검증)
- `static/index.html` — 웹 UI
- `JoyVASA/` — 벤더 (src/live_portrait_wmg_pipeline.py에 깜빡임 주입 5줄 수정됨)
- 생성 속도: 문장당 오디오 길이의 약 2배 시간 (RTX 4070 Ti, 모델 상주 기준)

## 배포 (HF Spaces)

이 리포는 그대로 HF Docker Space로 배포된다 (CPU, 퍼펫 모드만 — JoyVASA 영상 모드와 NVIDIA A2F 엔진은 GPU/TensorRT 필요라 로컬 전용).
- Space Settings → Secrets에 `HF_TOKEN` (NeuroSync 게이트 모델 접근 가능한 토큰) 등록 시 52채널 표정 활성화.
  미등록 시 폴백(음성 크기→입 벌림만)으로 동작.
- 첫 발화 요청 때 가중치(≈900MB)를 자동 다운로드하므로 재시작 직후 첫 응답은 느리다.

## 주의

- LivePortrait/JoyVASA의 InsightFace 검출 모델은 비상업(연구) 전용 — 상업 배포 시 교체 필요.
- NeuroSync는 듀얼 라이선스 (연매출 $1M 미만 MIT / 이상 상업 허가 필요).
- VRAM 사용 ~6.7GB. OOM 시 pipeline.py에서 `flag_use_half_precision=True` 고려.
