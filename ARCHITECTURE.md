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
main (ffmpegsrt/cli.py:350)
 └─ run (ffmpegsrt/cli.py:158)
     ├─ _validate ............ reject combinations that produce nothing
     ├─ media.require_tools .. fail now if ffmpeg is missing
     ├─ langs.resolve ........ -l/-t to (whisper code, prompt-ready name)
     ├─ resolve_llm_config ... credentials FIRST — a typo must not cost 40 min
     ├─ media.probe .......... duration, audio stream; no audio is fatal
     ├─ media.trim ........... --start/--duration, once, up front
     ├─ _get_cues
     │    ├─ srt.read_srt + shift_and_clip   (--srt-in)
     │    └─ media.extract_audio -> transcribe.transcribe
     │       └─ sound.classify  (automatic whole-cue sound labels)
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

## Development Loop

Coding Discipline governs writing; Review Checks govern review. This
loop connects them and defines when work is ready to release.

```text
Frame → Write → Prove → Review → Gate
          ▲          findings      │
          └────────────────────────┘
```

### The loop

**1. Frame.** Convert the request into a goal with an observable check.
Inspect the request, code, docs, and repository conventions; record the
narrowest supported assumptions. Ask one focused question only when a
required decision cannot be discovered or safely inferred and guessing
would materially change the result. Once framed, continue without an
approval pause.

**2. Write.** Make the smallest change that reaches the goal. Add no
unrequested features or abstractions, match local style, touch only
in-scope code, and remove only orphans created by the change.

**3. Prove.** Run relevant tests and retain observable evidence.

*Survey the suite before touching it.* Before adding, changing, merging,
or deleting any test, inventory the whole suite: enumerate every test
file and case name, then read in full each test whose subject, fixtures,
or assertions touch this change. Use a subagent for broad inventory when
supported. From that inventory decide the complete set of test edits at
once — what to change, what to add, what to merge, what to remove — each
backed by `file:line`, then execute only that plan. Never write a test
before the survey, and never discover existing coverage afterward.

The plan obeys four rules:

- **Reuse or extend first.** Add a case to the test that already owns
  the behavior or shares its setup, fixtures, and subject. A new test
  function or file is justified only when the survey found no existing
  test owning the behavior, or when merging would hide which case
  failed.
- **Add only what the goal needs.** A bug fix needs a reproducing
  regression test; a new capability needs a test of its claimed
  behavior. Nothing further.
- **Retire what this change made obsolete.** Delete tests whose behavior
  no longer exists, and merge tests this change turned into duplicates,
  citing the surviving test. Leave unrelated pre-existing tests alone;
  record suspected redundancy under **Open Gaps / Roadmap**.
- **Never delete to reach green.** A failing test is a finding for
  Write. Removal requires evidence that its behavior is gone or is still
  covered elsewhere, cited by `file:line`.

Coverage of claimed behavior must not decrease. A failure returns
directly to Write, never forward to Review.

**4. Review.** Walk all seven Review Checks as separate passes. Read
whole affected files, not only the diff. Every finding needs `file:line`
evidence. Use an independent agent or isolated pass for Fit,
Dependencies, and Security when available.

**5. Gate.** Apply the Definition of Done. Any unticked criterion,
`blocker`, or unresolved `major` returns its evidence to Write. All
criteria passing means the change is ready for public or production
release. There is no separate approval or reporting phase.

### Definition of Done

**Correctness**

- The framed goal and its named check pass.
- Tests cover claimed behavior and pass; a bug fix has a regression test.
- The suite was surveyed before any test was written, changed, or
  deleted; no added test duplicates coverage another test owns, and no
  removal left claimed behavior uncovered.
- The owning module's **How to Test** command passes with evidence.
- The project builds and tests from a fresh clone without local-only
  dependencies.

**Review**

- All seven Review Checks ran; none was skipped or assumed.
- No `blocker` or unresolved `major` remains.
- Nits were applied or consciously declined.

**Legibility and contract**

- A new maintainer can build, test, run, and understand public behavior
  from the docs.
- Every changed line serves the goal; no drive-by formatting, debugging
  remnants, commented-out code, secrets, tokens, or local paths remain.
- Public names, signatures, errors, and recovery are intelligible.
- Architecture docs and `file:line` references are current.
- Breaking changes, deprecations, dependencies, licenses, and attribution
  are handled; commit or PR text explains why.

### Iterating without thrashing

- Every pass closes a named finding and touches only what it names.
- Nits alone do not trigger another pass.
- Re-run Prove after every fix.
- Two no-change passes force Gate re-evaluation: release if Done passes;
  otherwise return the surviving evidence to Frame.
- Three passes on one finding return automatically to Frame for a new
  approach.
- Never widen scope to satisfy a finding. Record out-of-scope work under
  **Open Gaps / Roadmap**.

## Coding Discipline

### 1. Think Before Coding

- Understand the request, code, goal, and repository conventions first.
- Record assumptions and choose the narrowest evidence-backed reading.
- Prefer the simpler approach when it reaches the same verified goal.
- Ask only during planning and only for a required answer that cannot be
  discovered or safely inferred.

