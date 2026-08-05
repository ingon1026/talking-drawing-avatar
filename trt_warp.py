"""렌더 루프의 warping+spade 만 FasterLivePortrait 의 TRT 엔진으로 갈아끼운다.

실측(RTX 4070 Ti, 118프레임 4.7초 오디오): 전체 4.75s 중 warp_decode 가 3.13s(66%).
같은 입출력(feature_3d, kp_source, kp_driving → 1x3x512x512)을 가진 TRT 엔진이
15.5ms/frame 으로 torch.compile+fp16 의 24.6ms/frame 보다 1.6배 빠르다.

**여기만 바꾼다.** 모션 생성(JoyVASA 디퓨전)과 감정·깜빡임 주입은 delta_new 계산 단계라
손대지 않는다 — patches/joyvasa_inject.patch 의 두 hunk 는 그대로 유효하다.

엔진은 리포 밖(~/FasterLivePortrait)에 있고 **sm_89 전용**이다. 다른 GPU 로 옮기면
scripts/all_onnx2trt.sh 로 다시 뽑아야 한다. 없으면 torch 경로로 폴백한다.
"""
import ctypes
import os
from pathlib import Path

FLP = Path(os.environ.get("FLP_ROOT", Path.home() / "FasterLivePortrait"))
CK = FLP / "checkpoints" / "liveportrait_onnx"
ENGINE = CK / "warping_spade-fix.trt"
PLUGIN = CK / "libgrid_sample_3d_plugin.so"   # GridSample3D 커스텀 op — 이게 없으면 엔진이 안 뜬다


def available() -> bool:
    if not (ENGINE.exists() and PLUGIN.exists()):
        return False
    try:
        import tensorrt_libs  # noqa: F401
    except ImportError:
        return False
    return True


def _load_libs():
    """libnvinfer 의존 라이브러리를 순서대로 프로세스에 올린다.

    tensorrt-libs 를 --no-deps 로 깔아서(토치의 cudnn 8.9 와 충돌시키지 않으려고)
    로더가 없다. LD_LIBRARY_PATH 대신 여기서 직접 올려야 systemd 서비스에서도 뜬다.
    cudnn/cublas 는 토치가 lazy 로드라 아직 안 올라와 있을 수 있어 먼저 올린다.
    """
    import nvidia
    import tensorrt_libs
    nv = Path(nvidia.__file__).parent
    for so in (nv / "cublas/lib/libcublas.so.12", nv / "cublas/lib/libcublasLt.so.12",
               nv / "cudnn/lib/libcudnn.so.8"):
        if so.exists():
            ctypes.CDLL(str(so), mode=ctypes.RTLD_GLOBAL)
    libs = Path(tensorrt_libs.__file__).parent
    for so in ("libnvinfer.so.8", "libnvinfer_plugin.so.8"):
        ctypes.CDLL(str(libs / so), mode=ctypes.RTLD_GLOBAL)
    ctypes.CDLL(str(PLUGIN), mode=ctypes.RTLD_GLOBAL)


class TrtWarpDecode:
    """LivePortraitWrapper.warp_decode 와 같은 시그니처의 대체품."""

    def __init__(self):
        import torch
        _load_libs()
        import tensorrt_bindings.tensorrt as trt

        logger = trt.Logger(trt.Logger.ERROR)
        trt.init_libnvinfer_plugins(logger, "")
        self.engine = trt.Runtime(logger).deserialize_cuda_engine(ENGINE.read_bytes())
        if self.engine is None:
            raise RuntimeError(f"TRT 엔진 로드 실패: {ENGINE} (GPU 아키텍처 불일치?)")
        self.ctx = self.engine.create_execution_context()

        # 엔진은 정적 shape fp32 — 버퍼를 한 번 잡고 매 프레임 재사용한다.
        # (토치 쪽은 half 라 매번 .float() 로 옮겨 담는다. 8MB/frame, 0.1ms 수준)
        dev = "cuda"
        self.f = torch.empty(1, 32, 16, 64, 64, dtype=torch.float32, device=dev)
        self.ks = torch.empty(1, 21, 3, dtype=torch.float32, device=dev)
        self.kd = torch.empty(1, 21, 3, dtype=torch.float32, device=dev)
        self.out = torch.empty(1, 3, 512, 512, dtype=torch.float32, device=dev)
        idx = {self.engine.get_binding_name(i): i for i in range(self.engine.num_bindings)}
        self.bind = [0] * self.engine.num_bindings
        for name, t in (("feature_3d", self.f), ("kp_source", self.ks),
                        ("kp_driving", self.kd), ("out", self.out)):
            self.bind[idx[name]] = t.data_ptr()

    def __call__(self, feature_3d, kp_source, kp_driving):
        import torch
        self.f.copy_(feature_3d)
        self.ks.copy_(kp_source)
        self.kd.copy_(kp_driving)
        # 토치의 현재 스트림에 태워야 앞뒤 copy_ / .cpu() 와 순서가 보장된다.
        self.ctx.execute_async_v2(self.bind, torch.cuda.current_stream().cuda_stream)
        return {"out": self.out}


if __name__ == "__main__":
    # 자체 확인: 엔진이 뜨고 결과가 유한한 픽셀 범위인지 (GPU 필요)
    import torch
    assert available(), f"엔진 없음: {ENGINE}"
    w = TrtWarpDecode()
    o = w(torch.randn(1, 32, 16, 64, 64, device="cuda"),
          torch.randn(1, 21, 3, device="cuda"), torch.randn(1, 21, 3, device="cuda"))["out"]
    torch.cuda.synchronize()
    assert o.shape == (1, 3, 512, 512) and torch.isfinite(o).all(), o.shape
    print(f"trt_warp OK  range=[{o.min():.2f}, {o.max():.2f}]")
