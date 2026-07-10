"""NeuroSync audio-to-face 추론 코어(벤더링, v2 8-layer).

NeuroSync 신형 모델(convaitech/NEUROSYNC = AnimaVR/NEUROSYNC_Audio_To_Face_Blendshape,
RoPE 기반 8층/16헤드, 입력 256차원[MFCC+autocorr])의 최소 추론 경로만 발췌해 고유
패키지로 벤더링. 전역 `utils`/`neurosync.core` top-level 이름을 점유하지 않는 목적.
- model.py / extract_features.py / audio_processing.py : its-DeFine/NeuroSync-Core 원본 그대로(무수정)
- config.py : 모델 서브셋만 발췌해 최소화(원본은 LLM/TTS env 설정 혼재)
- 라이선스: dual (연매출 $1M 미만 MIT / 이상 상업허가) — 각 파일 헤더 참조
이 파일들은 서로/외부 패키지를 import 하지 않아 수정 없이 복사했다(config는 별도 작성).
"""
