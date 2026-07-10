// SPDX-License-Identifier: MIT
//
// Custom Audio2Face-3D blendshape exporter (added for the talking-avatar project).
// Runs the regression geometry model + host blendshape solver on a 16 kHz mono wav
// and writes the per-frame ARKit blendshape weights (skin poses) to a text file.
//
// Two modes:
//   single-shot:  sample-a2f-blendshape-export <model.json> <input_16k_mono.wav> <out.txt> [fps]
//   server:       sample-a2f-blendshape-export <model.json> --serve [fps]
//     Loads the TRT engine once, prints "READY" on stdout, then reads one request per
//     line from stdin ("<wav_path>\t<out_path>"), writes the result file, and prints
//     "DONE <out_path>" or "ERR <message>" on stdout. Exits on EOF. Diagnostics go to
//     stderr so they never collide with the stdout protocol.
//
// Output file format (ASCII, tab-separated):
//   line 1: "fps\t<fps>"
//   line 2: "names\t<name0>\t<name1>\t..."   (SDK pose names, e.g. jawOpen)
//   line 3+: one frame per line, "<w0>\t<w1>\t..."  (floats, weight per pose)

#include "audio2face/audio2face.h"
#include "audio2x/cuda_utils.h"

#include "AudioFile.h"

#include <algorithm>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

