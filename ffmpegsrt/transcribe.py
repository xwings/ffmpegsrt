"""Speech recognition via faster-whisper."""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ffmpegsrt.errors import FfmpegSrtError
from ffmpegsrt.srt import Cue

#: Ordered smallest to largest; used only for the ``--model`` help text, since
#: faster-whisper also accepts a local path or any CTranslate2 repo id.
KNOWN_MODELS = (
    "tiny", "base", "small", "medium",
    "large-v2", "large-v3", "distil-large-v3",
)


class TranscriptionError(FfmpegSrtError):
    """Whisper could not be loaded or produced nothing usable."""


#: Shared objects CTranslate2 dlopen()s by soname on the first GPU op, in
#: dependency order — cuDNN links against cuBLAS, so cuBLAS has to land first.
_CUDA_SONAMES = (
    "libcublas.so.12",
    "libcublasLt.so.12",
    "libcudnn.so.9",
)


def _nvidia_lib_dirs() -> list[Path]:
    """Return ``nvidia/*/lib`` directories from installed NVIDIA pip wheels."""
    dirs: list[Path] = []
    for entry in sys.path:
        nvidia = Path(entry) / "nvidia"
        if not nvidia.is_dir():
            continue
        dirs.extend(sorted(p for p in nvidia.glob("*/lib") if p.is_dir()))
    return dirs


def preload_cuda_libraries() -> list[str]:
    """Load the CUDA runtime libraries out of NVIDIA's pip wheels.

    ``pip install nvidia-cublas-cu12 nvidia-cudnn-cu12`` puts the shared
    objects under ``site-packages/nvidia/*/lib``, which is not on the loader's
    search path. CTranslate2 asks for them by bare soname, so without help the
    first GPU operation dies with "Library libcublas.so.12 is not found" —
    after the model has loaded, which makes it look like a decode failure
    rather than a missing dependency.

    Loading them here with ``RTLD_GLOBAL`` registers each soname with the
    dynamic linker, so CTranslate2's later ``dlopen`` resolves to the copy
    already in memory. Failures are ignored: the libraries may legitimately be
    installed system-wide, in which case CTranslate2 finds them unaided.

    Returns:
        The sonames successfully preloaded, for diagnostics.
    """
    loaded: list[str] = []
    lib_dirs = _nvidia_lib_dirs()
    if not lib_dirs:
        return loaded

    for soname in _CUDA_SONAMES:
        for lib_dir in lib_dirs:
            candidate = lib_dir / soname
            if not candidate.is_file():
                continue
            try:
                ctypes.CDLL(str(candidate), mode=ctypes.RTLD_GLOBAL)
            except OSError:
                continue
            loaded.append(soname)
            break
    return loaded


def _explain(exc: Exception, device: str) -> str:
    """Turn a CTranslate2 loader failure into something actionable.

    "Library libcublas.so.12 is not found" says nothing about what to install,
    and it arrives after the model has apparently loaded fine, so it reads as
    a decode bug rather than a missing CUDA runtime.
    """
    message = str(exc)
    lowered = message.lower()

    if "libcublas" in lowered or "libcudnn" in lowered or "cannot be loaded" in lowered:
        return (
            f"{message}\n"
            "  CTranslate2 needs the CUDA 12 runtime libraries, which are "
            "separate from the driver. Install them into this environment:\n"
            "      pip install nvidia-cublas-cu12 nvidia-cudnn-cu12\n"
            "  or re-run with --device cpu."
        )
    if "out of memory" in lowered or "cuda_error_out_of_memory" in lowered:
        return (
            f"{message}\n"
            "  The GPU ran out of memory. Try a smaller --model, "
            "--compute-type int8, or --device cpu."
        )
    if "no cuda" in lowered or "no gpu" in lowered or "cuda driver" in lowered:
        return (
            f"{message}\n"
            "  No usable CUDA device was found. Re-run with --device cpu."
        )
    return message


def default_compute_type(device: str) -> str:
    """Pick a sensible compute type for *device*.

    ``int8`` is the right default on CPU, where it is a large speedup for a
    small accuracy cost. On a GPU there is no reason to pay that cost:
    ``float16`` is both faster and more accurate there.
    """
    return "int8" if device == "cpu" else "float16"


@dataclass
class Transcript:
    """Recognised cues plus what the recogniser reported about the audio."""

    cues: list[Cue]
    language: str
    language_probability: float
    duration: float


def transcribe(
    audio: str | Path,
    *,
    language: str | None = None,
    model_size: str = "small",
    device: str = "cpu",
    compute_type: str | None = None,
    beam_size: int = 5,
    vad_filter: bool = True,
    on_progress: Callable[[Cue, float], None] | None = None,
) -> Transcript:
    """Transcribe *audio* into cues.

    Args:
        audio: Path to an audio file (mono 16 kHz PCM is ideal).
        language: ISO 639-1 code, or ``None`` to let Whisper detect it.
        model_size: Model name, local directory, or CTranslate2 repo id.
        device: ``cpu``, ``cuda``, or ``auto``.
        compute_type: e.g. ``int8`` or ``float16``. ``None`` picks per device.
        beam_size: Beam width for decoding.
        vad_filter: Drop non-speech with Silero VAD before decoding. Keeps
            Whisper from hallucinating dialogue over music and room tone.
        on_progress: Called with each cue and the audio duration as segments
            are decoded — faster-whisper yields lazily, so this is the only
            way to show progress on a feature-length file.

    Raises:
        TranscriptionError: If faster-whisper is missing or nothing was heard.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise TranscriptionError(
            "faster-whisper is not installed. Run `pip install -r requirements.txt`."
        ) from exc

    if device != "cpu":
        preload_cuda_libraries()

    if compute_type is None:
        compute_type = default_compute_type(device)

    try:
        model = WhisperModel(model_size, device=device, compute_type=compute_type)
    except Exception as exc:
        raise TranscriptionError(
            f"could not load Whisper model {model_size!r} on {device!r} "
            f"with compute type {compute_type!r}: {_explain(exc, device)}"
        ) from exc

    segments, info = model.transcribe(
        str(audio),
        language=language,
        beam_size=beam_size,
        vad_filter=vad_filter,
        # Long features are where Whisper's context carry-over turns into
        # repetition loops — the same line echoing for minutes. Cutting the
        # carry-over costs a little coherence and avoids that failure mode.
        condition_on_previous_text=False,
    )

    cues: list[Cue] = []
    try:
        # CTranslate2 defers loading its CUDA libraries until the first GPU
        # op, which happens here rather than at construction — so a missing
        # cuBLAS surfaces mid-iteration and has to be caught here too.
        for segment in segments:
            text = (segment.text or "").strip()
            if not text:
                continue
            cue = Cue(start=float(segment.start), end=float(segment.end), text=text)
            cues.append(cue)
            if on_progress:
                on_progress(cue, float(info.duration or 0.0))
    except RuntimeError as exc:
        raise TranscriptionError(
            f"transcription failed on {device!r}: {_explain(exc, device)}"
        ) from exc

    if not cues:
        raise TranscriptionError(
            "no speech was recognised — check that the file has an audible "
            "dialogue track, or try a larger --model."
        )

    return Transcript(
        cues=cues,
        language=info.language or (language or "unknown"),
        language_probability=float(getattr(info, "language_probability", 0.0) or 0.0),
        duration=float(info.duration or 0.0),
    )
