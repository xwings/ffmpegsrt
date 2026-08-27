"""Make the bundled clawstick submodule importable.

Clawstick lives at ``vendor/clawstick`` as a git submodule.  Installing it
(``pip install -e vendor/clawstick``) is the tidy route, but a plain
``git clone --recursive`` followed by ``python3 ffmpegsrt.py`` should also just
work, so an uninstalled checkout is added to ``sys.path`` as a fallback.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from ffmpegsrt.errors import FfmpegSrtError

#: Repository root — two levels up from this file (ffmpegsrt/_vendor.py).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENDOR_CLAWSTICK = PROJECT_ROOT / "vendor" / "clawstick"


class ClawstickMissing(FfmpegSrtError):
    """The clawstick submodule is neither installed nor checked out."""


def ensure_clawstick() -> None:
    """Put clawstick on ``sys.path`` if it is not already importable.

    Raises:
        ClawstickMissing: If the submodule directory is empty, which is what a
            clone without ``--recursive`` leaves behind.
    """
    if importlib.util.find_spec("clawstick") is not None:
        return

    if (VENDOR_CLAWSTICK / "clawstick" / "__init__.py").is_file():
        sys.path.insert(0, str(VENDOR_CLAWSTICK))
        return

    raise ClawstickMissing(
        "clawstick was not found. It ships as a git submodule — run:\n"
        "    git submodule update --init --recursive\n"
        "and optionally `pip install -e vendor/clawstick`."
    )
