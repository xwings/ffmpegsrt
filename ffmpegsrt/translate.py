"""Subtitle translation driven by a kerness multi-agent harness.

Every batch of cues is run through a full kerness ``Session``: a translator
drafts, a reviewer attacks the draft, and an orchestrator issues the final
line-for-line result. The gameplan lives in ``harness/subtitle_translate.md``.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ffmpegsrt import sound
from ffmpegsrt._vendor import PROJECT_ROOT, ensure_kerness
from ffmpegsrt.config import LLMConfig
from ffmpegsrt.errors import FfmpegSrtError
from ffmpegsrt.langs import Language
from ffmpegsrt.srt import Cue

#: Shipped alongside the code; the Session is given this path directly.
DEFAULT_GAMEPLAN = PROJECT_ROOT / "harness" / "subtitle_translate.md"

#: Cues carried into the next batch as context so pronouns and half-finished
#: sentences at a batch boundary still resolve.
CONTEXT_CUES = 3

#: Per-request HTTP timeout. A healthy endpoint answers a full orchestrator
#: turn in well under half a minute; anything past this is a stalled request,
#: and waiting it out costs more than reissuing it.
DEFAULT_TIMEOUT_SEC = 60.0

#: Wall-clock a batch may spend in the harness before the escalation ladder
#: stops climbing. A session that runs this long is not thinking hard, it is
#: waiting on requests that time out — and a second session would wait the
#: same way. Past this, take the one direct call and move on.
BATCH_BUDGET_SEC = 300.0


class TranslationError(FfmpegSrtError):
    """Translation could not be completed for a batch."""


class EmptyReply(FfmpegSrtError):
    """The endpoint answered with no content at all."""


class BudgetSpent(FfmpegSrtError):
    """The batch ran out of wall clock; no further requests are worth making."""


@dataclass
class TranslationStats:
    """How the run went, for the closing report."""

    batches: int = 0
    sessions_run: int = 0
    retried_batches: int = 0
    fallback_batches: int = 0
    untranslated_cues: int = 0
    #: Requests that timed out or errored at the endpoint, across every
    #: attempt. Retries usually hide these; a large number here is why a run
    #: took hours rather than minutes.
    failed_requests: int = 0
    glossary: dict[str, str] = field(default_factory=dict)
    #: Batches that exhausted every strategy and kept their source text.
    failed_ranges: list[str] = field(default_factory=list)


class _QuietChannel:
    """Swallow agent chatter.

    Kerness defaults to a ConsoleChannel, which would interleave three
    agents' full turns with the CLI's own progress output. ``-v`` swaps this
    for a real ConsoleChannel.
    """

    def send(self, sender: str, message: str) -> None:  # noqa: D102
        pass

    def send_system(self, message: str) -> None:  # noqa: D102
        pass


def _timed_provider(
    kerness,
    llm: LLMConfig,
    timeout_sec: float,
    on_failure: Callable[[float, Exception], None],
    deadline: Callable[[], float | None],
) -> object:
    """Build the provider, instrumented to report requests that don't land.

    Kerness retries internally and only logs a bare ``Provider error for
    <turn>`` once every attempt is gone, which from the outside is
    indistinguishable from the process having hung. Timing each attempt here
    is what turns those minutes of silence into a line of output.

    The provider is also where a batch's deadline is enforced, because it is
    the only place the session passes through often enough. A session cannot
    be interrupted from outside, but it can be starved: once the deadline is
    past, every request fails instantly and the loop unwinds in seconds
    instead of grinding through its remaining turns at one timeout each.
    """

    class TimedProvider(kerness.CustomProvider):
        def chat(self, model, messages, tools=None):  # noqa: D102, ANN001
            due = deadline()
            if due is not None and time.monotonic() > due:
                raise BudgetSpent("batch is out of time")
            started = time.monotonic()
            try:
                response = super().chat(model, messages, tools=tools)
            except Exception as exc:  # noqa: BLE001 - reported, then re-raised
                on_failure(time.monotonic() - started, exc)
                raise
            # A reasoning model that spends its whole output budget thinking
            # answers with empty content. Kerness treats that as a failed
            # attempt, so name it here rather than let it read as a stall.
            if not response.content.strip() and not response.tool_calls:
                stop = response.stop_reason or "no stop reason"
                on_failure(
                    time.monotonic() - started,
                    EmptyReply(
                        f"{stop}, "
                        f"{response.usage.get('completion_tokens', '?')} tokens "
                        "spent, none of them content"
                    ),
                )
            return response

    return TimedProvider(
        url=llm.api_base,
        api_key=llm.api_key,
        temperature=0.3,
        timeout_sec=int(timeout_sec),
    )


def _numbered(cues: list[Cue]) -> str:
    """Render a batch as the numbered list the gameplan expects.

    A sound cue is shown bracketed so the agents can tell an action tag from a
    line of dialogue and translate the label rather than describing it.
    """
    return "\n".join(
        f"{i}. {cue.display('source')}" for i, cue in enumerate(cues, start=1)
    )


def _build_topic(
    batch: list[Cue],
    source: Language | None,
    target: Language,
    context: list[Cue],
    glossary: dict[str, str],
    batch_no: int,
    batch_total: int,
) -> str:
    """Assemble the session topic: the work, plus what carried over."""
    source_name = source.name if source else "the source language"
    parts = [
        f"Translate subtitle batch {batch_no} of {batch_total} from "
        f"{source_name} into {target.name}.",
        f"There are exactly {len(batch)} numbered cues. Return exactly "
        f"{len(batch)} lines.",
    ]

    if context:
        parts.append(
            "Preceding cues, already translated, for context only — do NOT "
            "include these in your output:\n"
            + "\n".join(
                f"  {cue.display('source')}  ->  {cue.display('translated')}"
                for cue in context
            )
        )

    if glossary:
        parts.append(
            "Established glossary — reuse these renderings:\n"
            + "\n".join(f"  {k} -> {v}" for k, v in sorted(glossary.items()))
        )

    parts.append(f"Cues to translate:\n{_numbered(batch)}")
    return "\n\n".join(parts)


def _coerce_lines(raw: object, expected: int) -> list[str] | None:
    """Normalise the harness's ``lines`` field into a list of strings.

    Returns ``None`` when the value cannot be trusted to line up with the
    batch. Kerness's result parser returns ``[]`` rather than raising on
    malformed JSON, so a wrong length is the signal that something went wrong
    upstream — it must not be papered over.
    """
    if not isinstance(raw, list) or len(raw) != expected:
        return None

    lines: list[str] = []
    for item in raw:
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            # Models like to answer [{"1": "..."}] or [{"text": "..."}].
            text = str(
                item.get("text")
                or item.get("line")
                or item.get("translation")
                or next(iter(item.values()), "")
            )
        else:
            text = str(item)
        # Strip a leading "3. " if the model numbered its own output.
        lines.append(re.sub(r"^\s*\d+[.)]\s*", "", text).strip())
    return lines


class SubtitleTranslator:
    """Runs the kerness harness over batches of cues."""

    def __init__(
        self,
        llm: LLMConfig,
        *,
        gameplan: str | Path = DEFAULT_GAMEPLAN,
        batch_size: int = 40,
        verbose: bool = False,
        max_turns: int | None = None,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        batch_budget_sec: float = BATCH_BUDGET_SEC,
        on_note: Callable[[str], None] | None = None,
    ) -> None:
        ensure_kerness()
        import kerness  # noqa: PLC0415 — only imported once the check passes

        self._kerness = kerness
        self._llm = llm
        self._gameplan = str(gameplan)
        self._batch_size = max(1, batch_size)
        self._verbose = verbose
        self._max_turns = max_turns
        self._batch_budget_sec = batch_budget_sec
        self._on_note = on_note
        self._stats = TranslationStats()
        #: Monotonic time the current batch's harness session must be done by,
        #: or None outside a session.
        self._deadline: float | None = None
        self._provider = _timed_provider(
            kerness, llm, timeout_sec, self._on_request_failure,
            lambda: self._deadline,
        )

        if not Path(self._gameplan).is_file():
            raise TranslationError(f"gameplan not found: {self._gameplan}")

    # -- public API ------------------------------------------------------

    def translate(
        self,
        cues: list[Cue],
        source: Language | None,
        target: Language,
        *,
        on_batch: Callable[[int, int, int], None] | None = None,
    ) -> TranslationStats:
        """Translate *cues* in place, filling each cue's ``translated`` field.

        Args:
            cues: Cues to translate, in order.
            source: Source language, or ``None`` if it was auto-detected and
                should be described generically to the model.
            target: Target language.
            on_batch: Called with ``(batch_no, batch_total, cues_done)`` before
                each batch, for progress reporting.

        Returns:
            Stats describing retries, fallbacks and the accumulated glossary.
        """
        stats = self._stats = TranslationStats()
        batches = [
            cues[i : i + self._batch_size]
            for i in range(0, len(cues), self._batch_size)
        ]
        stats.batches = len(batches)
        done = 0

        for batch_no, batch in enumerate(batches, start=1):
            if on_batch:
                on_batch(batch_no, len(batches), done)

            context = cues[max(0, done - CONTEXT_CUES) : done]
            lines = self._translate_batch(
                batch, source, target, context, stats, batch_no, len(batches)
            )

            for cue, line in zip(batch, lines):
                # Sound cues travel bracketed so the agents can see what they
                # are; the wrapper comes off again because display() re-adds it.
                cue.translated = sound.strip_brackets(line) if cue.sound else line
                if not cue.translated:
                    stats.untranslated_cues += 1
            done += len(batch)

        return stats

    # -- internals -------------------------------------------------------

    def _note(self, message: str) -> None:
        """Tell the caller something that explains a long silence."""
        if self._on_note:
            self._on_note(message)

    def _on_request_failure(self, elapsed: float, exc: Exception) -> None:
        """Report one failed HTTP attempt as it happens, not minutes later."""
        self._stats.failed_requests += 1
        what = (
            "returned nothing" if isinstance(exc, EmptyReply)
            else "did not answer"
        )
        self._note(
            f"endpoint {what} after {elapsed:.0f}s ({exc}); retrying"
        )

    def _translate_batch(
        self,
        batch: list[Cue],
        source: Language | None,
        target: Language,
        context: list[Cue],
        stats: TranslationStats,
        batch_no: int,
        batch_total: int,
    ) -> list[str]:
        """Translate one batch, escalating through three strategies."""
        topic = _build_topic(
            batch, source, target, context, stats.glossary, batch_no, batch_total
        )

        # 1. The harness, as designed.
        started = time.monotonic()
        lines = self._run_session(topic, target, len(batch), stats)
        if lines is not None:
            return lines

        # 2. The harness again — a malformed closing block is often a one-off,
        #    and the transcript is discarded anyway. Skipped once the batch has
        #    outrun its budget: a session that slow failed on stalled requests
        #    rather than on its output, and a second one stalls the same way.
        elapsed = time.monotonic() - started
        if elapsed < self._batch_budget_sec:
            stats.retried_batches += 1
            lines = self._run_session(
                topic
                + "\n\nIMPORTANT: the previous attempt returned the wrong number "
                f"of lines. The result JSON must contain exactly {len(batch)} "
                "entries in `lines`.",
                target,
                len(batch),
                stats,
            )
            if lines is not None:
                return lines
        else:
            self._note(
                f"batch {batch_no} spent {elapsed:.0f}s in the harness without "
                "a usable result; skipping the retry session"
            )

        # 3. One direct call, no agents. Worse translation than the harness
        #    produces, but a translated batch beats a missing one.
        stats.fallback_batches += 1
        self._note(f"batch {batch_no}: no usable harness result, calling the "
                   "model directly")
        lines = self._direct_fallback(batch, source, target)
        if lines is not None:
            return lines

        # 4. Give up on this batch but not on the film. Aborting a two-hour
        #    run at batch 47 would throw away 46 batches of paid-for work; the
        #    source text at least keeps those cues on screen and in sync.
        stats.failed_ranges.append(
            f"batch {batch_no}/{batch_total} "
            f"({batch[0].start:.1f}s-{batch[-1].end:.1f}s)"
        )
        return [cue.text for cue in batch]

    def _run_session(
        self,
        topic: str,
        target: Language,
        expected: int,
        stats: TranslationStats,
    ) -> list[str] | None:
        """Run one kerness session; return its lines, or None if unusable.

        The session gets the batch budget as a deadline. It is cleared on the
        way out so the direct fallback, which is cheap and usually the thing
        that saves the batch, is not starved by the session that preceded it.
        """
        kerness = self._kerness
        session = kerness.Session(
            gameplan=self._gameplan,
            topic=topic,
            provider=self._provider,
            channel=kerness.ConsoleChannel() if self._verbose else _QuietChannel(),
            # No session file: each batch is a fresh run, and a stale one on
            # disk would make kerness resume the previous batch instead.
            session_file=None,
            turn_delay_sec=0.0,
            max_turns=self._max_turns,
            system_prompt=(
                "You are a professional subtitler. Output only what the "
                "instruction asks for, with one line per numbered source cue."
            ),
        )

        session.add_participant(
            name="Translator",
            model=self._llm.model,
            persona=(
                f"A native {target.name} subtitler with a good ear for spoken "
                "register. Writes tight, natural lines that read at a glance."
            ),
        )
        session.add_participant(
            name="Reviewer",
            model=self._llm.model,
            persona=(
                f"A bilingual subtitle editor. Checks the draft against the "
                f"source for meaning, honorifics, name consistency and line "
                f"length. Objects with cue numbers, not general impressions."
            ),
        )
        session.add_orchestrator(
            name="Editor",
            model=self._llm.model,
            persona=(
                "The editor who ships the file. Settles disputes quickly and "
                "guarantees one output line per source cue."
            ),
        )

        stats.sessions_run += 1
        self._deadline = time.monotonic() + self._batch_budget_sec
        try:
            result = session.run()
        except Exception as exc:  # noqa: BLE001 - any provider fault escalates
            if self._verbose:
                print(f"  [harness] session failed: {exc}")
            return None
        finally:
            self._deadline = None

        glossary = result.fields.get("glossary")
        if isinstance(glossary, dict):
            stats.glossary.update(
                {str(k): str(v) for k, v in glossary.items() if k and v}
            )

        return _coerce_lines(result.fields.get("lines"), expected)

    def _direct_fallback(
        self,
        batch: list[Cue],
        source: Language | None,
        target: Language,
    ) -> list[str] | None:
        """Last resort: ask the model once for a bare JSON array."""
        source_name = source.name if source else "the source language"
        prompt = (
            f"Translate each numbered subtitle line from {source_name} into "
            f"{target.name}.\n"
            f"Reply with a JSON array of exactly {len(batch)} strings and "
            "nothing else — one string per numbered line, same order. Use an "
            "empty string for a line with no meaningful content.\n\n"
            f"{_numbered(batch)}"
        )
        try:
            response = self._provider.chat_with_retries(
                self._llm.model,
                [{"role": "user", "content": prompt}],
                purpose="subtitle batch fallback",
            )
        except Exception as exc:  # noqa: BLE001
            if self._verbose:
                print(f"  [fallback] request failed: {exc}")
            return None

        text = response.content or ""
        # The array may be fenced, prefixed with prose, or both.
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end <= start:
            return None
        try:
            return _coerce_lines(json.loads(text[start : end + 1]), len(batch))
        except (ValueError, TypeError):
            return None
