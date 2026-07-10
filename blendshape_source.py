"""wav → ARKit 블렌드셰이프 타임시리즈 (NeuroSync 로컬 GPU 추론 + RMS 폴백).

NeuroSync audio-to-face seq2seq 트랜스포머(AnimaVR/convaitech, CC-BY-NC 4.0)로
음성 wav를 60fps ARKit 블렌드셰이프 계수로 변환한다. 추론 코어는 neurosync_infer/
에 벤더링돼 있고, 학습 가중치 model.pth 는 HF 게이트 리포라 최초 1회 토큰 소유자의
웹 약관 동의가 필요하다. 가중치를 못 받으면 librosa RMS 엔벨로프로 JawOpen만 구동하는
폴백으로 자동 전환해 인터페이스 동작을 항상 보장한다.

    audio_to_blendshapes(wav_path) -> {"fps": float, "names": list[str], "frames": list[list[float]]}

모델은 첫 호출 시 1회 로드되어 GPU에 상주한다(재로드 없음).
"""
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent
_HF_REPO = "AnimaVR/NEUROSYNC_Audio_To_Face_Blendshape"
_MODEL_PATH = ROOT / "neurosync_infer" / "model.pth"  # 여기에 두면 로컬 우선 로드

FPS = 60.0  # NeuroSync frame_rate

# NeuroSync 출력 68채널 중 0..51 = 표준 ARKit 블렌드셰이프(0~1 범위).
# 52-60 은 head/eye pose(도 단위), 61-67 은 emotion 이라 0~1 계약을 벗어나 제외한다.
# (순서는 NeuroSync FaceBlendShape enum 0..51 을 그대로 전사)
ARKIT_NAMES = [
    "EyeBlinkLeft", "EyeLookDownLeft", "EyeLookInLeft", "EyeLookOutLeft",
    "EyeLookUpLeft", "EyeSquintLeft", "EyeWideLeft",
    "EyeBlinkRight", "EyeLookDownRight", "EyeLookInRight", "EyeLookOutRight",
    "EyeLookUpRight", "EyeSquintRight", "EyeWideRight",
    "JawForward", "JawLeft", "JawRight", "JawOpen",
    "MouthClose", "MouthFunnel", "MouthPucker", "MouthLeft", "MouthRight",
    "MouthSmileLeft", "MouthSmileRight", "MouthFrownLeft", "MouthFrownRight",
    "MouthDimpleLeft", "MouthDimpleRight", "MouthStretchLeft", "MouthStretchRight",
    "MouthRollLower", "MouthRollUpper", "MouthShrugLower", "MouthShrugUpper",
    "MouthPressLeft", "MouthPressRight", "MouthLowerDownLeft", "MouthLowerDownRight",
    "MouthUpperUpLeft", "MouthUpperUpRight",
    "BrowDownLeft", "BrowDownRight", "BrowInnerUp", "BrowOuterUpLeft", "BrowOuterUpRight",
    "CheekPuff", "CheekSquintLeft", "CheekSquintRight",
    "NoseSneerLeft", "NoseSneerRight", "TongueOut",
]
assert len(ARKIT_NAMES) == 52, len(ARKIT_NAMES)
N_ARKIT = 52
JAW_OPEN_IDX = ARKIT_NAMES.index("JawOpen")  # 17

_model = None
_device = None
_load_attempted = False
_LAST_SOURCE = None  # "neurosync" | "fallback-rms" (self-check/로그용, 반환 dict엔 미포함)


def _ensure_weights():
    """로컬 model.pth 우선, 없으면 HF 게이트 리포에서 다운로드 시도. 실패 시 None."""
    if _MODEL_PATH.exists():
        return _MODEL_PATH
    try:
        from huggingface_hub import hf_hub_download
        return Path(hf_hub_download(_HF_REPO, "model.pth"))
    except Exception as e:
        sys.stderr.write(
            f"[blendshape_source] model.pth 다운로드 실패({type(e).__name__}). "
            f"HF 게이트 미승인으로 추정 → RMS 폴백 사용.\n")
        return None


