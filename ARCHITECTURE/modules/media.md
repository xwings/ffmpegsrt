---
eatmycode_version: "2.1.0"
---
# Media

Owner: [Project architecture](../../ARCHITECTURE.md)

Read when: touching `ffmpegsrt/media.py` or `test/make_sample.sh`; changing ffmpeg/ffprobe invocations, trimming, audio extraction, encoder settings, or burned-in subtitle style.

## Responsibility and Status

Every shell-out to ffmpeg and ffprobe: tool detection, probing, cutting the working clip, extracting recogniser audio, and burning subtitles in with libass. Each operation checks that it produced a non-empty file rather than trusting the exit code alone.

Status: `done`. Evidence: `TestMediaHelpers` (4 tests) pass and the synthetic burn-in below yields an `hevc` video plus `aac` audio stream. Non-goals: no hardware encoders, no arbitrary ffmpeg passthrough, no audio-stream selection.

## Code Map

| Path / symbol | Role |
| --- | --- |
| `ffmpegsrt/media.py:15` `DEFAULT_FONT` | `Droid Sans Fallback`: covers CJK, which libass's DejaVu default does not. |
| `ffmpegsrt/media.py:43` `require_tools` | First call in `cli.run`; a missing tool raises `MediaError` with an install hint. |
| `ffmpegsrt/media.py:53` `_run` | Runs an argv list; on failure raises `MediaError` with the last 12 stderr lines. |
| `ffmpegsrt/media.py:62` `probe` | `ffprobe -of json`; returns `MediaInfo` with duration, `has_video` and the **first** audio stream. |
| `ffmpegsrt/media.py:104` `extract_audio` | Mono 16 kHz `pcm_s16le`; `-ss` precedes `-i` for keyframe seeking. |
| `ffmpegsrt/media.py:143` `trim` | Re-encodes the clip (libx265, `veryfast`, crf 18) so the cut lands exactly on `start`. |
| `ffmpegsrt/media.py:190` `escape_filter_path`, `:205` `build_force_style`, `:230` `burn_in` | Filtergraph escaping, the ASS style string, the burn-in encode. |
| `test/test_units.py:401` `TestMediaHelpers` | Escaping, style, and that trim and burn-in emit `libx265`, `yuv420p10le`, `hvc1`. |

## Local Conventions

Root baseline suffices. Commands are argv lists starting `ffmpeg -y -v error`, never a shell string. Every non-obvious ffmpeg choice carries a docstring saying why (`ffmpegsrt/media.py:111`, `:152`, `:241`).

## Contracts and Invariants

- **Trim re-encodes on purpose.** `-c copy` can only cut on a keyframe and would drift the cut by up to a GOP, desyncing every cue (`ffmpegsrt/media.py:158`). Trim and burn-in both write 10-bit HEVC (`yuv420p10le`, `-tag:v hvc1`) so a re-encode of a re-encode does not compound 8-bit error and the mp4 plays in QuickTime; `test_video_is_encoded_as_10_bit_hevc` pins this.
- **Burn-in re-encodes video, stream-copies audio**, adds `+faststart`. The SRT path is resolved and escaped before entering the filtergraph: `\`, `:` and `'` are neutralised (`:198`). A comma in the path is not escaped and fails silently.
- **Extraction** uses `-map 0:a:0?`, which tolerates a missing stream, so the empty-file check afterwards is the real failure detector.
- **Probe** takes the first `codec_type == audio` stream; `AudioStream.language` is filled from tags and never read.
- **Style** (`build_force_style`): white fill, dark outline 1.6, shadow 0.6, bottom centre, `MarginV` 28; font and size come from `--font` and `--font-size`.
- **Failures** raise `MediaError(FfmpegSrtError)`. `subprocess.run` has no timeout, so a wedged ffmpeg hangs the run.

## Dependencies and Boundaries

Incoming: [CLI](cli.md) only. Outgoing: `ffmpegsrt/errors.py` and the `ffmpeg`/`ffprobe` binaries. Its audio feeds [Transcribe](transcribe.md); its burn-in consumes the file [Subtitles](subtitles.md) wrote. `test/make_sample.sh:38` repeats the trim encoder flags in bash; change both together.

## Change Guide

| Change trigger | Inspect / extend | Required docs / checks |
| --- | --- | --- |
| Encoder, pixel format or tag | `trim`, `burn_in`, `test/make_sample.sh:40` | `test_video_is_encoded_as_10_bit_hevc`; the log text at `ffmpegsrt/cli.py:223` |
| Subtitle appearance | `build_force_style`, `DEFAULT_FONT` | `test_force_style_carries_font_and_size`; synthetic burn-in |
| Audio stream choice | `probe`, the `-map` in `extract_audio` | The flag belongs in [CLI](cli.md) |
| New ffmpeg operation | Follow `_run` plus the non-empty output check | A `TestMediaHelpers` case with `_run` stubbed |

## Verification

```sh
python3 test/test_units.py                                          # TestMediaHelpers ok
python3 -c "from ffmpegsrt import media; media.require_tools()"     # exit 0, silent
ffmpeg -y -v error -f lavfi -i testsrc=d=5:s=320x240 -f lavfi -i sine=d=5 -shortest /tmp/s.mp4
printf '1\n00:00:01,000 --> 00:00:03,000\nhello\n' > /tmp/in.srt
python3 ffmpegsrt.py -i /tmp/s.mp4 --srt-in /tmp/in.srt -b -o /tmp/burn.mp4 --preset ultrafast
ffprobe -v error -show_entries stream=codec_name -of default=nw=1:nk=1 /tmp/burn.mp4
```

Pass: exit 0 throughout; ffprobe prints `hevc` and `aac`. Needs ffmpeg built with libx265 and libass. Nothing exercises the real stderr-tail formatting in `_run`.

## Known Gaps

- The first audio stream always wins; a commentary or dub track ordered first transcribes the wrong audio.
- libx265 and 10-bit are hardcoded; no 8-bit or non-HEVC option for players that cannot decode it.
- No subprocess timeout.
- `AudioStream.language` is dead data.
