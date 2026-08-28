# Translation via the kerness harness

## Goal

Translate batches of timed cues into the target language without ever changing
how many there are. Each batch runs through a full kerness session — a
translator drafts, a reviewer attacks the draft, an editor ships it — with a
glossary carried between batches so characters are not renamed halfway through
the film. Serves `M2`, and carries `M4`'s sound tags through translation.

## Status

`done` — the harness, the escalation ladder, glossary carry-over and sound-tag
handling all work. Not covered by automated tests: every path needs a live
endpoint.

## Code Structure

| File | Role |
| ---- | ---- |
| `ffmpegsrt/translate.py` | Batching, the session, the escalation ladder, result coercion |
| `harness/subtitle_translate.md` | The gameplan: agents, phases, result shape, and the editor's instructions |
| `ffmpegsrt/_vendor.py` | Checks the `vendor/kerness` submodule is built and installed |

## Key Types and Entry Points

- `ffmpegsrt/translate.py:222` — `SubtitleTranslator` — holds the provider, the
  gameplan path and the running `TranslationStats`.
- `ffmpegsrt/translate.py:262` — `.translate(cues, source, target, on_batch)` —
  splits into batches, carries `CONTEXT_CUES` (3) translated cues forward as
  context, and fills each `Cue.translated` in place.
- `ffmpegsrt/translate.py:328` — `_translate_batch(...)` — the **escalation
  ladder**, and the heart of this module: (1) run the harness; (2) if the result
  is unusable and the batch is still inside its budget, run it once more saying
  so; (3) fall back to one direct call with no agents; (4) give up on the batch
  but keep its source text, because aborting at batch 47 would throw away 46
  batches of paid-for work.
- `ffmpegsrt/translate.py:192` — `_coerce_lines(raw, expected)` — returns `None`
  on a length mismatch. **This is the guard the whole design rests on.**
  Kerness returns type defaults rather than raising on a malformed result
  block, so a short list would silently desync every following subtitle.
- `ffmpegsrt/translate.py:88` — `_timed_provider(...)` — wraps `CustomProvider`
  to (a) report each failed or empty request as it happens instead of after all
  retries are gone, and (b) enforce the batch deadline. A session cannot be
  interrupted from outside, but it can be starved: past the deadline every
  request fails instantly and the loop unwinds in seconds.
- `ffmpegsrt/translate.py:143` — `_numbered(cues)` — renders the batch, using
  `Cue.display('source')` so a sound cue arrives bracketed and the agents can
  tell a tag from a line.
- `ffmpegsrt/translate.py:56` — `TranslationStats` — sessions, retries,
  fallbacks, failed requests, the glossary, and the ranges that kept source text.
- `ffmpegsrt/translate.py:34,40` — `DEFAULT_TIMEOUT_SEC` (60s per request) and
  `BATCH_BUDGET_SEC` (300s per batch).
- `ffmpegsrt/_vendor.py:29` — `ensure_kerness()` — raises `KernessMissing`,
  distinguishing an unchecked-out submodule from a checked-out but unbuilt one.
  Kerness is a Rust extension, so unlike a pure-Python dependency it cannot be
  made importable by adding a directory to `sys.path`; there is no fallback,
  only the build instruction.

## Interactions

- Driven by [cli.md](cli.md), which reports the stats it returns.
- Reads and writes `Cue.translated` from [subtitles.md](subtitles.md), and uses
  its `strip_brackets` to unwrap a returned sound label.
- Takes an `LLMConfig` from [config.md](config.md).
- Delegates to the vendored kerness submodule: `Session`, `CustomProvider`,
  `ConsoleChannel`, `SessionResult.fields`.

## How to Test

```sh
python3 test/test_units.py    # pass = exit 0, "OK"
```

- `TestCoerceLines` covers the desync guard offline — pass = a short list, a
  long list and a non-list all coerce to `None`.
- The gameplan loads and declares its result shape:

  ```sh
  python3 -c "
  from kerness.gameplan_loader import load_gameplan
  g = load_gameplan('harness/subtitle_translate.md')
  print(sorted(f.name for f in g.harness.result))
  print([p.name for p in g.harness.loop.phases])"
  ```

  pass = `['glossary', 'lines', 'notes']` then
  `['draft', 'review', 'finalize']`.
- End to end needs credentials and is covered by `test/run_test.sh` — pass =
  the output SRT is in the target script, not a passthrough of the source.

## Open Gaps / Roadmap

- **No offline coverage of the ladder.** Steps 1–3 of `_translate_batch` have
  never been exercised by a test; a fake provider would make all four reachable.
- The endpoint recorded for this project stalls or returns empty on roughly one
  request in five, which is why the timing and deadline machinery exists at all.
- Batches are sequential. Cross-batch glossary carry-over is what forbids
  parallelism, but batches with no shared names could overlap.
- A batch that exhausts the ladder keeps source text and is only reported at the
  end, as a time range. There is no machine-readable list of what failed.
- `max_rounds` is left to the gameplan; only `max_turns` is plumbed through.
