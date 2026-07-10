# vendor_patches

Audio2Face-3D-SDK(별도 클론, gitignore됨)에 얹는 커스텀 파일 보존본.

- `a2f-blendshape-export/` → `Audio2Face-3D-SDK/audio2face-sdk/source/samples/sample-a2f-blendshape-export/` 에 복사
  (wav → ARKit 블렌드셰이프 exporter, `--serve` 상주 모드 포함)
- `SETUP.md`, `build_sdk.sh` → `Audio2Face-3D-SDK/_project_build/` (재빌드 절차·함정 해결책)
