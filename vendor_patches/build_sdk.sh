#!/bin/bash
set -e
source /home/ingon/miniconda3/etc/profile.d/conda.sh
conda activate a2f

SDK="/home/ingon/face/Audio2Face-3D-SDK"
cd "$SDK"
export TENSORRT_ROOT_DIR="$(ls -d "$SDK"/_deps/TensorRT-*/ | head -1 | sed 's:/*$::')"
export CUDA_PATH="$CONDA_PREFIX"
export CUDA_HOME="$CONDA_PREFIX"
export CUDACXX="$CONDA_PREFIX/bin/nvcc"
CUDA_INC="$CONDA_PREFIX/targets/x86_64-linux/include"
CUDA_LIBDIR="$CONDA_PREFIX/targets/x86_64-linux/lib"
# nvcc.profile's auto-include isn't applied inside CMake's compiler-id subprocess;
# NVCC_PREPEND/APPEND_FLAGS are inherited by every nvcc invocation and fix it reliably.
export NVCC_PREPEND_FLAGS="-I$CUDA_INC"
export NVCC_APPEND_FLAGS="-L$CUDA_LIBDIR"
echo "TENSORRT_ROOT_DIR=$TENSORRT_ROOT_DIR"
echo "CUDA_PATH=$CUDA_PATH  CUDA_INC=$CUDA_INC"
echo "nvcc: $(which nvcc)  |  gcc: $CXX"

CMAKE="$SDK/_deps/build-deps/cmake/bin/cmake"
# CMake may pick the targets/.../bin/nvcc whose nvcc.profile fails to locate cicc
# (empty _TARGET_DIR_). Put nvvm/bin on PATH so cicc/nvvm tools are always found.
export PATH="$SDK/_deps/build-deps/ninja:$CONDA_PREFIX/nvvm/bin:$CONDA_PREFIX/targets/x86_64-linux/nvvm/bin:$PATH"

BUILD_DIR="$SDK/_build/release"
rm -rf "$BUILD_DIR"   # clear stale CUDA-detect cache
"$CMAKE" -B "$BUILD_DIR" -G Ninja -S "$SDK" \
  -DCMAKE_BUILD_TYPE=Release \
  -DTEST_DATA_DIR="$SDK" \
  -DTENSORRT_ROOT_DIR="$TENSORRT_ROOT_DIR" \
  -DCUDAToolkit_ROOT="$CONDA_PREFIX" \
  -DCMAKE_CUDA_COMPILER="$CONDA_PREFIX/bin/nvcc" \
  -DCMAKE_CUDA_ARCHITECTURES=89 \
  -DCMAKE_C_COMPILER="$CC" \
  -DCMAKE_CXX_COMPILER="$CXX" \
  -DCMAKE_CUDA_HOST_COMPILER="$CXX" \
  -DCMAKE_CUDA_FLAGS="-I$CUDA_INC" \
  -DCMAKE_CXX_FLAGS="-I$CUDA_INC" \
  -DCMAKE_EXE_LINKER_FLAGS="-L$CUDA_LIBDIR -Wl,-rpath,$CUDA_LIBDIR" \
  -DCMAKE_SHARED_LINKER_FLAGS="-L$CUDA_LIBDIR -Wl,-rpath,$CUDA_LIBDIR"

echo "=== CONFIG DONE, building exporter target (+ audio2x dep) ==="
"$CMAKE" --build "$BUILD_DIR" --target sample-a2f-blendshape-export --parallel
echo "=== BUILD DONE ==="
ls -la "$BUILD_DIR/audio2face-sdk/bin/" 2>/dev/null | grep blendshape || true
ls -la "$BUILD_DIR/audio2x-sdk/lib/" 2>/dev/null | grep -i "\.so" || true
