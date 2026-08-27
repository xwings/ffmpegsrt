"""Language code normalisation.

Two different consumers need two different things from a ``-l jp`` or
``-t zh_cn`` argument: Whisper wants an ISO 639-1 code, and the translation
prompt wants a name a model will read unambiguously ("Simplified Chinese", not
"zh").  Both come out of the same table.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    """A language as the two downstream stages need to see it."""

    #: ISO 639-1 code understood by Whisper.
    code: str
    #: Human-readable name written into translation prompts.
    name: str

    def __str__(self) -> str:
        return f"{self.name} ({self.code})"


#: Aliases the CLI accepts, mapped to the canonical entry.  Keys are matched
#: after lowercasing and folding ``-`` to ``_``.
_ALIASES: dict[str, Language] = {}


def _register(code: str, name: str, *aliases: str) -> None:
    lang = Language(code=code, name=name)
    for key in (code, name.lower(), *aliases):
        _ALIASES[key.lower().replace("-", "_")] = lang


# Chinese needs two distinct entries: Whisper transcribes both as "zh", but a
# translation target of Simplified vs Traditional is a real difference.
_register("zh", "Simplified Chinese", "zh_cn", "zhs", "zh_hans", "chs", "chinese", "cn")
_register("zh", "Traditional Chinese", "zh_tw", "zh_hk", "zht", "zh_hant", "cht")

_register("ja", "Japanese", "jp", "jpn")
_register("en", "English", "eng", "en_us", "en_gb")
_register("ko", "Korean", "kr", "kor")
_register("es", "Spanish", "spa")
_register("fr", "French", "fra", "fre")
_register("de", "German", "deu", "ger")
_register("it", "Italian", "ita")
_register("pt", "Portuguese", "por", "pt_br")
_register("ru", "Russian", "rus")
_register("ar", "Arabic", "ara")
_register("hi", "Hindi", "hin")
_register("th", "Thai", "tha")
_register("vi", "Vietnamese", "vie")
_register("id", "Indonesian", "ind")
_register("nl", "Dutch", "nld", "dut")
_register("pl", "Polish", "pol")
_register("tr", "Turkish", "tur")
_register("uk", "Ukrainian", "ukr")


def resolve(value: str) -> Language:
    """Resolve a user-supplied language argument.

    Unknown values are not rejected: a bare two-letter code is passed through
    to Whisper as-is, since the model's language list is longer than this
    table and there is no reason to block a code it would have accepted.

    Raises:
        ValueError: If *value* is neither a known alias nor a plausible code.
    """
    key = value.strip().lower().replace("-", "_")
    if key in _ALIASES:
        return _ALIASES[key]
    if key.isalpha() and len(key) in (2, 3):
        return Language(code=key, name=value.strip())
    raise ValueError(
        f"unrecognised language {value!r}; "
        f"try one of: {', '.join(sorted({lang.name for lang in _ALIASES.values()}))}"
    )


def known_aliases() -> list[str]:
    """Return the accepted aliases, for help text."""
    return sorted(_ALIASES)
