"""Shared error base.

Every failure the tool anticipates — a missing ffmpeg, an unreadable file, an
unset key, a model that will not load — derives from :class:`FfmpegSrtError`,
so the CLI can print one clean line for all of them and let anything else
surface as a traceback worth reporting.
"""

from __future__ import annotations


class FfmpegSrtError(RuntimeError):
    """Base class for anticipated, user-facing failures."""
