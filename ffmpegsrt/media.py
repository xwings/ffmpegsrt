"""ffmpeg / ffprobe wrappers: probing, audio extraction and subtitle burn-in."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ffmpegsrt.errors import FfmpegSrtError

#: Bundled with most Linux distros and covers the CJK range, which the usual
#: libass default (DejaVu) does not — Chinese would render as tofu boxes.
DEFAULT_FONT = "Droid Sans Fallback"


class MediaError(FfmpegSrtError):
    """An ffmpeg/ffprobe invocation failed or the file is unusable."""


@dataclass
class AudioStream:
    """The audio stream chosen for transcription."""

    index: int
    codec: str
    channels: int
    sample_rate: int
    language: str | None = None


@dataclass
class MediaInfo:
    """What the pipeline needs to know about the input file."""

    path: Path
    duration: float
    has_video: bool
    audio: AudioStream | None


def require_tools() -> None:
    """Fail early and clearly when ffmpeg is not installed."""
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        raise MediaError(
            f"{' and '.join(missing)} not found on PATH. Install ffmpeg "
            "(e.g. `apt install ffmpeg` or `brew install ffmpeg`)."
        )


def _run(cmd: list[str], *, what: str) -> subprocess.CompletedProcess[str]:
    """Run a command, raising :class:`MediaError` with stderr on failure."""
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-12:])
        raise MediaError(f"{what} failed (exit {proc.returncode}):\n{tail}")
    return proc


def probe(path: str | Path) -> MediaInfo:
    """Inspect *path* and return its duration and first audio stream."""
    path = Path(path)
    if not path.is_file():
        raise MediaError(f"input file not found: {path}")

    proc = _run(
        [
            "ffprobe", "-v", "error",
            "-show_streams", "-show_format",
            "-of", "json", str(path),
        ],
        what="ffprobe",
    )
    data = json.loads(proc.stdout or "{}")
    streams = data.get("streams", [])

    audio = None
    for stream in streams:
        if stream.get("codec_type") == "audio":
            audio = AudioStream(
                index=int(stream.get("index", 0)),
                codec=stream.get("codec_name", "?"),
                channels=int(stream.get("channels", 0) or 0),
                sample_rate=int(stream.get("sample_rate", 0) or 0),
                language=(stream.get("tags") or {}).get("language"),
            )
            break

    try:
        duration = float(data.get("format", {}).get("duration", 0.0))
    except (TypeError, ValueError):
        duration = 0.0

    return MediaInfo(
        path=path,
        duration=duration,
        has_video=any(s.get("codec_type") == "video" for s in streams),
        audio=audio,
    )


def extract_audio(
    src: str | Path,
    dest: str | Path,
    *,
    start: float | None = None,
    duration: float | None = None,
) -> Path:
    """Extract mono 16 kHz PCM — the format Whisper resamples to anyway.

    ``-ss`` goes before ``-i`` so ffmpeg seeks by keyframe instead of decoding
    from the top; on a multi-gigabyte film that is the difference between a
    second and several minutes.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["ffmpeg", "-y", "-v", "error"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(src)]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += [
        "-vn",
        "-map", "0:a:0?",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        str(dest),
    ]

    _run(cmd, what="audio extraction")
    if not dest.is_file() or dest.stat().st_size == 0:
        raise MediaError(
            f"no audio was extracted from {src} — the file may have no audio track."
        )
    return dest


def trim(
    src: str | Path,
    dest: str | Path,
    *,
    start: float | None = None,
    duration: float | None = None,
    crf: int = 18,
    preset: str = "veryfast",
) -> Path:
    """Cut a working clip out of *src*.

    Slicing is done once, up front, so that every later stage sees a single
    timeline starting at zero.  Transcribing a slice but burning into the full
    file would silently offset every cue by *start*.

    The clip is re-encoded rather than stream-copied: ``-c copy`` can only cut
    on a keyframe, which would drift the cut point by up to a GOP and desync
    the very timings this function exists to keep straight.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["ffmpeg", "-y", "-v", "error", "-stats"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(src)]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += [
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(dest),
    ]

    _run(cmd, what="trim")
    if not dest.is_file() or dest.stat().st_size == 0:
        raise MediaError(f"trim produced no output at {dest}")
    return dest


def escape_filter_path(path: str | Path) -> str:
    """Escape a path for use inside an ffmpeg filtergraph.

    The filtergraph parser eats ``\\`` and ``:``, and ``'`` terminates the
    quoted argument, so all three have to be neutralised before the path is
    handed to the ``subtitles`` filter. Windows paths like ``C:\\clips`` break
    loudly without this; paths with a comma break silently.
    """
    text = str(path)
    text = text.replace("\\", "\\\\")
    text = text.replace(":", r"\:")
    text = text.replace("'", r"\'")
    return text


def build_force_style(
    font: str = DEFAULT_FONT,
    font_size: int = 20,
    margin_v: int = 28,
) -> str:
    """Build an ASS ``force_style`` string for legible burned-in subtitles.

    White fill with a dark outline and a light shadow stays readable over both
    bright and dark footage, which a plain white fill does not.
    """
    return ",".join(
        [
            f"FontName={font}",
            f"FontSize={font_size}",
            "PrimaryColour=&H00FFFFFF",
            "OutlineColour=&H90000000",
            "BorderStyle=1",
            "Outline=1.6",
            "Shadow=0.6",
            "Alignment=2",
            f"MarginV={margin_v}",
        ]
    )


def burn_in(
    video: str | Path,
    subtitles: str | Path,
    dest: str | Path,
    *,
    font: str = DEFAULT_FONT,
    font_size: int = 20,
    crf: int = 20,
    preset: str = "medium",
    fontsdir: str | Path | None = None,
) -> Path:
    """Render *subtitles* into *video*, writing *dest*.

    The video is necessarily re-encoded — burning in means rewriting pixels —
    but the audio is stream-copied, so nothing is lost there.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    sub_arg = escape_filter_path(Path(subtitles).resolve())
    style = build_force_style(font, font_size)
    vf = f"subtitles='{sub_arg}':force_style='{style}'"
    if fontsdir:
        vf += f":fontsdir='{escape_filter_path(Path(fontsdir).resolve())}'"

    cmd = [
        "ffmpeg", "-y", "-v", "error", "-stats",
        "-i", str(video),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(dest),
    ]

    _run(cmd, what="subtitle burn-in")
    if not dest.is_file() or dest.stat().st_size == 0:
        raise MediaError(f"burn-in produced no output at {dest}")
    return dest
