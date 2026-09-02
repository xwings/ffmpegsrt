# ffmpegsrt

Transcribe a movie's dialogue, translate it into another language, and write an
SRT file and/or burn the subtitles into the video.

```bash
python3 ffmpegsrt.py -i movie.mp4 -l jp -t zh_cn -s movie.srt -b -o movie_out.mp4
```

Speech recognition runs locally with [faster-whisper](https://github.com/SYSTRAN/faster-whisper).
Translation runs through a [kerness](https://github.com/xwings/kerness) multi-agent
harness — a translator drafts each batch, a reviewer attacks the draft for accuracy and
readability, and an editor issues the final line-for-line result. ffmpeg does the
demuxing and the burn-in.

MIT licensed.

## Install

```bash
git clone --recursive https://github.com/xwings/ffmpegsrt
cd ffmpegsrt

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install ./vendor/kerness/bindings/python
```

Already cloned without `--recursive`? `git submodule update --init --recursive`.

Kerness is Rust, so that last line compiles an extension module and needs a Rust
toolchain (rustc 1.88+) from [rustup.rs](https://rustup.rs). It is only needed for
translation — transcribing and burning in an existing SRT do not import it.

You also need `ffmpeg` and `ffprobe` on your PATH:

```bash
sudo apt install ffmpeg      # Debian/Ubuntu
brew install ffmpeg          # macOS
```

For burned-in CJK subtitles you need a font that covers the glyphs. On Debian and
Ubuntu, `fonts-droid-fallback` provides the default; otherwise pass `--font` with
something you have installed.

### GPU (optional)

To run speech recognition on an NVIDIA GPU with `--device cuda`, install the CUDA 12
runtime libraries. These are **separate from the driver**, and CTranslate2 will not
find them otherwise:

```bash
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

ffmpegsrt loads them out of the pip wheels automatically — no `LD_LIBRARY_PATH`
needed. Without them you get `Library libcublas.so.12 is not found or cannot be
loaded`, which surfaces partway into transcription rather than at startup.

## Configure

Translation talks to any OpenAI-compatible `chat/completions` endpoint. Nothing is
hardcoded — copy the example and fill in your own:

```bash
cp .env.example .env
$EDITOR .env
```

```ini
FFMPEGSRT_API_BASE=https://api.openai.com/v1
FFMPEGSRT_API_KEY=sk-your-key-here
FFMPEGSRT_MODEL=gpt-4o-mini
```

`.env` is gitignored. You can use environment variables or `--api-base` / `--api-key` /
`--llm-model` instead, though a key on the command line is visible to anyone who can
read the process list. Credentials are checked before transcription starts, so a typo
fails in the first second rather than the fortieth minute.

Transcription alone needs no credentials at all — drop `-t` and no network calls happen.

## Usage

```
ffmpegsrt.py -i FILE [-l LANG] [-t LANG] [-s FILE] [-b] [-o FILE]
```

| Flag | Meaning |
|---|---|
| `-i, --input` | Input movie (mp4, mkv, anything ffmpeg reads) |
| `-l, --language` | Spoken language, e.g. `jp`, `en`, `ko`. Auto-detected if omitted |
| `-t, --translate` | Translate into this language, e.g. `zh_cn`, `en` |
| `-s, --srt` | Write subtitles to this `.srt` |
| `-b, --burn` | Burn subtitles into the video (works without `-s`) |
| `-o, --output` | Output video for `-b` (default `<input>_out.mp4`) |

At least one of `-s` or `-b` is required — otherwise there is nothing to produce.

### Examples

```bash
# Japanese film -> Chinese subtitles, both an SRT and a burned-in copy
python3 ffmpegsrt.py -i movie.mp4 -l jp -t zh_cn -s movie.srt -b -o movie_out.mp4

# Transcript only, no translation, no network
python3 ffmpegsrt.py -i movie.mkv -l en -s movie.en.srt

# Burn in without keeping the SRT
python3 ffmpegsrt.py -i movie.mp4 -l jp -t zh_cn -b -o movie_out.mp4

# Source line above the translation
python3 ffmpegsrt.py -i movie.mp4 -l jp -t zh_cn --bilingual -s movie.srt

# Sound labels become action tags automatically: (coughs) becomes [咳嗽]
python3 ffmpegsrt.py -i movie.mp4 -l jp -t zh_cn -s movie.srt

# Re-burn an SRT you already have — skips speech recognition entirely
python3 ffmpegsrt.py -i movie.mp4 --srt-in movie.srt -b -o movie_out.mp4

# Try the pipeline on the first two minutes before committing to a full run
python3 ffmpegsrt.py -i movie.mp4 -l jp -t zh_cn --duration 120 -s sample.srt
```

### Other options

**Speech recognition** — `--model` (default `small`; also `tiny`, `base`, `medium`,
`large-v3`, or a local path), `--device cpu|cuda|auto`, `--compute-type`, `--beam-size`,
`--no-vad`.

`--compute-type` defaults to `int8` on CPU and `float16` on GPU — on a GPU there is no
reason to pay int8's accuracy cost.

`small` on CPU runs at roughly real time. On a GPU you can afford `large-v3`, which is
markedly more accurate on Japanese.

**Translation** — `--batch-size` (cues per harness session, default 40), `--llm-timeout`
(per-request timeout, default 60s), `--batch-timeout` (wall clock per batch in the
harness, default 300s), `-v` to watch the agents argue.

A flaky endpoint is the usual reason a run appears to hang: every request that stalls or
comes back empty is retried, and a batch can spend its whole budget on turns that never
land. Failed requests are printed as they happen, and a batch that burns through
`--batch-timeout` drops to a single direct call instead of waiting on a second session.
Translating a feature film is a long job even when the endpoint is healthy — pass
`-s out.srt` and the transcript is written before translation starts, so an interrupted
run can be resumed with `--srt-in out.srt` without transcribing again.

**Sound events** — non-speech that Whisper or an input SRT labels as a whole cue is
automatically rendered as an action tag: `[breathing]`, `[coughs]`, `[music]`. The tag
is translated by the configured LLM like any other cue, so a Chinese track can get
`[咳嗽]`. Nothing is invented — bare text remains dialogue, even if it mentions a
sound. Voice-activity filtering can remove non-speech before Whisper decodes it; use
`--no-vad` to expose more of the audio when needed, accepting that Whisper may then
hallucinate dialogue over music or room tone.

**Encoding** — burn-in writes 10-bit HEVC (`libx265`, `yuv420p10le`, tagged `hvc1`
so Apple players accept it). `--start` / `--duration` to process a slice, `--font`,
`--font-size`, `--crf`, `--preset`, `--keep-temp`.

## How it works

```
input.mp4
   │
   ├─ ffmpeg ──────────────► mono 16 kHz PCM
   │                              │
   │                        faster-whisper ──► timed cues in the source language
   │                                                │
   │                        kerness session per batch of 40 cues:
   │                          Translator drafts → Reviewer objects → Editor finalises
   │                                                │
   │                                          translated cues
   │                                                │
   │                                          ┌─────┴─────┐
   └─ ffmpeg + libass ◄───────────────────────┤  movie.srt │
              │                               └───────────┘
        movie_out.mp4
```

The gameplan lives in [`harness/subtitle_translate.md`](harness/subtitle_translate.md) —
YAML frontmatter declares the agents, phases and result shape; the Markdown body is the
editor's instructions. Edit it to change how translation behaves; no Python changes
needed.

A few things worth knowing:

- **Cue counts are checked.** The harness must return exactly one line per source cue.
  Kerness returns type defaults rather than raising on a malformed result block, so a
  short list would silently desync every following subtitle. A wrong count triggers a
  retry, then a direct single-call fallback, and only then gives up on that batch —
  keeping its source text so the run still finishes.
- **A glossary carries across batches.** Names and recurring terms agreed in one batch
  are handed to the next, along with the last three cues, so characters do not get
  renamed halfway through the film.
- **Slices are cut once, up front.** `--start`/`--duration` materialise a clip before
  transcription, so cue timings and the burn-in always share one timeline. Cues coming
  from `--srt-in` are shifted onto that clip's timeline and clipped to it, so re-burning
  a slice of an SRT you already have stays in sync.
- **Whisper's context carry-over is disabled.** It is the usual cause of a line
  repeating for minutes on a long feature.

## Testing

```bash
# unit tests — no ffmpeg, no model, no API key
python3 test/test_units.py          # or: pytest test/test_units.py

# end-to-end: cut a 30s sample, then transcribe -> translate -> burn in
./test/make_sample.sh path/to/movie.mp4 30
./test/run_test.sh
```

`run_test.sh` checks that the SRT parses, that cue timings are monotonic and inside the
sample, that the text is actually in the target script, and that the output video kept
both its streams.

Test media is gitignored — bring your own file.

## License

MIT. See [LICENSE](LICENSE).

Kerness is vendored as a submodule and is also MIT licensed. faster-whisper (MIT) and
ffmpeg (LGPL/GPL depending on build) are separate dependencies under their own terms.
