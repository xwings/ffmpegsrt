# Testing

## Goal

Two tiers, split by what they cost. `test_units.py` covers the pure logic and
needs nothing — no ffmpeg, no model, no key — so it runs in under a second on
any checkout. The shell pair drives the whole pipeline against real media and
a real endpoint. Serves `M5`.

## Status

`in progress (M5)` — 45 unit tests pass. The end-to-end pair is written but
cannot run unattended: it needs a movie the user supplies (test media is
gitignored) and working translation credentials.

## Code Structure

| File | Role |
| ---- | ---- |
| `test/test_units.py` | Pure-logic tests. Plain `unittest`, so `python3` and `pytest` both run it |
| `test/make_sample.sh` | Cuts a short sample out of a movie into `test/sample.mp4` |
| `test/run_test.sh` | End-to-end: transcribe → translate → burn in, then asserts on both outputs |

## Key Types and Entry Points

- `test/test_units.py:19` — the `sys.path` insert that makes the suite runnable
  as a bare script, matching how `ffmpegsrt.py` bootstraps itself.
- `test/test_units.py:220` — `TestConfig.setUp` — clears `os.environ` and patches
  `config.PROJECT_ROOT` to an empty directory. Without it the checkout's real
  `.env` satisfies the test asserting that credentials are missing; the suite
  passed for the wrong reason once already.
- `TestCoerceLines` — the desync guard: a short list, a long list and a non-list
  must all coerce to `None`, never be padded.
- `TestShiftAndClip` — cues rebased onto a clip timeline, dropped outside the
  window, clamped when they straddle an edge.
- `TestSoundDetection` — the marker set, and that a tagged cue survives an SRT
  round trip so `--srt-in` restores it.
- `TestLanguages.test_bare_zh_is_simplified` — regression for the alias
  collision described in [config.md](config.md).
- `test/make_sample.sh:33` — defaults the cut to a third of the way in: opening
  credits are usually silent, and a sample with no dialogue tests nothing.
- `test/run_test.sh` — asserts the SRT parses, timings are monotonic and inside
  the sample, the text is in the target script (a still-ASCII "translation"
  means the stage silently fell through to source text), and the output video
  kept both streams at the sample's duration.

## Interactions

- Exercises [subtitles.md](subtitles.md), [config.md](config.md) and
  [translate.md](translate.md)'s `_coerce_lines` directly.
- Exercises the pure helpers of [media.md](media.md) and
  [transcribe.md](transcribe.md); their ffmpeg and model paths are reachable
  only from the shell pair.
- `run_test.sh` drives [cli.md](cli.md) end to end.

## How to Test

```sh
python3 test/test_units.py          # pass = exit 0, output ends in "OK"
pytest test/test_units.py           # same suite, if pytest is installed
```

```sh
./test/make_sample.sh path/to/movie.mp4 30   # pass = "ok: test/sample.mp4 (...)"
./test/run_test.sh ja zh_cn                  # pass = exit 0, final line "PASS"
```

- Shell syntax without executing:
  `bash -n test/make_sample.sh && bash -n test/run_test.sh` — pass = exit 0.

## Open Gaps / Roadmap

- **M5**: no fake provider, so [translate.md](translate.md)'s escalation ladder —
  retry, direct fallback, keep-source-text — has no automated coverage at all.
  This is the largest untested surface in the project.
- **M5**: `transcribe()` and the ffmpeg wrappers are only reachable through the
  shell pair, which needs media that cannot be committed.
- No CI. Nothing runs the unit suite on a push.
- `run_test.sh` costs real API calls; there is no dry-run mode.
- The target-script check only knows Chinese, Japanese and Korean ranges and
  silently skips any other target.
