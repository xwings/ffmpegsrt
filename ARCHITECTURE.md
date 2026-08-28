# ffmpegsrt

## Mission

Turn a movie in one language into a watchable subtitle track in another.

Speech recognition runs locally, so a transcript costs nothing but time and
never leaves the machine. Translation is the part a single model does badly —
subtitles need spoken register, consistent character names across a two-hour
film, and exactly one line per timed cue — so it runs through a
[kerness](https://github.com/xwings/kerness) multi-agent harness where a
translator drafts, a reviewer attacks the draft, and an editor ships the
result. ffmpeg does the demuxing and the burn-in.

The design constraint that shapes everything downstream: **cues are already
timed against the picture**. Merging, splitting, reordering or dropping one
does not make a slightly worse file, it desyncs every line that follows.

## Target environment

| | |
| --- | --- |
| Runtime | Python 3.10+ (the code uses PEP 604 `X \| Y` unions) |
| Platform | Linux, macOS, WSL2. Windows paths are handled in the filtergraph escaper but untested |
| Hard external deps | `ffmpeg` and `ffprobe` on `PATH` |
| Python deps | `faster-whisper` (CTranslate2 — no torch); `kerness`, built from the submodule |
| Optional | `nvidia-cublas-cu12` + `nvidia-cudnn-cu12` for `--device cuda` |
| Network | Only for translation. Transcription alone makes no network calls |
| Credentials | Any OpenAI-compatible `chat/completions` endpoint, via `.env` or env vars. Never hardcoded |

Speech recognition is CPU-bound and slow: `small` on CPU runs at roughly real
time, so a feature film is an hours-long job. Translation is network-bound and
unreliable — a flaky endpoint, not the model, is the usual reason a run appears
to hang. Both halves are built to survive that: the transcript is checkpointed
to disk before translation starts, and a batch that cannot be translated keeps
its source text rather than aborting the film.

## Workspace layout

```
ffmpegsrt.py              entry point; runnable from a checkout, no install
ffmpegsrt/                the package
  cli.py                  argument parsing and pipeline orchestration
  media.py                ffmpeg/ffprobe: probe, trim, extract, burn in
  transcribe.py           faster-whisper, plus the CUDA runtime shim
  translate.py            the kerness harness and its escalation ladder
  srt.py                  the Cue type and SRT read/write
  sound.py                non-speech sound events -> [tag] cues
  langs.py                language alias table
  config.py               credential resolution
  errors.py               the shared error base
  _vendor.py              checks kerness is built, and says how if it is not
harness/
  subtitle_translate.md   the gameplan: agents, phases, result shape
test/                     unit tests and the end-to-end pair
vendor/kerness/           git submodule (Rust; build its bindings/python)
ARCHITECTURE/             per-subsystem docs (see the Index)
```

## Boot and pipeline flow

`ffmpegsrt.py` puts the checkout on `sys.path` and calls
`cli.main`, which is the only place an anticipated failure becomes a one-line
message instead of a traceback.

```
main (cli.py:356)
 └─ run (cli.py:162)
     ├─ _validate ............ reject combinations that produce nothing
     ├─ media.require_tools .. fail now if ffmpeg is missing
     ├─ langs.resolve ........ -l/-t to (whisper code, prompt-ready name)
     ├─ resolve_llm_config ... credentials FIRST — a typo must not cost 40 min
     ├─ media.probe .......... duration, audio stream; no audio is fatal
     ├─ media.trim ........... --start/--duration, once, up front
     ├─ _get_cues
     │    ├─ srt.read_srt + shift_and_clip   (--srt-in)
     │    └─ media.extract_audio -> transcribe.transcribe
     │       └─ sound.classify  (--sound-tags)
     ├─ srt.write_srt (source) ... checkpoint before the unreliable half
     ├─ _translate ........... SubtitleTranslator over batches of 40
     ├─ srt.write_srt ........ source | translated | bilingual
     └─ media.burn_in ........ ffmpeg + libass
```

Two ordering decisions carry weight. Credentials resolve before transcription,
so a missing key fails in the first second. The transcript is written before
translation, so an interrupted run resumes with `--srt-in` instead of
re-transcribing.

## Roadmap

| | Milestone | Status |
| --- | --- | --- |
| **M1** | Transcribe a movie to a timed SRT | `done` |
| **M2** | Translate through the kerness harness, with glossary carry-over | `done` |
| **M3** | Burn subtitles into the video | `done` |
| **M4** | Non-speech sound events as `[tag]` cues | `done` |
| **M5** | Test coverage | `in progress` — unit tests pass; the end-to-end pair needs user-supplied media |

## Coding Discipline

Behavioral guidelines to reduce common LLM coding mistakes. Merge with
project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For
trivial tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If
yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make
it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs,
fewer rewrites due to overcomplication, and clarifying questions come
before implementation rather than after mistakes.

### Project-Specific Deviations

- **Never invent a sound.** `sound.py` and the gameplan only ever tag what the
  recogniser already labelled. Guessing at noise costs speech that was really
  there — see [ARCHITECTURE/subtitles.md](ARCHITECTURE/subtitles.md).
- **Never widen a cue contract silently.** Anything that changes how many
  cues exist, or their order, belongs in
  [ARCHITECTURE/translate.md](ARCHITECTURE/translate.md) before it is written.
- **Never commit a credential.** `.env` and `.env.local` are gitignored;
  `LLMConfig.__repr__` redacts the key so it cannot reach a traceback.

## Index

- [cli.md](ARCHITECTURE/cli.md) — argument parsing and pipeline orchestration
- [media.md](ARCHITECTURE/media.md) — ffmpeg and ffprobe wrappers
- [transcribe.md](ARCHITECTURE/transcribe.md) — speech recognition and the CUDA shim
- [translate.md](ARCHITECTURE/translate.md) — the kerness harness and its gameplan
- [subtitles.md](ARCHITECTURE/subtitles.md) — the Cue type, SRT I/O and sound events
- [config.md](ARCHITECTURE/config.md) — credentials, languages and the error base
- [testing.md](ARCHITECTURE/testing.md) — unit and end-to-end tests
