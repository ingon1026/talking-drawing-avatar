# NeuroSync 신형(v2, 8-layer/RoPE) 모델 config.
# 값 출처: its-DeFine/NeuroSync-Core neurosync/core/config.py 의 모델 서브셋.
# convaitech/NEUROSYNC model.pth 체크포인트와 정합(encoder/decoder 각 8층, input_dim=256).
config = {
    'sr': 88200,
    'frame_rate': 60,
    'hidden_dim': 1024,
    'n_layers': 8,
    'num_heads': 16,
    'dropout': 0.0,
    'output_dim': 68,       # 61 ARKit-related + 7 emotion
    'input_dim': 256,       # MFCC(23*3=69) + autocorrelation(187)
    'frame_size': 128,
    'overlap': 32,
    'use_half_precision': False,  # fp32 고정(JoyVASA와 VRAM 공유, 결정성 우선)
}