namespace {

struct Destroyer {
  template <typename T> void operator()(T* obj) const { obj->Destroy(); }
};
template <typename T> using UniquePtr = std::unique_ptr<T, Destroyer>;
template <typename T> UniquePtr<T> ToUniquePtr(T* ptr) { return UniquePtr<T>(ptr); }

// Fatal check for one-time setup: prints and aborts main() with code 1.
#define CHECK(func)                                                             \
  {                                                                            \
    std::error_code _e = (func);                                               \
    if (_e) {                                                                  \
      std::cerr << "Error (" << __LINE__ << "): " << #func                     \
                << " -> " << _e.message() << std::endl;                        \
      return 1;                                                                \
    }                                                                          \
  }

// Per-request check: returns an error string (non-empty) instead of aborting, so the
// server loop can report the failure and keep serving.
#define FAIL_IF(func)                                                           \
  {                                                                            \
    std::error_code _e = (func);                                               \
    if (_e) return std::string(#func) + " -> " + _e.message();                 \
  }

struct Collector {
  std::mutex mtx;
  std::vector<std::pair<std::int64_t, std::vector<float>>> frames;
};

void hostResultsCallback(void* userdata,
                         const nva2f::IBlendshapeExecutor::HostResults& results,
                         std::error_code errorCode) {
  if (errorCode) {
    return;
  }
  auto* collector = static_cast<Collector*>(userdata);
  const float* d = results.weights.Data();
  std::vector<float> w(d, d + results.weights.Size());
  std::lock_guard<std::mutex> lock(collector->mtx);
  collector->frames.emplace_back(results.timeStampCurrentFrame, std::move(w));
}

// Run one inference on an already-created bundle and write the result file.
// Resets the executor and both accumulators up front so the (expensive) bundle/TRT
// engine can be reused across many requests. Returns "" on success, else a message.
std::string runInference(nva2f::IBlendshapeExecutorBundle& bundle,
                         nva2f::IBlendshapeExecutor& executor,
                         const std::vector<std::string>& poseNames,
                         std::size_t fps,
                         const std::string& wavPath,
                         const std::string& outPath,
                         Collector& collector) {
  // Reset for reuse. Harmless on the very first call (freshly created bundle). The
  // prior request already ran Wait()+Synchronize(), so nothing is in flight here.
  FAIL_IF(executor.Reset(0));
  FAIL_IF(bundle.GetAudioAccumulator(0).Reset());
  FAIL_IF(bundle.GetEmotionAccumulator(0).Reset());
  {
    std::lock_guard<std::mutex> lock(collector.mtx);
    collector.frames.clear();
  }
  // Results callback is (re)set each call; cheap and robust to Reset() clearing it.
  FAIL_IF(executor.SetResultsCallback(hostResultsCallback, &collector));

  AudioFile<float> audioFile;
  if (!audioFile.load(wavPath)) return "Failed to load wav: " + wavPath;
  if (audioFile.getSampleRate() != 16000) {
    return "Expected 16000 Hz, got " + std::to_string(audioFile.getSampleRate());
  }
  const std::vector<float>& audio = audioFile.samples[0];
  if (audio.empty()) return "Empty audio: " + wavPath;

  // Default (neutral) emotion: a single zero vector at t=0, then close.
  {
    auto& emo = bundle.GetEmotionAccumulator(0);
    std::vector<float> zero(emo.GetEmotionSize(), 0.0f);
    FAIL_IF(emo.Accumulate(0, nva2x::HostTensorFloatConstView{zero.data(), zero.size()},
                           bundle.GetCudaStream().Data()));
    FAIL_IF(emo.Close());
  }

  // Accumulate the whole audio track and close.
  FAIL_IF(bundle.GetAudioAccumulator(0).Accumulate(
      nva2x::HostTensorFloatConstView{audio.data(), audio.size()},
      bundle.GetCudaStream().Data()));
  FAIL_IF(bundle.GetAudioAccumulator(0).Close());

  // Process everything.
  while (nva2x::GetNbReadyTracks(executor) > 0) {
    FAIL_IF(executor.Execute(nullptr));
  }
  FAIL_IF(bundle.GetCudaStream().Synchronize());
  FAIL_IF(executor.Wait(0));

  // Order frames by timestamp (host callbacks may arrive from worker threads).
  std::sort(collector.frames.begin(), collector.frames.end(),
            [](const auto& a, const auto& b) { return a.first < b.first; });

  std::ofstream out(outPath);
  if (!out) return "Cannot open output: " + outPath;
  out << "fps\t" << fps << "\n";
  out << "names";
  for (const auto& n : poseNames) out << "\t" << n;
  out << "\n";
  out.setf(std::ios::fixed);
  out.precision(6);
  for (const auto& [ts, w] : collector.frames) {
    for (std::size_t i = 0; i < w.size(); ++i) {
      if (i) out << "\t";
      out << w[i];
    }
    out << "\n";
  }
  out.close();

  std::cerr << "Wrote " << collector.frames.size() << " frames x " << poseNames.size()
            << " poses @ " << fps << " fps to " << outPath << std::endl;
  return "";
}

}  // namespace

int main(int argc, char** argv) {
  if (argc < 3) {
    std::cerr << "Usage:\n"
              << "  " << argv[0] << " <model.json> <input_16k_mono.wav> <out.txt> [fps]\n"
              << "  " << argv[0] << " <model.json> --serve [fps]" << std::endl;
    return 2;
  }
  const std::string modelPath = argv[1];
  const bool serve = (std::string(argv[2]) == "--serve");

  std::size_t fps = 60u;
  std::string wavPath, outPath;
  if (serve) {
    if (argc > 3) fps = static_cast<std::size_t>(std::stoul(argv[3]));
  } else {
    if (argc < 4) {
      std::cerr << "Usage: " << argv[0]
                << " <model.json> <input_16k_mono.wav> <out.txt> [fps]" << std::endl;
      return 2;
    }
    wavPath = argv[2];
    outPath = argv[3];
    if (argc > 4) fps = static_cast<std::size_t>(std::stoul(argv[4]));
  }

  CHECK(nva2x::SetCudaDeviceIfNeeded(0));

  using ExecOpt = nva2f::IGeometryExecutor::ExecutionOption;
  const ExecOpt execOption = ExecOpt::Skin;  // skin poses = 52 ARKit blendshapes

  // Create the regression blendshape-solve executor bundle (host solver => CPU results).
  // This loads the TensorRT engine; in --serve mode it happens exactly once.
  nva2f::IRegressionModel::IGeometryModelInfo* geoInfoRaw = nullptr;
  nva2f::IRegressionModel::IBlendshapeSolveModelInfo* bsInfoRaw = nullptr;
  auto bundle = ToUniquePtr(nva2f::ReadRegressionBlendshapeSolveExecutorBundle(
      /*nbTracks=*/1, modelPath.c_str(), execOption, /*useGpuSolver=*/false,
      /*frameRateNumerator=*/fps, /*frameRateDenominator=*/1, &geoInfoRaw, &bsInfoRaw));
  if (!bundle) {
    std::cerr << "ReadRegressionBlendshapeSolveExecutorBundle returned null. "
              << "Check model.json / network.trt paths." << std::endl;
    return 1;
  }
  UniquePtr<nva2f::IRegressionModel::IGeometryModelInfo> geoInfo(geoInfoRaw);
  UniquePtr<nva2f::IRegressionModel::IBlendshapeSolveModelInfo> bsInfo(bsInfoRaw);

  auto& executor = bundle->GetExecutor();

  // Pose names come straight from the model info so the weight order and the
  // reported names are guaranteed to match.
  std::vector<std::string> poseNames;
  if (bsInfo) {
    const auto params = bsInfo->GetExecutorCreationParameters(execOption);
    const auto* skin = params.initializationSkinParams;
    if (skin != nullptr && skin->data.poseNames != nullptr) {
      for (std::size_t i = 0; i < skin->data.poseNamesSize; ++i) {
        poseNames.emplace_back(skin->data.poseNames[i]);
      }
    }
  }
  const std::size_t weightCount = executor.GetWeightCount();
  if (poseNames.size() != weightCount) {
    std::cerr << "Warning: poseNames(" << poseNames.size() << ") != weightCount("
              << weightCount << "). Emitting indices for missing names." << std::endl;
    while (poseNames.size() < weightCount) {
      poseNames.emplace_back("pose_" + std::to_string(poseNames.size()));
    }
    poseNames.resize(weightCount);
  }

  if (executor.GetResultType() != nva2f::IBlendshapeExecutor::ResultsType::HOST) {
    std::cerr << "Expected HOST results (useGpuSolver=false)." << std::endl;
    return 1;
  }

  Collector collector;

  if (serve) {
    // Signal that the engine is loaded and we are ready to accept requests.
    std::cout << "READY" << std::endl;
    std::string line;
    while (std::getline(std::cin, line)) {
      if (line.empty()) continue;
      const auto tab = line.find('\t');
      if (tab == std::string::npos) {
        std::cout << "ERR malformed request (expected <wav>\\t<out>)" << std::endl;
        continue;
      }
      const std::string reqWav = line.substr(0, tab);
      const std::string reqOut = line.substr(tab + 1);
      const std::string err =
          runInference(*bundle, executor, poseNames, fps, reqWav, reqOut, collector);
      if (err.empty()) {
        std::cout << "DONE " << reqOut << std::endl;
      } else {
        std::cout << "ERR " << err << std::endl;
      }
    }
    return 0;
  }

  // Single-shot mode.
  const std::string err =
      runInference(*bundle, executor, poseNames, fps, wavPath, outPath, collector);
  if (!err.empty()) {
    std::cerr << err << std::endl;
    return 1;
  }
  return 0;
}
