# Speech recognition

## Goal

Turn extracted audio into timed cues in the source language, locally, using
faster-whisper on the CTranslate2 backend (no torch). Also owns the CUDA
runtime shim, which exists because the failure it prevents is deeply
misleading. Serves `M1`.

## Status

`done` — CPU and CUDA both work, progress is reported on a feature-length file,
and loader failures are translated into something actionable.

## Code Structure

| File | Role |
| ---- | ---- |
| `ffmpegsrt/transcribe.py` | Model loading, decoding, progress, CUDA preloading, error explanation |

## Key Types and Entry Points

- `ffmpegsrt/transcribe.py:135` — `transcribe(audio, ...)` — returns a
  `Transcript`. Sets `condition_on_previous_text=False`: Whisper's context
  carry-over is the usual cause of a line repeating for minutes on a long
  feature, and cutting it trades a little coherence for avoiding that.
- `ffmpegsrt/transcribe.py:126` — `Transcript(cues, language,
  language_probability, duration)`.
- `ffmpegsrt/transcribe.py:46` — `preload_cuda_libraries()` — `pip install
  nvidia-cublas-cu12 nvidia-cudnn-cu12` puts the shared objects under
  `site-packages/nvidia/*/lib`, which is not on the loader's search path.
  CTranslate2 asks for them by bare soname. Loading them here with
  `RTLD_GLOBAL` makes its later `dlopen` resolve to the copy already in memory,
  so no `LD_LIBRARY_PATH` is needed. Failures are ignored — the libraries may
  legitimately be installed system-wide.
- `ffmpegsrt/transcribe.py:28` — `_CUDA_SONAMES` — **order matters**: cuDNN
  links against cuBLAS, so cuBLAS has to land first.
- `ffmpegsrt/transcribe.py:83` — `_explain(exc, device)` — turns
  "Library libcublas.so.12 is not found" into the `pip install` line that fixes
  it. The raw message arrives *after* the model has apparently loaded, so it
  reads as a decode bug rather than a missing CUDA runtime.
- `ffmpegsrt/transcribe.py:115` — `default_compute_type(device)` — `int8` on
  CPU (a large speedup for a small accuracy cost), `float16` on GPU (both faster
  and more accurate, so there is no reason to pay int8's cost there).
- `ffmpegsrt/transcribe.py:201` — the decode loop, wrapped in its own
  `RuntimeError` handler because CTranslate2 defers loading its CUDA libraries
  until the first GPU op — which happens mid-iteration, not at construction.
- `ffmpegsrt/transcribe.py:16` — `KNOWN_MODELS` — help text only; faster-whisper
  also accepts a local path or any CTranslate2 repo id.

## Interactions

- Called by [cli.md](cli.md), which passes a throttled progress callback because
  faster-whisper yields segments lazily.
- Consumes audio produced by [media.md](media.md).
- Produces `Cue` objects owned by [subtitles.md](subtitles.md); when
  `--sound-tags` is on, its output is then passed through `sound.classify`.
- Raises `TranscriptionError`, a subclass of the base in [config.md](config.md).

## How to Test

```sh
python3 test/test_units.py    # pass = exit 0, "OK"
```

- Compute-type defaults, offline:
  `python3 -c "from ffmpegsrt import transcribe as t; print(t.default_compute_type('cpu'), t.default_compute_type('cuda'))"`
  — pass = `int8 float16`.
- The dependency is importable:
  `python3 -c "import faster_whisper; print('ok')"` — pass = `ok`.
- Real recognition needs media and downloads a model; it is covered by
  `test/make_sample.sh` + `test/run_test.sh` — pass = the run reports
  `asr : N cues` with `N > 0`.

## Open Gaps / Roadmap

- **No offline test of `transcribe()` itself.** Every assertion here needs a
  model download and real audio; a fake `WhisperModel` would make the cue-building
  and error-explanation paths reachable.
- `--device auto` is accepted by the parser and passed straight through to
  faster-whisper; ffmpegsrt does not itself detect whether a GPU is usable, so
  `default_compute_type("auto")` returns `float16` regardless.
- Word-level timestamps are not requested, so cue boundaries are segment-level.
- The `on_progress` callback reports the last cue's end against
  `info.duration`; with VAD on, long silences make that jump rather than climb.
