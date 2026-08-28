"""Check that the bundled kerness submodule is installed.

Kerness lives at ``vendor/kerness`` as a git submodule.  Unlike a pure-Python
dependency it cannot be made importable by putting a directory on ``sys.path``:
the framework is Rust, and ``kerness._core`` is a compiled extension that has
to be built before anything can import it.  So there is no fallback here, only
a check and the instruction that fixes it.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from ffmpegsrt.errors import FfmpegSrtError

#: Repository root — two levels up from this file (ffmpegsrt/_vendor.py).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENDOR_KERNESS = PROJECT_ROOT / "vendor" / "kerness"

#: The maturin project inside the submodule — what pip is pointed at.
KERNESS_PYTHON = VENDOR_KERNESS / "bindings" / "python"


class KernessMissing(FfmpegSrtError):
    """The kerness submodule is not installed."""


def ensure_kerness() -> None:
    """Raise unless ``kerness`` is importable.

    Raises:
        KernessMissing: If the package is not installed. An unchecked-out
            submodule and an unbuilt one need different fixes, so they are
            reported separately.
    """
    if importlib.util.find_spec("kerness") is not None:
        return

    if not (KERNESS_PYTHON / "pyproject.toml").is_file():
        raise KernessMissing(
            "kerness was not found. It ships as a git submodule — run:\n"
            "    git submodule update --init --recursive\n"
            "    pip install ./vendor/kerness/bindings/python"
        )

    raise KernessMissing(
        "kerness is checked out but not installed. It is a Rust extension and "
        "cannot be imported straight from the source tree — build it with:\n"
        "    pip install ./vendor/kerness/bindings/python\n"
        "That needs a Rust toolchain (rustc 1.88+): https://rustup.rs"
    )
