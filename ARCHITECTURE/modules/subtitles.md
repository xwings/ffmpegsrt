---
eatmycode_version: "2.1.0"
---
# Subtitles

Owner: [Project architecture](../../ARCHITECTURE.md)

Read when: touching `ffmpegsrt/srt.py` or `ffmpegsrt/sound.py`; changing the `Cue` type, SRT parsing or writing, timeline rebasing, output modes, or sound-event detection.

## Responsibility and Status

Owns `Cue`, the unit every stage passes around, and everything that turns cues into SRT and back: timestamps, the three output modes, rebasing onto a trimmed clip, and recognising whole-cue non-speech markers so they render as `[tag]` cues in every mode.

Status: `done`. Evidence: `TestTimestamps`, `TestSrtIO`, `TestCueDisplay`, `TestShiftAndClip` and `TestSoundDetection` (25 tests) pass; the synthetic run in [CLI](cli.md) shows `(crying)` surviving a trim as `[crying]`.

## Code Map

| Path / symbol | Role |
| --- | --- |
| `ffmpegsrt/srt.py:16` `Cue` | `start`, `end`, `text` (always source), `translated=None`, `sound=False`. |
| `ffmpegsrt/srt.py:35` `Cue._wrap`, `:39` `Cue.display` | The single place brackets are added; `display(mode)` renders `source`, `translated` or `bilingual`, falling back to source. |
| `ffmpegsrt/srt.py:52,63` `format_timestamp`, `parse_timestamp` | `HH:MM:SS,mmm`; short millis are right-padded (`"1"` means .100). |
| `ffmpegsrt/srt.py:73` `shift_and_clip` | Rebase onto a clip timeline, drop outside cues, clamp straddlers. |
| `ffmpegsrt/srt.py:120` `write_srt`, `:155` `read_srt` | Writer skips empty bodies and renumbers; reader tolerates a missing index, `.` millis, a BOM and multi-line bodies. |
| `ffmpegsrt/sound.py:22,36` `_WRAPPED_RE`, `_NOTE_RE` | `[..]`, `(..)`, `（..）`, `【..】`, `*..*`, and `♪` runs. |
| `ffmpegsrt/sound.py:42` `detect`, `:65` `strip_brackets`, `:75` `classify` | Whole-cue detection, unwrapping a returned label, marking cues in place. |
| `test/test_units.py:25-203` | The five test classes named above. |

## Local Conventions

Root baseline suffices. `srt.py` imports nothing project-local and `sound.py` imports only `srt.Cue`; keep that direction so `read_srt` never depends on detection. Labels are stored bare (`cry`, not `[cry]`); brackets exist only in `display` output.

## Contracts and Invariants

- **Cue count and order are sacred.** `shift_and_clip` and `write_srt` are the only functions that drop cues, and only for cues outside the clip or with an empty body; `write_srt` renumbers so indices never skip (`ffmpegsrt/srt.py:139`).
- **Modes**: `source` ignores `translated`; `translated` falls back to source when `translated is None`; `bilingual` puts source above translation, or translation alone when `text` is empty.
- **Sound cues**: `sound=True` means both bodies are bare labels and every mode brackets them identically (`test_sound_cue_is_bracketed_in_every_mode`). Only whole-cue markers count; `[Music] I cannot wait` stays dialogue (`test_dialogue_is_not_a_sound`). A bare `♪♪` becomes `music`; `♪ la la ♪` becomes `la la`.
- **Never invent a sound** (`ffmpegsrt/sound.py:10`): `classify` marks only what the recogniser already wrapped.
- **Round trip**: `read_srt` does not set `sound`; the CLI re-runs `classify` on `--srt-in`, pinned by `test_tagged_cue_survives_an_srt_round_trip`.
- **Encoding**: BOM-less UTF-8 out (what libass assumes), `utf-8-sig` in.
- **Rebasing**: `finish <= 0` or `begin >= duration` drops the cue; straddlers clamp to `[0, duration]`.

## Dependencies and Boundaries

Incoming: [CLI](cli.md) (`read_srt`, `shift_and_clip`, `classify`, `write_srt`), [Transcribe](transcribe.md) (constructs `Cue`), [Translate](translate.md) (`Cue.display('source')` for the batch, `strip_brackets` on return); [Media](media.md) consumes the written file. Outgoing: standard library only.

## Change Guide

| Change trigger | Inspect / extend | Required docs / checks |
| --- | --- | --- |
| New marker syntax | `_WRAPPED_RE` or `_NOTE_RE` | A case in `test_wrapped_markers` and a negative one in `test_dialogue_is_not_a_sound` |
| New output mode | `Cue.display`, `write_srt`, the mode choice in `cli.run` | `TestCueDisplay`; [CLI](cli.md) |
| Splitting mixed cues or merging | Changes cue count: read [Translate](translate.md) first | Root System Design |
| Parser tolerance | `read_srt`, `_TIMING_RE` | `TestSrtIO` |

## Verification

```sh
python3 test/test_units.py                                                              # 25 of 49 tests are here
python3 -c "from ffmpegsrt import sound; print(sound.detect('(crying)'))"               # crying
python3 -c "from ffmpegsrt import sound; print(sound.detect('[Music] I cannot wait'))"  # None
```

Pass: exit 0 and the printed values. libass rendering of the written file is checked only by the burn-in run in [Media](media.md).

## Known Gaps

- Mixed cues (`[Music] I can't wait`) stay dialogue; splitting would need timings nothing here may invent.
- `♪ la la ♪` tags as `[la la]`, not `[music]`.
- No SDH conventions beyond the tag: no speaker labels, no positioning.
