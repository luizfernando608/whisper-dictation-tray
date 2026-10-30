# Local Nemotron diarization validation

Validated on 2026-10-14, Windows x64, Intel Core Ultra 7 255H (32 GB RAM),
Intel Arc 140T integrated GPU. Default engine is local Nemotron; the separately
prepared Community-1 engine remains optional for guided speaker counts and
larger meetings. Neither default preparation nor inference requires a token.

## Publisher artifacts and distribution

- Model: [NVIDIA Nemotron-3-Diarization pinned model card](https://huggingface.co/nvidia/Nemotron-3-Diarization/blob/f667ed73aee57d40cc39428eb768b4fd87a0a29e/README.md),
  immutable revision `f667ed73aee57d40cc39428eb768b4fd87a0a29e`, Q8 GGUF,
  107,012,128 bytes, SHA256
  `08456d9e22cd9a323c0364d98375f3746d6e68507ebb705cd46438c534c7a3a1`.
- Model license: [OpenMDW 1.1](https://openmdw.ai/license/1-1/). The publisher
  model card and full license are retained beside the cached weights.
- Runtime: official [NVIDIA NeMo-Speech.cpp v0.2.0 release](https://github.com/NVIDIA/NeMo-Speech.cpp/releases/tag/v0.2.0),
  Apache-2.0, CPU ZIP 5,528,418 bytes and Vulkan ZIP 19,302,464 bytes.
  Binary downloads use pinned SHA256; extracted EXEs/DLLs are subsequently
  checked against the contents of those verified ZIPs. Runtime license/notices
  remain in each original extracted package at
  `share/licenses/nemo-speech/{LICENSE,NOTICE,THIRD_PARTY_NOTICES.md}` with the
  accompanying dependency licenses. A mutable cache manifest alone
  cannot authorize different weights or executable bytes.
- The CPU/Vulkan runtime pair plus model downloads total 131.8 MB (decimal),
  excluding optional ASR weights. Runtime binaries live in the model cache,
  outside PyInstaller `_internal`; the supervised PACE child process loads the
  corresponding runtime DLLs. Both source and frozen entry points use the same
  `--nemotron-runtime` contract.

## Complete speaker identity and acceleration

The trained model predicts up to eight speaker slots. PACE creates one model
and one persistent native stream for the entire recording. A five-second PCM
read is input transport, not an independently diarized fragment. Native state
and arrival-order speaker slots persist through subsequent pushes and history
compaction. Full-attention `--offline` mode is never enabled.

CPU uses the official SDK `gpu=-1` sentinel. Vulkan candidate devices are
enumerated by the official `doctor --json`, then tested individually. Each
candidate must finish genuine inference, preserve valid timestamps and speaker
IDs, and differ by at most 1% from the CPU reference at 10 ms resolution after
optimal speaker-label assignment. The benchmark measures the complete
diarization operation, including child initialization. Automatic selection
chooses the fastest accepted candidate and caches results by model, runtime,
hardware and driver identity. Longer recordings probe first/middle/last spoken
regions; failed GPUs do not prevent testing another GPU. Final accelerator
failure replays the complete recording on CPU with one persistent stream.

Drivers and inference run in a supervised child process. Cancellation/timeouts
terminate its owned process tree, including a Windows venv Python launcher and
its Python child. Progress files carry actual labeled-audio seconds. A temporary
Windows sharing violation when updating this advisory file cannot abort a job.

## Public trained fixture

[The publisher fixture README](https://github.com/NVIDIA/NeMo-Speech.cpp/blob/v0.2.0/test_files/diar/README.md)
attributes the 60-second `ami_en2002d_2132.wav` excerpt to the AMI Meeting Corpus,
CC BY 4.0; the matching RTTM is cut from BUTSpeechFIT AMI-diarization-setup,
Apache-2.0. This is public test audio, not user audio. Three people speak
substantially, and a fourth contributes only a few words.

| Actual trained run | Turns | IDs detected | Time | Human-reference DER, collar 0 | DER, collar 0.25 s |
| --- | ---: | ---: | ---: | ---: | ---: |
| Native CPU baseline | 25 | 3 | 1.625 s | 23.731% | 13.574% |
| Native Vulkan Intel GPU | 25 | 3 | 1.16 s warm | 23.710% | 13.574% |
| Bounded C API CPU | 25 | 3 | 1.937 s | Same turns as CPU baseline | Same turns |
| Bounded C API Vulkan | 25 | 3 | 1.219 s, independent reviewer | Same turns as Vulkan baseline | Same turns |

DER includes overlap, scored with pyannote.metrics 4.1 against the fixture
RTTM, using the union of reference/hypothesis extents as the scoring region;
it counts missed speech, false speech and incorrect speaker identity.
CPU/Vulkan disagreement is 0.021997%, passing the 1% accelerator-equivalence
gate. This low CPU/GPU discrepancy demonstrates equivalent execution, not a
13% or 0.02% universal accuracy guarantee. The rare fourth speaker was not
separated, and Portuguese, five-plus substantial speakers and other acoustic
conditions remain unmeasured.

The independent review reproduced CPU/Vulkan inference and checked the exact
SDK ABI against the official `include/nemo_speech/diar.h`. Root also ran the
full imported-recording pipeline with local Whisper tiny and Nemotron: `ready`,
18 timestamped transcription segments, 25 turns, three speaker IDs, Intel
Vulkan selected, without external credentials.

## Long recordings and memory

The initial official CLI test completed two hours of synthetic silence in
218.39 s but peaked at 2,331,148,288 bytes (~2.17 GiB). Its whole-file audio
preparation is unsuitable for the desired resource bounds. PACE therefore uses
the native C API with bounded PCM reads instead of invoking CLI `diarize`.
The C API 60-second CPU run peaked at 199,962,624 bytes (~190.7 MiB), measured
inside the Python process that actually hosts the DLL, not the ~4 MB launcher.

A 30-minute concatenation of the public AMI excerpt produced 770 turns and only
three global IDs. Native `retained_frame_start=118387` confirms history
compaction took place. Root compared early/late one-minute windows against the
first minute: optimal-label disagreement ranged approximately 1.4–3.1% through
the final minute. An independent reviewer also checked fixed IDs at offsets
60/600/1200/1740 seconds: disagreement was respectively
1.5321/1.4445/1.4664/3.1298%, equal to the optimal assignment result. No label
swap was detected at those sampled points after compaction.
This artificial repetition exercises stable IDs and compaction, not the quality
of an actual 30-minute meeting.

The bounded C API two-hour CPU memory stress **passed**: 217.64 seconds elapsed,
zero speaker turns on silence, native-host peak 207,106,048 bytes (~197.5 MiB),
720,001 frames, `retained_frame_start=611559`. Peak memory dropped ~91.1% versus
the initial whole-file CLI run while processing the same WAV. Sampling used
Windows GetProcessMemoryInfo inside the DLL-hosting process, eliminating the
launcher-only measurement error. Metrics and complete diagnostics are saved in
`temp/nemotron-prototype/stress-2h-capi/native-metrics.json` and
`stream-result.json`. This is native inference memory, not a claim about the
whole app, ASR model or GPU-driver allocations. Silence tests validate resource
behavior and absence of fabricated speaker turns; they do not validate long
conversational accuracy.

## Acceptance limitations

- At most eight inferred identities, with automatic count. Explicit count/range
  guidance belongs to optional Community-1; PACE does not create fake IDs to
  satisfy a count. A request above eight returns a clear engine-selection error.
- Speaker labels are identities within a recording, not verified personal names.
- Vulkan support is admitted only by a real local inference/equivalence gate.
  CPU works when the GPU or its driver is absent, incompatible or rejected.
- Current pinned native runtime package is Windows x64. CPU/Vulkan use no
  NVIDIA CUDA toolkit requirement; AMD/NVIDIA Vulkan hardware was not available
  for physical validation on this computer.
- Community-1 trained quality cannot be compared locally without that model's
  license acceptance/token. Its gated models remain optional and unborrowed.
- Ten focused unit tests cover public artifact hash rejection, cache tampering,
  ZIP paths, malformed timestamps, overlap, label equivalence, CPU sentinel,
  multi-GPU continuation, multiple probes, native cancel/timeouts and invalid
  manifest recovery through explicit model preparation.

Reproduction artifacts are under ignored `temp/nemotron-prototype` (fixture,
quality-report.json, C API results/metrics) and `temp/nemotron-continuity`.
Model cache, public WAV and generated stress WAV are not included in Git.
