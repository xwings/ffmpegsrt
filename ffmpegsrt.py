#!/usr/bin/env python3
"""ffmpegsrt entry point.

Runnable straight from a checkout — the repository root is put on ``sys.path``
so ``python3 ffmpegsrt.py`` works without installing anything first.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ffmpegsrt.cli import main  # noqa: E402 — must follow the path insert

if __name__ == "__main__":
    raise SystemExit(main())
