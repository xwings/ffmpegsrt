---
eatmycode_version: "2.1.0"
---
# Transcribe

Owner: [Project architecture](../../ARCHITECTURE.md)

Read when: touching `ffmpegsrt/transcribe.py`; changing Whisper decode options, model loading, compute types, CUDA runtime handling, or progress reporting.

## Responsibility and Status

Turns extracted audio into timed source-language cues locally with faster-whisper on CTranslate2, no torch. Also owns the CUDA runtime shim and the translation of loader errors into actionable messages.

Status: `in progress`. `default_compute_type` is unit-tested; `transcribe()` itself is unverified here because faster-whisper is not installed on this machine, and it is reachable only through the end-to-end pair. The README GPU section describes CUDA runs as working.

## Code Map

| Path / symbol | Role |
| --- | --- |
| `ffmpegsrt/transcribe.py:16` `KNOWN_MODELS` | Help text only; any local path or CTranslate2 repo id also works. |
| `ffmpegsrt/transcribe.py:28` `_CUDA_SONAMES` | cuBLAS, cuBLASLt, cuDNN, in dependency order. |
| `ffmpegsrt/transcribe.py:46` `preload_cuda_libraries` | `ctypes.CDLL(..., RTLD_GLOBAL)` from `site-packages/nvidia/*/lib`; failures ignored. |
| `ffmpegsrt/transcribe.py:83` `_explain` | Maps loader, out-of-memory and no-device messages to the `pip install` or `--device cpu` fix. |
| `ffmpegsrt/transcribe.py:115` `default_compute_type` | `int8` on CPU, `float16` otherwise. |
| `ffmpegsrt/transcribe.py:135` `transcribe` | Loads the model, decodes lazily, builds `Cue`s, returns `Transcript` (`:126`). |
| `test/test_units.py:414` `test_default_compute_type` | The only offline test of this module. |

## Local Conventions

Root baseline suffices. `faster_whisper` is imported inside `transcribe()` so the rest of the tool works without it. Do not import torch or add a GPU-detection library.

## Contracts and Invariants

- **Input**: an audio path (mono 16 kHz PCM from [Media](media.md) is ideal), ISO 639-1 code or `None` for detection, model name, `device` in `cpu|cuda|auto`, optional compute type, beam size, VAD flag, `on_progress(cue, duration)`.
- **Output**: `Transcript(cues, language, language_probability, duration)`. Empty segments are skipped; zero cues raises `TranscriptionError` ("no speech was recognised").
- **`condition_on_previous_text=False`** (`ffmpegsrt/transcribe.py:193`) is deliberate: context carry-over is the usual cause of one line repeating for minutes on a feature.
- **CUDA shim**: on any non-CPU device the wheels' shared objects are preloaded before `WhisperModel` is built. CTranslate2 loads CUDA libraries at the first GPU op, mid-iteration, so the decode loop has its own `RuntimeError` handler (`:209`).
- **VAD on by default** (`--no-vad` disables) keeps Whisper from hallucinating dialogue over music, at the cost of filtering most non-speech markers.
- **Failures** raise `TranscriptionError(FfmpegSrtError)` with `_explain` appended; a missing import names `requirements.txt`.
- **Performance**: `small` on CPU runs at roughly real time; a feature film is an hours-long job. Progress is one callback per segment; the caller throttles.

## Dependencies and Boundaries

Incoming: [CLI](cli.md). Outgoing: `faster_whisper` (lazy), `ffmpegsrt/srt.py` for `Cue`, `ffmpegsrt/errors.py`. Cues are passed through `sound.classify` by the CLI, never here, so this module knows nothing about sound tags ([Subtitles](subtitles.md)).

## Change Guide

| Change trigger | Inspect / extend | Required docs / checks |
| --- | --- | --- |
| New decode option | `transcribe` kwargs, then a flag in [CLI](cli.md) `build_parser` | `TestGetCues` asserts `vad_filter` is passed; extend it |
| New CUDA library or version | `_CUDA_SONAMES` order, `_explain` patterns | `README.md` GPU section; `requirements.txt` comment |
| Model list | `KNOWN_MODELS` | `--help` output |
| Word-level timing or cue splitting | Changes cue count: read [Translate](translate.md) first | Root System Design |

## Verification

```sh
python3 -c "from ffmpegsrt import transcribe as t; print(t.default_compute_type('cpu'), t.default_compute_type('cuda'))"   # int8 float16
python3 -c "import faster_whisper; print('ok')"     # ok, once requirements.txt is installed
```

Real recognition: `./test/run_test.sh` reports `asr      : N cues` with `N > 0`; see [End-to-end test](../topics/end-to-end-test.md). Needs a model download and user-supplied media.

## Known Gaps

- No offline test of `transcribe()`; a fake `WhisperModel` would make cue building and `_explain` reachable.
- `--device auto` is passed through; `default_compute_type("auto")` returns `float16` without checking for a GPU.
- Segment-level timestamps only.
- With VAD on, the progress callback jumps across long silences rather than climbing.
