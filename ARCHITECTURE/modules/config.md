---
eatmycode_version: "2.1.0"
---
# Config

Owner: [Project architecture](../../ARCHITECTURE.md)

Read when: touching `ffmpegsrt/config.py`, `ffmpegsrt/langs.py`, `ffmpegsrt/errors.py` or `.env.example`; changing credential resolution, environment variable names, language aliases, or the error base.

## Responsibility and Status

Three small shared pieces: endpoint credentials resolved without hardcoding or leaking a key; a language argument normalised into the Whisper code and the prompt-ready name; and `FfmpegSrtError`, the base that lets the CLI print one line for every anticipated failure.

Status: `done`. Evidence: `TestConfig` (6 tests) and `TestLanguages` (5) pass, including the `zh` alias regression.

## Code Map

| Path / symbol | Role |
| --- | --- |
| `ffmpegsrt/errors.py:12` `FfmpegSrtError(RuntimeError)` | Base of `MediaError`, `TranscriptionError`, `TranslationError`, `EmptyReply`, `BudgetSpent`, `ConfigError`, `KernessMissing`. |
| `ffmpegsrt/config.py:17-19` `ENV_*` | `FFMPEGSRT_API_BASE`, `FFMPEGSRT_API_KEY`, `FFMPEGSRT_MODEL`; mirrored in `.env.example`. |
| `ffmpegsrt/config.py:29` `redact` | `sk-abcdefghij` becomes `sk-***ij`; six characters or fewer become `***`. |
| `ffmpegsrt/config.py:38` `load_dotenv` | `.env.local` then `.env`, in `start` (cwd) then `PROJECT_ROOT`; nearest wins via `setdefault`. |
| `ffmpegsrt/config.py:71` `LLMConfig` | `api_base`, `api_key`, `model`; `__repr__` redacts the key. |
| `ffmpegsrt/config.py:85` `resolve_llm_config` | Flag, then env, then dotenv; every missing value reported together with its flag and variable name. |
| `ffmpegsrt/langs.py:15` `Language`, `:32` `_register`, `:66` `resolve` | Frozen `(code, name)`; first registration wins; unknown 2 to 3 letter codes pass through. |
| `test/test_units.py:275` `TestLanguages`, `:299` `TestConfig` | Alias table; credential resolution with env and `PROJECT_ROOT` patched out. |

## Local Conventions

Root baseline suffices. The dotenv parser is deliberately minimal (no interpolation, `export` or multi-line values) to avoid a dependency; do not swap in python-dotenv. Alias keys are matched after lowercasing and folding `-` to `_`.

## Contracts and Invariants

- **Precedence**: CLI flag, environment, `.env.local`, `.env`; working directory before checkout root (`test_dotenv_parsing_and_precedence`, `test_checkout_root_is_consulted_when_cwd_has_no_env`). Values lose surrounding whitespace and quotes.
- **Never commit a credential**: `.env`, `.env.local` and `*.key` are gitignored; the key never appears in `repr` (`test_repr_does_not_leak_the_key`). `--api-key` on argv is visible in the process list and the help text says so.
- **Missing credentials** raise `ConfigError` naming every missing `--flag or ENV_VAR`. The CLI calls this before transcription.
- **`zh` means Simplified** (`test_bare_zh_is_simplified`): both Chinese entries share code `zh`; `_register` uses `setdefault` so the first wins. Whisper receives `zh` for either; the variant only changes the translation prompt.
- **Unknown languages**: alphabetic 2 to 3 letter codes pass through as `Language(code, value)`; anything else raises `ValueError`, which `cli.main` also catches.
- **Tests isolate the machine**: `TestConfig.setUp` clears `os.environ` and patches `config.PROJECT_ROOT`; otherwise a real `.env` in the checkout satisfies a "missing credentials" assertion.

## Dependencies and Boundaries

Incoming: [CLI](cli.md) (`resolve_llm_config`, `langs.resolve`), [Translate](translate.md) (`LLMConfig`, `Language.name`), [Transcribe](transcribe.md) (`Language.code` via the CLI), and every module's error class. Outgoing: `ffmpegsrt/_vendor.py` for `PROJECT_ROOT`; standard library.

## Change Guide

| Change trigger | Inspect / extend | Required docs / checks |
| --- | --- | --- |
| New environment variable | `ENV_*`, the hints in `resolve_llm_config`, `.env.example`, `EPILOG` in `cli.py` | `test_missing_values_name_the_flag_and_the_env_var`; `README.md` Configure |
| New language or alias | The `_register` calls; order matters for shared codes | `TestLanguages` |
| New error type | Subclass `FfmpegSrtError` in the owning module | `cli.main` handles it automatically |
| Dotenv syntax | `load_dotenv` | `test_dotenv_parsing_and_precedence` |

## Verification

```sh
python3 test/test_units.py                                                     # TestConfig, TestLanguages ok
python3 -c "from ffmpegsrt import langs; print(langs.resolve('zh'))"           # Simplified Chinese (zh)
python3 -c "from ffmpegsrt.config import LLMConfig; print(repr(LLMConfig('u','sk-supersecret','m')))"   # shows sk-***et
```

Pass: exit 0 and the printed values. Live credential use is verified only by the end-to-end run.

## Known Gaps

- `.env` values are not validated; a base URL missing `/v1` fails at the first request.
- `known_aliases()` (`ffmpegsrt/langs.py:87`) is exported for help text and never called.
- The comment at `ffmpegsrt/config.py:46` names PyYAML as a dependency; it is not in `requirements.txt`. Comment drift only.
