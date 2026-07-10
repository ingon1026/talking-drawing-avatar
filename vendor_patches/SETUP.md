# A2F-3D 로컬 빌드 재현 (WSL2, RTX 4070 Ti, sm_89)

말하는 아바타 프로젝트의 `/home/ingon/face/a2f_source.py` 가 쓰는 로컬 A2F-3D 추론
파이프라인을 처음부터 재현하는 절차. 이미 빌드된 상태면 이 문서는 참고용.

## 1. conda 빌드 환경 (시스템 apt 미사용, .venv 불변)
```bash
conda create -y -n a2f -c nvidia -c conda-forge \
  python=3.10 pip numpy cuda-toolkit=12.9.2 gcc=13 gxx=13 cmake ninja
conda install -y -n a2f -c nvidia "cuda-nvtx-dev=12.9.*"   # nvtx3/nvToolsExt.h (SDK가 요구)
```

## 2. 빌드 의존성 (packman) + TensorRT tar (로그인 불필요 공개 URL, ~6.5GB)
```bash
cd /home/ingon/face/Audio2Face-3D-SDK
./fetch_deps.sh release            # gtest/eigen/cnpy/tbtsvd + cmake3.24/ninja
curl -L -o /tmp/trt.tar.gz \
  https://developer.download.nvidia.com/compute/machine-learning/tensorrt/10.13.3/tars/TensorRT-10.13.3.9.Linux.x86_64-gnu.cuda-12.9.tar.gz
tar -xzf /tmp/trt.tar.gz -C _deps/ && rm /tmp/trt.tar.gz
# 용량 절약: _deps/TensorRT-*/{doc,samples,python,data}, targets/.../lib/*.a,
#           libnvinfer_builder_resource_win.* 삭제 가능
```

## 3. conda nvcc 배선 픽스 (핵심 — 안 하면 CMake CUDA 검출 실패)
conda cuda-toolkit 은 `$CONDA_PREFIX/targets/x86_64-linux/bin/nvcc` 를 canonical 로
쓰는데 이 디렉토리에 nvcc.profile / crt / nvvm 이 없어 include/cicc/link 가 깨진다.
```bash
P=/home/ingon/miniconda3/envs/a2f
ln -sfn $P/bin/crt          $P/targets/x86_64-linux/bin/crt
ln -sfn $P/nvvm             $P/targets/x86_64-linux/nvvm
ln -sfn $P/bin/nvcc.profile $P/targets/x86_64-linux/bin/nvcc.profile
```
추가로 빌드 시 `NVCC_PREPEND_FLAGS="-I$P/targets/x86_64-linux/include"` export (build_sdk.sh 가 처리).

## 4. SDK 빌드 (exporter 타깃만; libaudio2x.so 포함 빌드됨)
```bash
./_project_build/build_sdk.sh      # sm_89 전용, TensorRT::TensorRT 링크
# 산출물: _build/release/audio2face-sdk/bin/sample-a2f-blendshape-export
#         _build/release/audio2x-sdk/lib/libaudio2x.so
```
커스텀 exporter 소스: `audio2face-sdk/source/samples/sample-a2f-blendshape-export/`
(스톡 샘플은 프레임 수만 세고 weight 미출력이라 신규 작성. blendshape solve executor +
host solver + pose 이름을 modelInfo 에서 직접 취득해 텍스트로 덤프).

## 5. 모델 (비게이트 회귀 mark v2.3, 306MB) → TRT 변환
```bash
python -c "from huggingface_hub import snapshot_download; snapshot_download('nvidia/Audio2Face-3D-v2.3-Mark', local_dir='_data/audio2face-models/audio2face-3d-v2.3-mark')"
./_project_build/gen_run.sh        # trtexec 로 network.trt 생성 + a2f_ms_config 배선
# 산출물: _data/generated/audio2face-sdk/samples/data/mark/model.json (+ network.trt)
```
A2E(감정)는 게이트 리포라 스킵 — a2f_source 는 zero-emotion 으로 geometry+blendshape 만 구동.

## 6. 확인
```bash
/home/ingon/face/.venv/bin/python /home/ingon/face/a2f_source.py <wav>
```
