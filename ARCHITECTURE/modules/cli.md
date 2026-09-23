---
eatmycode_version: "2.1.0"
---
# CLI

Owner: [Project architecture](../../ARCHITECTURE.md)

Read when: touching `ffmpegsrt.py` or `ffmpegsrt/cli.py`; adding a flag; changing stage order, progress output, exit codes, or how `--srt-in` interacts with `--start/--duration`.

## Responsibility and Status

Parses the command line and runs the pipeline in a fixed order: validate, check tools, resolve languages and credentials, probe, trim, load or transcribe cues, classify sounds, checkpoint, translate, write, burn in. It is the only module that knows the stage order and the only place an anticipated failure becomes a one-line message instead of a traceback.

Status: `done`. Evidence: the 49-test unit suite passes, `--help` and `--version` exit 0, and the synthetic no-network run below yields the expected SRT. Non-goals: no resume inside translation, no audio-stream selection, no config file beyond `.env`.

## Code Map

| Path / symbol | Role |
| --- | --- |
| `ffmpegsrt.py:13` | Entry point. Inserts the checkout on `sys.path`, then imports `cli.main`; the import must follow the insert. |
| `ffmpegsrt/cli.py:37` `build_parser` | Every flag, grouped: subtitle, speech recognition, translation endpoint, encoding, misc. Defaults come from `media.DEFAULT_FONT` and the `translate` constants. |
| `ffmpegsrt/cli.py:145` `_validate` | Rejects `-i` without `-s` or `-b`, and `--bilingual` without `-t`, via `parser.error` (exit 2). |
| `ffmpegsrt/cli.py:158` `run` | The pipeline. Temp dir `ffmpegsrt-*` is removed in `finally` unless `--keep-temp`. |
| `ffmpegsrt/cli.py:241` `_get_cues` | `--srt-in` path (read, rebase onto the clip, classify) or extract-and-transcribe with a once-a-second progress callback. |
| `ffmpegsrt/cli.py:307` `_translate` | Builds `SubtitleTranslator`; reports sessions, retries, fallbacks, failed requests and kept-source ranges. |
| `ffmpegsrt/cli.py:350` `main` | `FfmpegSrtError` and `ValueError` exit 1; `KeyboardInterrupt` exits 130. |
| `test/test_units.py:205` `TestParser`, `:217` `TestGetCues` | Parser shape; automatic sound classification on both cue paths. |

## Local Conventions

Root baseline suffices. Progress and notes go through `_log` to stderr (`ffmpegsrt/cli.py:140`) so stdout stays pipeable; lines use a fixed-width `label    :` prefix. A flag that mirrors a module constant takes its default from that constant, not a literal.

## Contracts and Invariants

- **Inputs.** argv only. `-i` required; at least one of `-s`, `-b`.
- **Outputs.** SRT at `-s` in mode `source`, `translated` or `bilingual` (`ffmpegsrt/cli.py:210`); video at `-o` or `<stem>_out<ext>` (`:135`). Exit 0 success, 1 anticipated error, 2 usage, 130 interrupt.
- **Load-bearing order** (`ffmpegsrt/cli.py:166`, `:200`): credentials resolve before probe and transcription so a typo fails in the first second; with both `-t` and `-s`, the source transcript is written before translation and overwritten after.
- **One timeline.** `--start/--duration` trims once, up front; later stages see a clip starting at zero. Cues from `--srt-in` are rebased with `shift_and_clip`; an empty result is fatal (`:257`).
- **Sound classification is automatic** on both paths (`:266`, `:301`). There is no flag; `TestParser` asserts `--sound-tags` stays removed.
- **No audio stream is fatal** (`:176`). `-l` is ignored with `--srt-in`, with a note.
- **Recovery.** Interrupted translation: rerun with `--srt-in <the -s file>`.

## Dependencies and Boundaries

Outgoing only: [Media](media.md) (probe, trim, extract, burn in), [Transcribe](transcribe.md), [Translate](translate.md), [Subtitles](subtitles.md) (`read_srt`, `shift_and_clip`, `classify`, `write_srt`), [Config](config.md) (`resolve_llm_config`, `langs.resolve`). Nothing imports `cli` except the tests. Driven end to end by `test/run_test.sh`: [End-to-end test](../topics/end-to-end-test.md).

## Change Guide

| Change trigger | Inspect / extend | Required docs / checks |
| --- | --- | --- |
| New flag | `build_parser` group; thread through `run`, `_get_cues` or `_translate`; default from the owning module | Owning module page; `TestParser`; `--help` |
| New stage or reorder | `run`; keep credentials-first and checkpoint-before-translate | Root System Design; synthetic run |
| Exit codes or error text | `main`, `_validate` | Root Verification table |
| `--srt-in` rebasing | `_get_cues`, `srt.shift_and_clip` | [Subtitles](subtitles.md); `TestGetCues`, `TestShiftAndClip` |
| Progress format | `_log` callers, the `progress` closure | Unit suite |

## Verification

From the repo root, no network, no model:

```sh
python3 test/test_units.py                                   # 49 tests, OK
python3 ffmpegsrt.py --help && python3 ffmpegsrt.py --version   # exit 0, "ffmpegsrt 0.1.0"
python3 ffmpegsrt.py -i x.mp4                                # exit 2, "nothing to produce"
python3 ffmpegsrt.py -i x.mp4 -s o.srt --bilingual           # exit 2, "needs a translation target"
```

Synthetic clip through trim, `--srt-in` rebase and sound tagging (needs ffmpeg):

```sh
ffmpeg -y -v error -f lavfi -i testsrc=d=12:s=320x240 -f lavfi -i sine=d=12 -shortest /tmp/s.mp4
printf '1\n00:00:04,000 --> 00:00:06,000\nhello\n\n2\n00:00:07,000 --> 00:00:08,500\n(crying)\n' > /tmp/in.srt
python3 ffmpegsrt.py -i /tmp/s.mp4 --srt-in /tmp/in.srt --start 4 --duration 5 -s /tmp/out.srt
```

Pass: exit 0; `/tmp/out.srt` holds cues at `00:00:00,000` and `00:00:03,000`, the second reading `[crying]`. Real recognition and translation are reachable only through the end-to-end pair.

## Known Gaps

- No resume within translation: a restart with `--srt-in` re-translates every batch.
- `--srt-in` ignores `-l`; the prompt cannot be told the language of an existing file.
- Progress is a callback per stage, not one reporter.
- `media.extract_audio` accepts `start`/`duration`, but `run` always trims first and extracts from the clip, so the CLI never passes them.
