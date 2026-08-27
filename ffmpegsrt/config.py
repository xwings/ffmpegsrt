"""Credential and endpoint resolution.

Nothing here ever bakes in a key.  Values are resolved CLI flag > environment
> ``.env`` in the working directory, and the key is redacted anywhere it might
reach a log or a traceback.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ffmpegsrt.errors import FfmpegSrtError

ENV_API_BASE = "FFMPEGSRT_API_BASE"
ENV_API_KEY = "FFMPEGSRT_API_KEY"
ENV_MODEL = "FFMPEGSRT_MODEL"

#: Files consulted for fallback values, nearest first.  Both are gitignored.
_DOTENV_NAMES = (".env.local", ".env")


class ConfigError(FfmpegSrtError):
    """Raised when translation was requested without usable credentials."""


def redact(secret: str | None) -> str:
    """Render a key safe to print."""
    if not secret:
        return "<unset>"
    if len(secret) <= 6:
        return "***"
    return f"{secret[:3]}***{secret[-2:]}"


def load_dotenv(start: Path | None = None) -> dict[str, str]:
    """Read ``KEY=value`` pairs out of a ``.env`` beside the project.

    Deliberately minimal — no interpolation, no export keyword, no multi-line
    values — so the tool keeps its dependency list to Whisper and PyYAML.
    """
    values: dict[str, str] = {}
    root = Path(start) if start else Path.cwd()
    for name in _DOTENV_NAMES:
        path = root / name
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip("'\"")
            # Nearest file wins; do not let .env overwrite .env.local.
            values.setdefault(key.strip(), value)
    return values


@dataclass
class LLMConfig:
    """Everything needed to talk to an OpenAI-compatible endpoint."""

    api_base: str
    api_key: str
    model: str

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"LLMConfig(api_base={self.api_base!r}, "
            f"api_key={redact(self.api_key)!r}, model={self.model!r})"
        )


def resolve_llm_config(
    api_base: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    *,
    project_root: Path | None = None,
) -> LLMConfig:
    """Resolve endpoint settings, preferring explicit arguments.

    Raises:
        ConfigError: If any of the three values is still missing, naming the
            flag and the environment variable that would supply it.
    """
    dotenv = load_dotenv(project_root)

    def pick(explicit: str | None, env_name: str) -> str:
        return (explicit or os.environ.get(env_name) or dotenv.get(env_name) or "").strip()

    resolved = LLMConfig(
        api_base=pick(api_base, ENV_API_BASE),
        api_key=pick(api_key, ENV_API_KEY),
        model=pick(model, ENV_MODEL),
    )

    missing = [
        hint
        for value, hint in (
            (resolved.api_base, f"--api-base or {ENV_API_BASE}"),
            (resolved.api_key, f"--api-key or {ENV_API_KEY}"),
            (resolved.model, f"--llm-model or {ENV_MODEL}"),
        )
        if not value
    ]
    if missing:
        raise ConfigError(
            "translation needs an OpenAI-compatible endpoint, but these are "
            "unset: " + "; ".join(missing) + ". Copy .env.example to .env and "
            "fill it in, or export the variables."
        )
    return resolved
