---
eatmycode_version: "2.1.0"
---
# ffmpegsrt Architecture

Generated with [eatmycode](https://github.com/xwings/eatmycode).

## Read First

Before planning code changes or reviewing code, read
[Agent Rules](ARCHITECTURE/AGENT_RULES.md). Follow the Task Index to the
owning module and read pages whose **Read when** trigger matches the task.
Load partner modules only for affected boundaries; never load the entire
ARCHITECTURE directory. Reuse unchanged pages already read in this session.
Check claims against source, configuration, and tests; they remain
authoritative. If a route or fact is missing or stale, inspect source and
repair the affected docs. For broad changes, work through owners in batches
and retain cross-owner constraints and verification evidence.

## Project Snapshot

| Fact | Value and evidence |
| --- | --- |
| Purpose | Transcribe a film locally, translate the cues, write an SRT and/or burn it in. Usage examples: `ffmpegsrt/cli.py:18`. |
| Languages | Python (`ffmpegsrt/`, `test/test_units.py`); bash (`test/*.sh`); YAML+Markdown gameplan (`harness/`). |
| Runtime | Observed 3.10+ (PEP 604 unions; `test/test_units.py:257`). Undeclared; run here on 3.13. |
| Dependencies | `requirements.txt`: `faster-whisper>=1.2`. Kerness: Rust submodule `vendor/kerness`, built with pip (rustc 1.88+, see Translate). Optional CUDA 12 wheels for `--device cuda`. |
| Tools, platform | `ffmpeg`/`ffprobe` on `PATH` (`ffmpegsrt/media.py:43`) with libx265 and libass. Linux, macOS, WSL2 observed; Windows untested. |
| Build | None; run `ffmpegsrt.py` from the checkout. No pyproject or CI. |
| Non-goals | No cue merging, splitting or reordering; no speaker labels or stream selection. |

## System Design

One process, no persistent state; `cli.run` owns the stage order:

```
probe/trim -> (read_srt+shift_and_clip | extract_audio -> transcribe) -> classify
           -> write_srt (checkpoint) -> translate -> write_srt -> burn_in
```

- **Cue contract.** A `Cue` (`ffmpegsrt/srt.py:16`) is timed against the picture; nothing downstream may add, drop, merge or reorder cues. Translation rejects a wrong-length result (`ffmpegsrt/translate.py:192`).
- **Dependencies.** `cli` imports everything; `translate` uses `sound`, `config`, `langs`, `srt`; `transcribe` and `sound` use `srt`; `srt.py` has no project imports.
- **Order** (`ffmpegsrt/cli.py:166`, `:200`): credentials before transcription; the source transcript is written before translation so `--srt-in` resumes it.
- **Trust.** Only translation touches the network. Keys come from flags, env or gitignored `.env`, redacted in `LLMConfig.__repr__`. ffmpeg runs from argv lists, never a shell.
- **Failures** raise `FfmpegSrtError` and print one line (`ffmpegsrt/cli.py:359`); an exhausted translation batch keeps source text.

## Code Conventions

| Area | Convention (observed unless noted) |
| --- | --- |
| Layout, naming | One flat module per stage in `ffmpegsrt/`; `snake_case` files and functions, `CapWords` dataclasses, `_underscore` private helpers. `vendor/kerness`: bump the pin, never edit. |
| Typing | `from __future__ import annotations`; PEP 604 unions; keyword-only options after `*` (`ffmpegsrt/media.py:104`). |
| Errors, output | One `FfmpegSrtError` subclass per module; messages name the fix. Progress goes to stderr via `cli._log`; no `logging`. |
| Comments, tests | Docstrings and `#:` notes explain *why*. Plain `unittest`, one file, one class per subject. No formatter or linter. |
| Project rules (required) | **Never invent a sound** (`ffmpegsrt/sound.py:10`). **Never widen the cue contract silently**: document cue count or order changes in [Translate](ARCHITECTURE/modules/translate.md) first. **Never commit a credential**: `.env*` is gitignored. |

## Verification

| Change/check | Command and working directory | Prerequisites / pass evidence |
| --- | --- | --- |
| Any Python change | `python3 test/test_units.py` (repo root) | Nothing external. 49 tests, exit 0, `OK`. |
| CLI wiring | `python3 ffmpegsrt.py --help && python3 ffmpegsrt.py --version` | Exit 0; `ffmpegsrt 0.1.0`. |
| ffmpeg path, no network | Synthetic runs in [CLI](ARCHITECTURE/modules/cli.md#verification), [Media](ARCHITECTURE/modules/media.md#verification) | `ffmpeg` on PATH. |
| Shell scripts | `bash -n test/make_sample.sh test/run_test.sh` | Exit 0. |
| Real media and endpoint | `./test/make_sample.sh MOVIE 30 && ./test/run_test.sh ja zh_cn` | Model, `.env`, a movie; final `PASS`. [End-to-end test](ARCHITECTURE/topics/end-to-end-test.md). |

No CI. No offline coverage of `transcribe()` or the translation ladder.

## Task Index

| Source paths / task trigger | Responsibility | Read next |
| --- | --- | --- |
| `ffmpegsrt.py`, `ffmpegsrt/cli.py`; flags, stage order, progress, exit codes | CLI and pipeline order | [CLI](ARCHITECTURE/modules/cli.md) |
| `ffmpegsrt/media.py`, `test/make_sample.sh`; ffmpeg, trim, encoding, burn-in | ffmpeg and ffprobe wrappers | [Media](ARCHITECTURE/modules/media.md) |
| `ffmpegsrt/transcribe.py`; Whisper options, CUDA runtime, compute types | Local speech recognition | [Transcribe](ARCHITECTURE/modules/transcribe.md) |
| `ffmpegsrt/translate.py`, `_vendor.py`, `harness/`, `vendor/kerness`; batching, ladder, agents, glossary | Kerness harness and gameplan | [Translate](ARCHITECTURE/modules/translate.md) |
| `ffmpegsrt/srt.py`, `ffmpegsrt/sound.py`; `Cue`, SRT I/O, timeline rebase, sound tags | Cue type, SRT and sound events | [Subtitles](ARCHITECTURE/modules/subtitles.md) |
| `ffmpegsrt/config.py`, `langs.py`, `errors.py`, `.env.example`; credentials, aliases, errors | Configuration and shared errors | [Config](ARCHITECTURE/modules/config.md) |
| `test/test_units.py`; add or change a unit test | Extend the class owning the subject | [Verification](#verification) |
| `test/run_test.sh`, `test/make_sample.sh`; verify against real media | Real-media test pair | [End-to-end test](ARCHITECTURE/topics/end-to-end-test.md) |
