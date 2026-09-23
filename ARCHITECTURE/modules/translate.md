---
eatmycode_version: "2.1.0"
---
# Translate

Owner: [Project architecture](../../ARCHITECTURE.md)

Read when: touching `ffmpegsrt/translate.py`, `ffmpegsrt/_vendor.py`, `harness/subtitle_translate.md` or the `vendor/kerness` pin; changing batching, the escalation ladder, agents, prompts, timeouts, or anything that could alter cue count or order.

## Responsibility and Status

Translates batches of cues through a kerness session (Translator drafts, Reviewer objects, Editor finalises), carries a glossary and three context cues between batches, and fills `Cue.translated` in place without ever changing how many cues exist.

Status: `in progress`. Only `_coerce_lines` and `_numbered` are unit-tested. Every other path needs a live endpoint and a built kerness, neither present in this environment; the `more reliable kerness` commits are the latest evidence of live runs.

## Code Map

| Path / symbol | Role |
| --- | --- |
| `harness/subtitle_translate.md` | The gameplan. YAML frontmatter declares the agents, phases `draft`, `review`, `finalize`, `max_turns: 12`, `max_rounds: 2`, and result fields `lines`, `glossary`, `notes`; the body is the editor's instructions. |
| `ffmpegsrt/translate.py:29,34,40` `CONTEXT_CUES`, `DEFAULT_TIMEOUT_SEC`, `BATCH_BUDGET_SEC` | 3 context cues; 60 s per request; 300 s per batch in the harness. |
| `ffmpegsrt/translate.py:88` `_timed_provider` | Subclasses `kerness.CustomProvider`: reports each failed or empty request as it happens and fails instantly once the batch deadline has passed. |
| `ffmpegsrt/translate.py:192` `_coerce_lines` | Returns `None` on wrong length or non-list; unwraps dict items; strips self-numbering. **The desync guard.** |
| `ffmpegsrt/translate.py:222` `SubtitleTranslator` | `.translate` (`:266`) batches and fills cues; `_translate_batch` (`:332`) is the ladder; `_run_session` (`:395`) seats the three agents; `_direct_fallback` (`:474`) asks for a bare JSON array. |
| `ffmpegsrt/_vendor.py:29` `ensure_kerness` | Raises `KernessMissing` with the submodule-init or build command. |
| `test/test_units.py:367` `TestCoerceLines` | Length guard, dict shapes, numbering, bracketed sound cues. |

## Local Conventions

Root baseline suffices. `kerness` is imported inside `SubtitleTranslator.__init__` after `ensure_kerness()`, so transcription-only runs never need the Rust build. Broad `except Exception` here is intentional and marked `noqa: BLE001`: any provider fault escalates down the ladder instead of aborting the film.

## Contracts and Invariants

- **One line per cue, same order.** `_coerce_lines` rejects any `lines` whose length differs from the batch. Kerness returns type defaults, not errors, on a malformed result block, so a short list must never be padded. The gameplan states the same rule to the agents. Any change that could merge, split, reorder or drop cues is documented here before it is written.
- **Escalation ladder** (`_translate_batch`): (1) harness session; (2) if unusable and the batch is still under `BATCH_BUDGET_SEC`, one more session with a wrong-count warning appended; (3) one direct `chat_with_retries` call, no agents; (4) keep source text and record the range in `stats.failed_ranges`. Aborting at batch 47 would waste 46 paid batches.
- **Deadline**: `_run_session` sets `self._deadline`; `TimedProvider.chat` raises `BudgetSpent` past it; `finally` clears it so the direct fallback is not starved.
- **Sound cues** travel bracketed via `_numbered` and are unwrapped with `sound.strip_brackets` on return (`ffmpegsrt/translate.py:307`). An empty translation counts in `untranslated_cues`; `write_srt` then drops that cue's body.
- **Session setup**: a fresh `Session` per batch with `session_file=None` (a stale file would resume the wrong batch), `turn_delay_sec=0`, `AccessPolicy(allowed_commands=["*"])` because a denial reaches the agent as an error it must work around, and `_QuietChannel` unless `-v`. The Editor has `role="orchestrator"`; the other two are participants.
- **Provider**: `temperature=0.3`, `timeout_sec=int(timeout)`, credentials from `LLMConfig`.
- **Glossary** from each result merges into `stats.glossary` and is rendered into the next topic.
- **Failures**: `TranslationError` for a missing gameplan; provider errors never escape `translate()`.

## Dependencies and Boundaries

Incoming: [CLI](cli.md) constructs the translator and prints `TranslationStats`. Outgoing: `kerness` (`Session`, `CustomProvider`, `ConsoleChannel`, `AccessPolicy`, `SessionResult.fields`), [Subtitles](subtitles.md) (`Cue`, `strip_brackets`), [Config](config.md) (`LLMConfig`, `Language`). `vendor/kerness` is upstream (`.gitmodules`); bump the pin, never edit it in this repo.

## Change Guide

| Change trigger | Inspect / extend | Required docs / checks |
| --- | --- | --- |
| Prompt or agent behaviour | Gameplan body, `_run_session` personas, `_build_topic` | Gameplan load check below; end-to-end run |
| Result shape | Gameplan `result:` and the field reads in `_run_session` | This page; `TestCoerceLines` |
| Ladder or budgets | `_translate_batch`, the constants, `--llm-timeout`/`--batch-timeout` in [CLI](cli.md) | Help defaults follow the constants |
| Kerness API change | `_timed_provider`, `_run_session`, the `ensure_kerness` message | Bump the pin; rebuild; end-to-end run |
| Anything touching cue count or order | The first invariant above; `write_srt` renumbering | Root System Design |

## Verification

```sh
python3 test/test_units.py            # TestCoerceLines ok
python3 -c "
from kerness.gameplan_loader import load_gameplan
g = load_gameplan('harness/subtitle_translate.md')
print(sorted(f.name for f in g.harness.result)); print([p.name for p in g.harness.loop.phases])"
```

Pass: `['glossary', 'lines', 'notes']` then `['draft', 'review', 'finalize']`. The second command needs kerness built (`pip install ./vendor/kerness/bindings/python`, rustc 1.88+) and was not run here. Live translation: [End-to-end test](../topics/end-to-end-test.md), which fails when the output is still ASCII.

## Known Gaps

- No offline coverage of ladder steps 1 to 3; a fake provider would make all four reachable. The largest untested surface in the project.
- Batches run sequentially because of glossary carry-over.
- Failed ranges are reported only as text at the end; no machine-readable list.
- `max_rounds` is left to the gameplan; `max_turns` is plumbed through but the CLI never sets it.
- The `load_gameplan` check above is unverified against the current kerness pin.