def _get_model():
    """NeuroSync 모델 lazy 싱글턴 로드. 가중치 없거나 로드 실패면 None(폴백)."""
    global _model, _device, _load_attempted
    if _load_attempted:
        return _model
    _load_attempted = True
    try:
        import torch
        from neurosync_infer.model import load_model
        from neurosync_infer.config import config
        _device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        weights = _ensure_weights()
        if weights is None:
            return None
        _model = load_model(str(weights), config, _device)
        sys.stderr.write(f"[blendshape_source] NeuroSync 모델 로드 완료 ({_device}).\n")
    except Exception as e:
        sys.stderr.write(f"[blendshape_source] NeuroSync 로드 실패({type(e).__name__}: {e}) → RMS 폴백.\n")
        _model = None
    return _model


def _infer_neurosync(wav_path, model):
    """NeuroSync 추론 → (arkit (N,52) 0~1, head (N,3) 도 단위). 오디오 너무 짧으면 None."""
    from neurosync_infer.extract_features import extract_audio_features
    from neurosync_infer.audio_processing import process_audio_features
    from neurosync_infer.config import config
    feats, y = extract_audio_features(wav_path, from_bytes=False)
    if feats is None:
        return None
    out = np.asarray(process_audio_features(feats, model, _device, config))  # (N,68), [:, :61]/100 적용됨
    head = out[:, 52:55] * 100.0  # HeadYaw/Pitch/Roll (도) — /100 되돌림
    return np.clip(out[:, :N_ARKIT], 0.0, 1.0), head


def _infer_fallback(wav_path):
    """librosa RMS 엔벨로프 → JawOpen만 구동하는 60fps 폴백. (N, 52) 반환."""
    import librosa
    y, sr = librosa.load(wav_path, sr=16000, mono=True)
    hop = max(int(round(sr / FPS)), 1)
    rms = librosa.feature.rms(y=y, frame_length=hop * 2, hop_length=hop, center=True)[0]
    ref = np.percentile(rms, 95) if rms.size else 1.0  # 단발 피크가 전체를 눌러버리지 않게 95pct 기준
    jaw = np.clip(rms / (ref + 1e-8), 0.0, 1.0)
    frames = np.zeros((len(jaw), N_ARKIT), dtype=np.float32)
    frames[:, JAW_OPEN_IDX] = jaw
    return frames


def audio_to_blendshapes(wav_path: str) -> dict:
    """음성 wav → ARKit 블렌드셰이프 타임시리즈.

    반환: {"fps": float, "names": list[str], "frames": list[list[float]]}  (frames: 프레임×52, 0~1)
    """
    global _LAST_SOURCE
    wav_path = str(wav_path)
    model = _get_model()
    res = _infer_neurosync(wav_path, model) if model is not None else None
    if res is None:
        arkit, head = _infer_fallback(wav_path), None
        _LAST_SOURCE = "fallback-rms"
    else:
        arkit, head = res
        _LAST_SOURCE = "neurosync"
    out = {
        "fps": FPS,
        "names": list(ARKIT_NAMES),
        "frames": arkit.astype(float).tolist(),
    }
    if head is not None and float(np.abs(head).max()) > 0.05:  # 채널이 죽어있으면 생략
        out["head"] = head.astype(float).tolist()  # 프레임 × [yaw, pitch, roll] (도)
    return out


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python blendshape_source.py <wav_path>")
        sys.exit(1)
    wav = sys.argv[1]
    t0 = time.time()
    res = audio_to_blendshapes(wav)
    dt = time.time() - t0

    names, frames, fps = res["names"], res["frames"], res["fps"]
    n = len(frames)
    arr = np.array(frames) if n else np.zeros((1, len(names)))
    jaw = arr[:, names.index("JawOpen")]
    print(f"source            : {_LAST_SOURCE}")
    print(f"device            : {_device}")
    print(f"fps               : {fps}")
    print(f"channels          : {len(names)}")
    print(f"frames            : {n}  (~{n / fps:.2f}s @ {fps}fps)")
    print(f"value range (all) : [{arr.min():.4f}, {arr.max():.4f}]")
    print(f"JawOpen mean/std  : {jaw.mean():.4f} / {jaw.std():.4f}  (min {jaw.min():.4f}, max {jaw.max():.4f})")
    if "head" in res:
        h = np.array(res["head"])
        for i, nm in enumerate(["HeadYaw", "HeadPitch", "HeadRoll"]):
            print(f"{nm:18s}: mean {h[:, i].mean():+.2f}° std {h[:, i].std():.2f}° range [{h[:, i].min():+.2f}, {h[:, i].max():+.2f}]")
    else:
        print("head              : (없음/죽은 채널)")
    print(f"infer time        : {dt:.3f}s")
