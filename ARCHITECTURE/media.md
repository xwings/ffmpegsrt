# Media: ffmpeg and ffprobe

## Goal

Every shell-out to ffmpeg and ffprobe lives here: inspecting the input,
cutting a working clip, extracting audio for the recogniser, and burning
subtitles into the picture. Serves `M1` (audio for `M1`'s transcript) and `M3`
(burn-in), and owns the slicing that keeps every stage on one timeline.

## Status

`done` — all four operations work, each validating that it actually produced
something rather than trusting ffmpeg's exit code alone.

## Code Structure

| File | Role |
| ---- | ---- |
| `ffmpegsrt/media.py` | Probing, trimming, audio extraction, burn-in, and filtergraph escaping |

## Key Types and Entry Points

- `ffmpegsrt/media.py:43` — `require_tools()` — called first thing in `run()`,
  so a missing ffmpeg fails before anything expensive starts.
- `ffmpegsrt/media.py:53` — `_run(cmd, what)` — raises `MediaError` carrying the
  last 12 lines of stderr. ffmpeg's diagnostics are at the end, not the start.
- `ffmpegsrt/media.py:62` — `probe(path)` — returns `MediaInfo`: duration,
  whether there is video, and the **first** audio stream.
- `ffmpegsrt/media.py:104` — `extract_audio(src, dest, ...)` — mono 16 kHz PCM,
  the format Whisper resamples to anyway. `-ss` goes **before** `-i` so ffmpeg
  seeks by keyframe instead of decoding from the top; on a multi-gigabyte film
  that is seconds versus minutes.
- `ffmpegsrt/media.py:143` — `trim(src, dest, ...)` — cuts the working clip. It
  **re-encodes deliberately**: `-c copy` can only cut on a keyframe, which
  drifts the cut by up to a GOP and desyncs the very timings this exists to
  keep straight.
- `ffmpegsrt/media.py:186` — `escape_filter_path(path)` — the filtergraph parser
  eats `\` and `:`, and `'` terminates the quoted argument. Windows paths like
  `C:\clips` break loudly without this; a path with a comma breaks silently.
- `ffmpegsrt/media.py:201` — `build_force_style(font, size, margin)` — white
  fill, dark outline, light shadow, so the text stays readable over both bright
  and dark footage.
- `ffmpegsrt/media.py:226` — `burn_in(video, subtitles, dest, ...)` — re-encodes
  video (burning in means rewriting pixels) but stream-copies audio.
- `ffmpegsrt/media.py:15` — `DEFAULT_FONT = "Droid Sans Fallback"` — covers CJK,
  which libass's usual DejaVu default does not; Chinese would render as tofu.

## Interactions

- Called by [cli.md](cli.md) for all four operations.
- Its extracted audio feeds [transcribe.md](transcribe.md).
- Its burn-in consumes the file written by [subtitles.md](subtitles.md).
- Raises `MediaError`, a subclass of the base in [config.md](config.md).

## How to Test

```sh
python3 test/test_units.py    # pass = exit 0, "OK"
```

- `TestMediaHelpers` covers the two pure functions offline — pass = `ok`.
- Tool detection: `python3 -c "from ffmpegsrt import media; media.require_tools()"`
  — pass = exit 0 and no output when ffmpeg is installed.
- Probe and burn-in against a synthetic clip:

  ```sh
  ffmpeg -y -v error -f lavfi -i testsrc=d=5:s=320x240 -f lavfi -i sine=d=5 \
      -shortest /tmp/s.mp4
  printf '1\n00:00:01,000 --> 00:00:03,000\nhello\n' > /tmp/in.srt
  python3 ffmpegsrt.py -i /tmp/s.mp4 --srt-in /tmp/in.srt -b -o /tmp/burn.mp4
  ffprobe -v error -show_entries stream=codec_type -of default=nw=1:nk=1 \
      /tmp/burn.mp4 | sort -u | tr '\n' ' '
  ```

  pass = `audio video` — the output kept both streams.

## Open Gaps / Roadmap

- `AudioStream.language` (`ffmpegsrt/media.py:30`) is populated from stream tags
  and never read. Dead field, left in place.
- `probe` always takes the **first** audio stream. A film with a commentary
  track first, or a dub before the original, transcribes the wrong one; there is
  no flag to choose.
- Burn-in hardcodes libx264 and yuv420p. No HEVC, no hardware encoder, no way to
  pass through arbitrary ffmpeg flags.
- `subprocess.run` has no timeout, so a wedged ffmpeg hangs the run.
