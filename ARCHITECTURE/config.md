# Configuration: credentials, languages, errors

## Goal

The three small pieces the rest of the pipeline leans on: resolving endpoint
credentials without ever hardcoding or leaking one, normalising a language
argument into the two forms downstream stages need, and the error base that
lets the CLI print one clean line for every anticipated failure.
Infrastructure — it serves `M2` directly and every other milestone indirectly.

## Status

`done`. The `zh` alias collision that made `-t zh` mean Traditional Chinese is
fixed and covered by a regression test.

## Code Structure

| File | Role |
| ---- | ---- |
| `ffmpegsrt/config.py` | `.env` parsing, credential resolution, key redaction |
| `ffmpegsrt/langs.py` | Language alias table and resolution |
| `ffmpegsrt/errors.py` | `FfmpegSrtError`, the base every anticipated failure derives from |

## Key Types and Entry Points

- `ffmpegsrt/errors.py:12` — `FfmpegSrtError(RuntimeError)` — a missing ffmpeg,
  an unreadable file, an unset key, a model that will not load. `cli.main`
  catches it and prints one line; anything else surfaces as a traceback worth
  reporting.
- `ffmpegsrt/config.py:85` — `resolve_llm_config(...)` — precedence is
  **CLI flag > environment > `.env`**. Missing values are reported together,
  each naming both the flag and the environment variable that would supply it.
- `ffmpegsrt/config.py:38` — `load_dotenv(start)` — reads `.env.local` then
  `.env`, in the working directory and then the checkout root, nearest wins
  throughout. Deliberately minimal: no interpolation, no `export`, no
  multi-line values, so the dependency list stays at Whisper and PyYAML.
- `ffmpegsrt/config.py:29` — `redact(secret)` — `sk-abcdefghij` becomes
  `sk-***ij`.
- `ffmpegsrt/config.py:78` — `LLMConfig.__repr__` — redacts the key, so it
  cannot reach a log or a traceback.
- `ffmpegsrt/langs.py:15` — `Language(code, name)` — two consumers need two
  things from `-l jp`: Whisper wants the ISO 639-1 code, the translation prompt
  wants a name a model reads unambiguously ("Simplified Chinese", not "zh").
- `ffmpegsrt/langs.py:32` — `_register(code, name, *aliases)` — **first
  registration of a key wins**. Both Chinese entries claim the code `zh`;
  without this the later one took the bare alias and `-t zh` silently meant
  Traditional.
- `ffmpegsrt/langs.py:66` — `resolve(value)` — unknown two- and three-letter
  codes pass through rather than being rejected: Whisper's language list is
  longer than this table.

## Interactions

- [cli.md](cli.md) calls `resolve_llm_config` before transcription, so a typo
  fails in the first second rather than the fortieth minute, and `langs.resolve`
  for `-l` / `-t`.
- [translate.md](translate.md) consumes the `LLMConfig` and the `Language.name`.
- [transcribe.md](transcribe.md) consumes `Language.code`.
- `MediaError`, `TranscriptionError`, `TranslationError`, `ConfigError` and
  `KernessMissing` all derive from `FfmpegSrtError`.

## How to Test

```sh
python3 test/test_units.py    # pass = exit 0, "OK"
```

- `TestConfig` and `TestLanguages` cover this module. They patch out the
  environment **and** `config.PROJECT_ROOT`, because a real `.env` in the
  checkout would otherwise satisfy a test asserting that credentials are
  missing.
- The `zh` regression directly:
  `python3 -c "from ffmpegsrt import langs; print(langs.resolve('zh'))"`
  — pass = `Simplified Chinese (zh)`.
- Redaction:
  `python3 -c "from ffmpegsrt.config import LLMConfig; print(repr(LLMConfig('u','sk-supersecret','m')))"`
  — pass = the output does not contain `supersecret`.

## Open Gaps / Roadmap

- `--api-key` on the command line is visible in the process list. The help text
  says so, but nothing stops it.
- `known_aliases()` (`ffmpegsrt/langs.py:87`) is exported for help text and
  never called.
- Whisper transcribes both Chinese variants as `zh`; the Simplified/Traditional
  distinction only affects the translation target, not recognition.
- `.env` values are never validated — a base URL missing its `/v1` fails later,
  at the first request, rather than at resolution.