### 2. Simplicity First

- Implement only what was requested.
- Do not add single-use abstractions, speculative flexibility, or checks
  for impossible conditions.
- If the implementation is materially larger than the problem, simplify
  it.

### 3. Surgical Changes

- Do not refactor, reformat, or clean up unrelated code.
- Match the surrounding style.
- Remove imports, variables, and functions made unused by this change;
  leave pre-existing dead code alone unless requested.
- Every changed line must trace to the stated goal.

### 4. Goal-Driven Execution

Turn work into verifiable outcomes, then loop until they pass:

- Add validation → invalid inputs are rejected by a named passing test.
- Fix a bug → a regression test fails before the fix and passes after.
- Refactor → behavior tests pass before and after.

Give every plan step its own check. Strengthen vague criteria from
repository evidence before implementation.

### Project-Specific Deviations

- **Never invent a sound.** `sound.py` and the gameplan only ever tag what the
  recogniser already labelled. Guessing at noise costs speech that was really
  there — see [ARCHITECTURE/subtitles.md](ARCHITECTURE/subtitles.md).
- **Never widen a cue contract silently.** Anything that changes how many
  cues exist, or their order, belongs in
  [ARCHITECTURE/translate.md](ARCHITECTURE/translate.md) before it is written.
- **Never commit a credential.** `.env` and `.env.local` are gitignored;
  `LLMConfig.__repr__` redacts the key so it cannot reach a traceback.

## Review Checks

Run every check against every change before merge. Keep checks separate.

Four rules bind all checks:

- **Evidence or no finding.** Every finding cites `file:line`.
- **The repository is authoritative.** Demand only conventions visible
  in the tree.
- **Read files, not only hunks.** Context can invalidate a finding or
  reveal unreachable code, unused parameters, and hidden duplication.
- **Review the change, never the author.** Describe code and impact, not
  how or by whom it was produced.

### 1. Style

Check indentation and local file conventions. Mixed indentation is
`major`; a consistent new file using the wrong local indent is `nit`.
Leave machine-checkable formatting to existing formatters and linters;
never demand unrelated reformatting.

### 2. Naming

Compare new names with nearby precedents before filing a finding. If the
repository is inconsistent, demand nothing. A local mismatch is `nit`;
an inconsistent public name is `major`.

### 3. Duplication

Search distinctive constants, errors, fields, and call sequences—not
only symbol names—for code performing the same job. Cite both sites and
the remedy. Cross-layer duplication is `major`; small local repetition
is `nit`. Similar code with meaningfully different branches is not
duplication.

### 4. Quality

Require followable control flow, errors handled where they occur, and
abstractions proportional to the problem. Swallowed errors,
inappropriate prints, unexplained magic values, and dead branches are
`major`. Remove unrequested configurability, one-caller wrappers, filler
comments, debugging remnants, and unrelated formatting. Missing tests
belong to Prove, not this check.

### 5. Fit

Read `ARCHITECTURE.md` and the owning module doc before the diff. Check
scope, layering, ownership, public-API growth, and performance claims. A
layering violation or unjustified public API is `major`. Architectural or
public-behavior changes must update the relevant docs in the same change.

### 6. Dependencies

Check manifests and imports, maintenance, supply-chain risk, advisories,
install-time behavior, license, transitive cost, and whether the standard
library is sufficient. An unjustified top-level dependency is `major`;
a live advisory or abandoned upstream is `blocker`. Incomplete evidence
does not pass.

### 7. Security

Check both defects and widened exposure: unsafe memory access, unchecked
sizes or offsets, integer overflow, path traversal, unsafe
deserialization, command construction, committed secrets, and unbounded
untrusted input. Trace input to impact; without a reachable path there is
no finding. A real defect is `major`; a trust-boundary break is `blocker`.
Describe the fix without publishing exploit steps.

### Severity and the merge threshold

| Severity | Effect |
| -------- | ------ |
| `blocker` | Must not merge. |
| `major` | Must be resolved before merge. |
| `nit` | Apply or consciously decline. |
| `info` | Context or a question; no action implied. |

Merge only with no `blocker` and no unresolved `major`. A check that did
not run does not pass. Findings feed Write and Gate directly; they do not
create a reporting phase.

## Index

- [cli.md](ARCHITECTURE/cli.md) — argument parsing and pipeline orchestration
- [media.md](ARCHITECTURE/media.md) — ffmpeg and ffprobe wrappers
- [transcribe.md](ARCHITECTURE/transcribe.md) — speech recognition and the CUDA shim
- [translate.md](ARCHITECTURE/translate.md) — the kerness harness and its gameplan
- [subtitles.md](ARCHITECTURE/subtitles.md) — the Cue type, SRT I/O and sound events
- [config.md](ARCHITECTURE/config.md) — credentials, languages and the error base
- [testing.md](ARCHITECTURE/testing.md) — unit and end-to-end tests
