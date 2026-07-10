"""wav → ARKit 블렌드셰이프 타임시리즈 (NVIDIA Audio2Face-3D 로컬 GPU 추론).

NVIDIA Audio2Face-3D SDK(회귀 모델 mark v2.3 + host blendshape solver)를 로컬에서
빌드한 실행파일 `sample-a2f-blendshape-export` 를 subprocess 로 호출해 음성 wav 를
ARKit 52 블렌드셰이프 계수(0~1)로 변환한다. SDK 는 mesh geometry 를 추론한 뒤
동봉된 skin 블렌드셰이프 rig(bs_skin.npz, poseNames == ARKit camelCase)에 solve 해서
52 채널 weight 를 만들고, 여기서 첫 글자만 대문자로 바꾸면 blendshape_source.py 의
ARKIT_NAMES(PascalCase)와 정확히 같은 이름 체계가 된다.

    audio_to_blendshapes(wav_path) -> {"fps": float, "names": list[str], "frames": list[list[float]]}

호출당 프로세스를 새로 띄운다(TensorRT 엔진 로드 포함 콜드스타트). 지연은 모듈 하단
self-check 로 측정/출력한다.
"""
import os
import subprocess
import sys
import tempfile
import time
from glob import glob
from pathlib import Path

import numpy as np

from blendshape_source import ARKIT_NAMES, N_ARKIT  # 52채널 PascalCase 이름/순서 공유

# --- 경로 설정 (로컬 빌드 산출물) ---
SDK = Path("/home/ingon/face/Audio2Face-3D-SDK")
_BUILD = SDK / "_build" / "release"
EXPORTER = _BUILD / "audio2face-sdk" / "bin" / "sample-a2f-blendshape-export"
MODEL_JSON = SDK / "_data" / "generated" / "audio2face-sdk" / "samples" / "data" / "mark" / "model.json"
FPS = 60.0  # exporter 에 넘기는 frameRate (A2F 회귀 executor)

_CONDA_ENV_LIB = Path("/home/ingon/miniconda3/envs/a2f/lib")  # cudart/cublas 등


def _trt_lib_dir() -> Path:
    hits = sorted(glob(str(SDK / "_deps" / "TensorRT-*" / "lib")))
    if not hits:
        raise FileNotFoundError("TensorRT lib dir not found under _deps/TensorRT-*/lib")
    return Path(hits[-1])


def _runtime_env() -> dict:
    """libaudio2x.so + TensorRT + CUDA 런타임 .so 를 찾도록 LD_LIBRARY_PATH 구성."""
    parts = [
        str(_BUILD / "audio2x-sdk" / "lib"),  # libaudio2x.so
        str(_trt_lib_dir()),
        str(_CONDA_ENV_LIB),
    ]
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = ":".join(parts + [env.get("LD_LIBRARY_PATH", "")]).rstrip(":")
    return env


def _to_pascal(name: str) -> str:
    return name[:1].upper() + name[1:] if name else name


def _resample_16k_mono(wav_path: str, dst: str) -> None:
    """ffmpeg 로 16kHz mono PCM wav 변환 (AudioFile 이 읽는 포맷)."""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", wav_path,
         "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", dst],
        check=True,
    )


def _parse_export(txt_path: str):
    """exporter 출력(text)을 (fps, names[list], frames[np.ndarray]) 로 파싱."""
    fps = FPS
    names = []
    rows = []
    with open(txt_path) as f:
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            if line.startswith("fps\t"):
                fps = float(line.split("\t", 1)[1])
            elif line.startswith("names\t"):
                names = line.split("\t")[1:]
            else:
                rows.append([float(x) for x in line.split("\t")])
    arr = np.asarray(rows, dtype=np.float32) if rows else np.zeros((0, len(names)), np.float32)
    return fps, names, arr


def audio_to_blendshapes(wav_path: str) -> dict:
    """음성 wav → ARKit 블렌드셰이프 타임시리즈.

    반환: {"fps": float, "names": list[str], "frames": list[list[float]]}  (frames: 프레임×52, 0~1)
    """
    wav_path = str(wav_path)
    if not EXPORTER.exists():
        raise FileNotFoundError(f"exporter 미빌드: {EXPORTER}")
    if not MODEL_JSON.exists():
        raise FileNotFoundError(f"모델 데이터 미생성: {MODEL_JSON}")

    with tempfile.TemporaryDirectory() as td:
        wav16 = os.path.join(td, "in16k.wav")
        out_txt = os.path.join(td, "bs.txt")
        _resample_16k_mono(wav_path, wav16)
        subprocess.run(
            [str(EXPORTER), str(MODEL_JSON), wav16, out_txt, str(int(FPS))],
            check=True, env=_runtime_env(),
        )
        fps, sdk_names, arr = _parse_export(out_txt)

    # SDK camelCase pose 이름 → PascalCase, ARKIT_NAMES(52) 순서로 재배열/선택.
    pas = [_to_pascal(n) for n in sdk_names]
    col = {n: i for i, n in enumerate(pas)}
    # 이름-열 정렬 검증: 매핑 실패한 채널은 조용히 0이 되므로 명시적으로 경고.
    # (mark v2.3 는 52 전부 매핑됨. claire/james/v3.0 등 교체 시 드리프트 감지용.)
    missing = [t for t in ARKIT_NAMES if t not in col]
    if missing:
        sys.stderr.write(
            f"[a2f_source] 경고: exporter 출력에 없는 ARKit 채널 {len(missing)}개를 0으로 채움: {missing}\n")
    n_frames = arr.shape[0]
    frames = np.zeros((n_frames, N_ARKIT), dtype=np.float32)
    for j, target in enumerate(ARKIT_NAMES):
        if target in col:
            frames[:, j] = arr[:, col[target]]
    frames = np.clip(frames, 0.0, 1.0)

    return {
        "fps": float(fps),
        "names": list(ARKIT_NAMES),
        "frames": frames.astype(float).tolist(),
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python a2f_source.py <wav_path>")
        sys.exit(1)
    wav = sys.argv[1]
    t0 = time.time()
    res = audio_to_blendshapes(wav)
    dt = time.time() - t0

    names, frames, fps = res["names"], res["frames"], res["fps"]
    n = len(frames)
    arr = np.array(frames) if n else np.zeros((1, len(names)))
    jaw = arr[:, names.index("JawOpen")]
    print(f"source            : audio2face-3d (mark v2.3, host bs-solve)")
    print(f"fps               : {fps}")
    print(f"channels          : {len(names)}")
    print(f"frames            : {n}  (~{n / fps:.2f}s @ {fps}fps)")
    print(f"value range (all) : [{arr.min():.4f}, {arr.max():.4f}]")
    print(f"JawOpen mean/std  : {jaw.mean():.4f} / {jaw.std():.4f}  (min {jaw.min():.4f}, max {jaw.max():.4f})")
    nonzero = [names[i] for i in range(len(names)) if arr[:, i].std() > 1e-4]
    print(f"active channels   : {len(nonzero)}  {nonzero[:12]}{'...' if len(nonzero) > 12 else ''}")
    print(f"infer time        : {dt:.3f}s  (프로세스 콜드스타트 포함)")
