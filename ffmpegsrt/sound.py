"""Non-speech sound events.

Whisper occasionally labels what it hears instead of transcribing it —
``[Music]``, ``(laughs)``, ``♪♪``, ``（笑）``.  Left alone those labels are
translated as if they were dialogue, or, once the gameplan blanks them, dropped
from the file entirely.  This module recognises them so the pipeline can carry
them through as action tags: ``[cry]`` in the source, ``[哭泣]`` in the
translation.

Nothing here invents a sound.  A cue becomes a tag only when the recogniser
already wrote one down.
"""

from __future__ import annotations

import re

from ffmpegsrt.srt import Cue

#: Bracket pairs a recogniser wraps a sound label in, including the full-width
#: forms that show up in CJK output.  ``*`` is its own opener and closer.
_WRAPPED_RE = re.compile(
    r"""^\s*
    (?: \[ \s*(?P<square>[^\[\]]+?)\s* \]
      | \( \s*(?P<round>[^()]+?)\s* \)
      | （ \s*(?P<wide>[^（）]+?)\s* ）
      | 【 \s*(?P<lenticular>[^【】]+?)\s* 】
      | \* \s*(?P<star>[^*]+?)\s* \*
    )
    \s*$""",
    re.VERBOSE,
)

#: A run of musical notes, optionally with a label between them.  Whisper emits
#: a bare ``♪`` pair for music it will not transcribe.
_NOTE_RE = re.compile(r"^\s*[♪♫]+\s*(?P<label>[^♪♫]*?)\s*[♪♫]*\s*$")

#: What a bare note run is called once it has no label of its own.
_BARE_NOTE_LABEL = "music"


def detect(text: str) -> str | None:
    """Return the sound label if *text* is entirely a non-speech marker.

    Only whole-cue markers count.  A cue that mixes a marker with dialogue
    ("[Music] I can't wait") is speech that happens to mention a sound, and
    rewriting it would cost the line.

    Returns:
        The label with its wrapper removed and surrounding space trimmed, or
        ``None`` when the cue is ordinary dialogue.
    """
    match = _WRAPPED_RE.match(text)
    if match:
        label = next(value for value in match.groupdict().values() if value)
        return label.strip() or None

    match = _NOTE_RE.match(text)
    if match:
        return match.group("label").strip() or _BARE_NOTE_LABEL

    return None


def strip_brackets(text: str) -> str:
    """Unwrap a label the translator handed back still wrapped.

    The harness is asked to keep the brackets so it can see that a cue is a
    tag rather than a line of dialogue.  :meth:`Cue.display` adds them back on
    the way out, so they have to come off before the label is stored.
    """
    return detect(text) or text.strip()


def classify(cues: list[Cue]) -> int:
    """Mark every cue in *cues* that is a non-speech marker.

    A marked cue keeps its label in ``text`` without the wrapper; the wrapper
    is reapplied by :meth:`Cue.display` so that every output mode brackets it
    the same way.

    Returns:
        How many cues were marked, for the caller's progress line.
    """
    found = 0
    for cue in cues:
        label = detect(cue.text)
        if label is None:
            continue
        cue.text = label
        cue.sound = True
        found += 1
    return found
