# Subtitles: cues, SRT I/O and sound events

## Goal

Own the `Cue` — the unit every other subsystem passes around — and everything
that turns cues into an SRT file and back. Also owns sound events: recognising
the non-speech markers a recogniser emits and rendering them as `[tag]` cues.
Serves `M1` (transcript to SRT), `M3` (the file libass burns in) and `M4`
(sound events).

## Status

`done` — `M4` included. Whole-cue markers are detected automatically, tagged,
translated as labels and rendered bracketed in all three output modes, and
they survive an SRT round trip so `--srt-in` restores them.

## Code Structure

| File | Role |
| ---- | ---- |
| `ffmpegsrt/srt.py` | The `Cue` dataclass, timestamp conversion, `write_srt` / `read_srt`, timeline rebasing |
| `ffmpegsrt/sound.py` | Detects non-speech markers and marks the cues that are sounds |

## Key Types and Entry Points

- `ffmpegsrt/srt.py:16` — `Cue(start, end, text, translated=None, sound=False)`
  — `text` is always the source line. `translated` stays `None` when `-t` was
  not requested, which is how `write_srt` tells the modes apart. When `sound`
  is set, both bodies hold a *bare* label (`cry`, not `[cry]`).
- `ffmpegsrt/srt.py:39` — `Cue.display(mode)` — renders `source`, `translated`
  or `bilingual`. Falls back to the source line when there is no translation.
- `ffmpegsrt/srt.py:35` — `Cue._wrap(body)` — the single place brackets are
  added. Keeping it here is why every mode marks a sound identically.
- `ffmpegsrt/srt.py:73` — `shift_and_clip(cues, start, duration)` — moves cues
  from an existing SRT onto a trimmed clip's zero-based timeline and drops what
  falls outside. Straddling cues are clamped, not dropped.
- `ffmpegsrt/srt.py:120` — `write_srt(cues, path, mode)` — skips cues with an
  empty body and renumbers over the hole, so the index never skips. Writes
  BOM-less UTF-8, which is what libass assumes.
- `ffmpegsrt/srt.py:155` — `read_srt(path)` — tolerant parser: the leading index
  is optional, `,` and `.` both work as the millisecond separator, a BOM is
  stripped, multi-line bodies stay intact.
- `ffmpegsrt/sound.py:42` — `detect(text)` — returns the label when the **whole**
  cue is a marker: `[Music]`, `(laughs)`, `（笑）`, `【音楽】`, `*sobbing*`,
  `♪ ... ♪`. A bare note run normalises to `music`.
- `ffmpegsrt/sound.py:75` — `classify(cues)` — marks matching cues in place,
  strips the wrapper into a bare label, returns the count.
- `ffmpegsrt/sound.py:65` — `strip_brackets(text)` — unwraps a label the
  translator handed back still bracketed.

## Interactions

- `Cue` is produced by [transcribe.md](transcribe.md) and by `read_srt`.
- Filled in by [translate.md](translate.md), which renders sound cues bracketed
  in the numbered batch and unwraps the reply through `strip_brackets`.
- Driven by [cli.md](cli.md), which calls `classify` and `shift_and_clip`.
- The written file is consumed by [media.md](media.md) for burn-in.
- Covered by [testing.md](testing.md).

## How to Test

```sh
python3 test/test_units.py    # pass = exit 0, output ends in "OK"
```

- `TestSrtIO`, `TestCueDisplay`, `TestShiftAndClip`, `TestSoundDetection` and
  `TestTimestamps` cover this module — pass = all report `ok`.
- Detection directly:
  `python3 -c "from ffmpegsrt import sound; print(sound.detect('(crying)'))"`
  — pass = `crying`.
- A dialogue line that merely mentions a sound must stay dialogue:
  `python3 -c "from ffmpegsrt import sound; print(sound.detect('[Music] I cannot wait'))"`
  — pass = `None`.

## Open Gaps / Roadmap

- **Mixed cues are left alone.** `[Music] I can't wait` stays a single dialogue
  line; splitting it into a tag plus a line would need new cue timings, which
  nothing here is entitled to invent.
- `♪ la la ♪` tags as `[la la]` rather than `[music]` — the label is what was
  heard, but a sung lyric arguably wants different treatment.
- No SDH conventions beyond the tag itself: no speaker labels, no positioning.
- `read_srt` does not restore `sound` on its own; the automatic ingestion path
  calls `classify` to re-detect it. That keeps `srt.py` free of a dependency on
  `sound.py`.
