"""Subtitle cues and SRT serialisation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

#: 00:01:02,345 --> 00:01:04,890
_TIMING_RE = re.compile(
    r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,.](\d{1,3})"
)


@dataclass
class Cue:
    """One subtitle cue.

    ``text`` is always the transcribed source line.  ``translated`` is filled
    in later by the translation pass and stays ``None`` when ``-t`` was not
    requested, which is how :func:`write_srt` tells the two modes apart.

    When ``sound`` is set the cue is a non-speech event rather than dialogue.
    Both bodies then hold a bare label — ``cry``, not ``[cry]`` — and
    :meth:`display` brackets them, so every output mode marks a sound the same
    way.  See :mod:`ffmpegsrt.sound`.
    """

    start: float
    end: float
    text: str
    translated: str | None = None
    sound: bool = False

    def _wrap(self, body: str) -> str:
        """Bracket a sound label; leave dialogue untouched."""
        return f"[{body}]" if self.sound and body else body

    def display(self, mode: str) -> str:
        """Return the body this cue contributes in the given output mode."""
        source = self._wrap(self.text)
        if mode == "source" or self.translated is None:
            return source
        translated = self._wrap(self.translated)
        if mode == "bilingual":
            # Source on top, translation under it — the order subtitle readers
            # expect when the translation is the one they are actually reading.
            return f"{source}\n{translated}" if self.text else translated
        return translated


def format_timestamp(seconds: float) -> str:
    """Format seconds as an SRT timestamp (``HH:MM:SS,mmm``)."""
    if seconds < 0:
        seconds = 0.0
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def parse_timestamp(hours: str, minutes: str, secs: str, millis: str) -> float:
    """Rebuild seconds from the four captured timestamp groups."""
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(secs)
        + int(millis.ljust(3, "0")) / 1000
    )


def shift_and_clip(
    cues: list[Cue],
    start: float | None = None,
    duration: float | None = None,
) -> list[Cue]:
    """Move *cues* onto a clip's timeline and drop what falls outside it.

    ``--start``/``--duration`` cut a working clip whose timeline begins at
    zero.  Cues that came from an existing SRT are still on the original
    timeline, so without this every one of them would sit ``start`` seconds
    late against the trimmed picture.

    Cues straddling an edge are kept and clamped: half a line on screen beats
    a line missing from the cut.

    Args:
        cues: Cues on the original timeline.
        start: Offset the clip was cut from, in seconds.
        duration: Length of the clip, in seconds.

    Returns:
        New cues on the clip's timeline, in order.
    """
    offset = start or 0.0
    kept: list[Cue] = []

    for cue in cues:
        begin = cue.start - offset
        finish = cue.end - offset
        if finish <= 0:
            continue
        if duration is not None and begin >= duration:
            continue
        if duration is not None:
            finish = min(finish, duration)
        kept.append(
            Cue(
                start=max(0.0, begin),
                end=finish,
                text=cue.text,
                translated=cue.translated,
                sound=cue.sound,
            )
        )
    return kept


def write_srt(cues: list[Cue], path: str | Path, mode: str = "translated") -> Path:
    """Write *cues* to *path* as SRT.

    Args:
        cues: Cues in chronological order.
        path: Destination file.
        mode: ``translated``, ``source`` or ``bilingual``.

    Returns:
        The path written.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    blocks: list[str] = []
    index = 1
    for cue in cues:
        body = cue.display(mode).strip()
        if not body:
            # A cue with nothing to show is a hole in the numbering, not a
            # blank subtitle flashed at the viewer.
            continue
        blocks.append(
            f"{index}\n"
            f"{format_timestamp(cue.start)} --> {format_timestamp(cue.end)}\n"
            f"{body}\n"
        )
        index += 1

    # SRT wants a trailing blank line after the last block, and BOM-less UTF-8
    # is what libass assumes when no encoding is declared.
    path.write_text("\n".join(blocks) + "\n", encoding="utf-8")
    return path


def read_srt(path: str | Path) -> list[Cue]:
    """Parse an SRT file into cues, keeping multi-line bodies intact."""
    raw = Path(path).read_text(encoding="utf-8-sig")
    cues: list[Cue] = []

    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        # The leading sequence number is optional in the wild; skip it if present.
        if lines[0].strip().isdigit() and len(lines) > 1:
            lines = lines[1:]
        match = _TIMING_RE.search(lines[0]) if lines else None
        if not match:
            continue
        groups = match.groups()
        cues.append(
            Cue(
                start=parse_timestamp(*groups[:4]),
                end=parse_timestamp(*groups[4:]),
                text="\n".join(lines[1:]).strip(),
            )
        )
    return cues
