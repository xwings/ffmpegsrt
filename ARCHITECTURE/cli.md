# CLI and pipeline orchestration

## Goal

Parse the command line and drive the whole pipeline: probe, trim, transcribe
or reuse an SRT, translate, write, burn in. This is the only module that knows
the order of the stages and the only one that turns an anticipated failure into
a message rather than a traceback. Infrastructure for `M1`–`M4`; it owns no
milestone of its own but every one of them ships through it.

## Status

`done` — every documented flag is wired and exercised. `--sound-tags` (`M4`)
and the `--srt-in` timeline rebase both land here.

## Code Structure

| File | Role |
| ---- | ---- |
| `ffmpegsrt.py` | Entry point. Puts the checkout on `sys.path` so `python3 ffmpegsrt.py` works uninstalled |
| `ffmpegsrt/cli.py` | Parser, validation, stage ordering, progress reporting |

## Key Types and Entry Points

- `ffmpegsrt.py:15` — imports `cli.main` after the `sys.path` insert, which is
  why the import is not at the top of the file.
- `ffmpegsrt/cli.py:37` — `build_parser()` — every flag, grouped into subtitle,
  speech-recognition, translation-endpoint, encoding and misc sections.
- `ffmpegsrt/cli.py:149` — `_validate(args, parser)` — rejects combinations that
  cannot produce output: no `-s` and no `-b`, or `--bilingual` without `-t`.
- `ffmpegsrt/cli.py:162` — `run(args, parser)` — the pipeline. Resolves
  credentials *before* transcription and checkpoints the transcript *before*
  translation; both orderings are load-bearing, not incidental.
- `ffmpegsrt/cli.py:245` — `_get_cues(...)` — either reads `--srt-in` (shifting
  it onto the clip's timeline when a slice was requested) or extracts audio and
  transcribes. Applies `sound.classify` on both paths when `--sound-tags` is on.
- `ffmpegsrt/cli.py:313` — `_translate(...)` — builds the `SubtitleTranslator`
  and reports what the endpoint did: failed requests, retries, fallbacks, and
  any range that kept its source text.
- `ffmpegsrt/cli.py:144` — `_log(message)` — progress goes to **stderr**, so
  stdout stays clean for piping.
- `ffmpegsrt/cli.py:356` — `main(argv)` — catches `FfmpegSrtError` and
  `ValueError`; returns `130` on Ctrl-C.

## Interactions

- Calls [media.md](media.md) to probe, trim, extract audio and burn in.
- Calls [transcribe.md](transcribe.md) for speech recognition, passing a
  once-a-second progress callback because faster-whisper yields lazily.
- Calls [translate.md](translate.md) for the harness run.
- Calls [subtitles.md](subtitles.md) to classify sound events, rebase cues onto
  a clip timeline, and write the SRT.
- Calls [config.md](config.md) for credentials and language resolution.
- Exercised by [testing.md](testing.md) end to end.

## How to Test

```sh
python3 ffmpegsrt.py --help          # pass = exit 0, usage printed
python3 ffmpegsrt.py --version       # pass = "ffmpegsrt 0.1.0"
```

- Nothing to produce is rejected:
  `python3 ffmpegsrt.py -i x.mp4` — pass = exit 2, message contains
  `nothing to produce`.
- `--bilingual` without `-t` is rejected — pass = exit 2, message contains
  `needs a translation target`.
- The full no-network path, using a synthetic clip and a hand-written SRT:

  ```sh
  ffmpeg -y -v error -f lavfi -i testsrc=d=12:s=320x240 \
      -f lavfi -i sine=d=12 -shortest /tmp/s.mp4
  printf '1\n00:00:04,000 --> 00:00:06,000\nhello\n\n2\n00:00:07,000 --> 00:00:08,500\n(crying)\n' > /tmp/in.srt
  python3 ffmpegsrt.py -i /tmp/s.mp4 --srt-in /tmp/in.srt --sound-tags \
      --start 4 --duration 5 -s /tmp/out.srt
  ```

  pass = exit 0; `/tmp/out.srt` holds two cues starting at `00:00:00,000` and
  `00:00:03,000`, the second reading `[crying]`.

## Open Gaps / Roadmap

- `--srt-in` silently ignores `-l`; it logs a note but there is no way to
  re-detect the language of an existing file.
- Progress reporting is a callback per stage rather than one shared reporter,
  so the `asr` and `translate` lines format themselves independently.
- No resume within translation. `--srt-in` restarts from the checkpointed
  transcript, but a run interrupted at batch 47 re-translates batches 1–46.
