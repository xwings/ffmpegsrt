"""Command-line interface and pipeline orchestration."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

from ffmpegsrt import __version__, langs, media, srt as srtlib, transcribe
from ffmpegsrt import translate as translate_mod
from ffmpegsrt.config import resolve_llm_config
from ffmpegsrt.errors import FfmpegSrtError
from ffmpegsrt.srt import Cue

EPILOG = """\
examples:
  # Japanese film -> Chinese SRT, and burn the result in
  ffmpegsrt.py -i movie.mp4 -l jp -t zh_cn -s movie.srt -b -o movie_out.mp4

  # transcript only, no translation
  ffmpegsrt.py -i movie.mkv -l en -s movie.en.srt

  # burn in without keeping the SRT
  ffmpegsrt.py -i movie.mp4 -l jp -t zh_cn -b -o movie_out.mp4

  # re-burn an SRT you already have, skipping speech recognition
  ffmpegsrt.py -i movie.mp4 --srt-in movie.srt -b -o movie_out.mp4

Translation needs an OpenAI-compatible endpoint. Set FFMPEGSRT_API_BASE,
FFMPEGSRT_API_KEY and FFMPEGSRT_MODEL, or copy .env.example to .env.
"""


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    parser = argparse.ArgumentParser(
        prog="ffmpegsrt.py",
        description=(
            "Transcribe a movie's dialogue, translate it, and write an SRT "
            "and/or burn the subtitles into the video."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument("-i", "--input", required=True, metavar="FILE",
                        help="input movie file (mp4, mkv, ...)")
    parser.add_argument("-l", "--language", metavar="LANG",
                        help="spoken language of the movie, e.g. jp, en, ko "
                             "(default: auto-detect)")
    parser.add_argument("-t", "--translate", metavar="LANG",
                        help="translate subtitles into this language, "
                             "e.g. zh_cn, en")
    parser.add_argument("-s", "--srt", metavar="FILE",
                        help="write subtitles to this .srt file")
    parser.add_argument("-b", "--burn", action="store_true",
                        help="burn the subtitles into the video "
                             "(works without -s)")
    parser.add_argument("-o", "--output", metavar="FILE",
                        help="output video for -b "
                             "(default: <input>_out<ext>)")

    subs = parser.add_argument_group("subtitle options")
    subs.add_argument("--bilingual", action="store_true",
                      help="keep the source line above the translation")
    subs.add_argument("--srt-in", metavar="FILE",
                      help="use this existing SRT instead of transcribing")

    asr = parser.add_argument_group("speech recognition")
    asr.add_argument("--model", default="small", metavar="NAME",
                     help="whisper model: %s, or a local path "
                          "(default: small)" % ", ".join(transcribe.KNOWN_MODELS))
    asr.add_argument("--device", default="cpu", choices=("cpu", "cuda", "auto"),
                     help="inference device (default: cpu)")
    asr.add_argument("--compute-type", default=None, metavar="TYPE",
                     help="ctranslate2 compute type, e.g. int8, float16 "
                          "(default: int8 on cpu, float16 on gpu)")
    asr.add_argument("--beam-size", type=int, default=5, metavar="N",
                     help="decoding beam width (default: 5)")
    asr.add_argument("--no-vad", action="store_true",
                     help="disable voice-activity filtering")

    llm = parser.add_argument_group("translation endpoint")
    llm.add_argument("--api-base", metavar="URL",
                     help="OpenAI-compatible base URL "
                          "(env: FFMPEGSRT_API_BASE)")
    llm.add_argument("--api-key", metavar="KEY",
                     help="API key (env: FFMPEGSRT_API_KEY). Prefer the env "
                          "var or .env — argv is visible in the process list.")
    llm.add_argument("--llm-model", metavar="NAME",
                     help="model name (env: FFMPEGSRT_MODEL)")
    llm.add_argument("--batch-size", type=int, default=40, metavar="N",
                     help="cues per translation session (default: 40)")
    llm.add_argument("--llm-timeout", type=float, metavar="SEC",
                     default=translate_mod.DEFAULT_TIMEOUT_SEC,
                     help="per-request timeout for the endpoint (default: "
                          f"{translate_mod.DEFAULT_TIMEOUT_SEC:.0f}). "
                          "Lower it when the endpoint stalls instead of "
                          "answering.")
    llm.add_argument("--batch-timeout", type=float, metavar="SEC",
                     default=translate_mod.BATCH_BUDGET_SEC,
                     help="wall clock one batch may spend in the agent "
                          f"harness (default: {translate_mod.BATCH_BUDGET_SEC:.0f}). "
                          "Past it the batch falls back to a single direct "
                          "call rather than waiting on a failing endpoint.")

    enc = parser.add_argument_group("encoding")
    enc.add_argument("--start", type=float, metavar="SEC",
                     help="only process from this offset")
    enc.add_argument("--duration", type=float, metavar="SEC",
                     help="only process this many seconds")
    enc.add_argument("--font", default=media.DEFAULT_FONT, metavar="NAME",
                     help=f"subtitle font (default: {media.DEFAULT_FONT})")
    enc.add_argument("--font-size", type=int, default=20, metavar="N",
                     help="subtitle font size (default: 20)")
    enc.add_argument("--crf", type=int, default=20, metavar="N",
                     help="x264 quality, lower is better (default: 20)")
    enc.add_argument("--preset", default="medium", metavar="NAME",
                     help="x264 speed preset (default: medium)")

    misc = parser.add_argument_group("misc")
    misc.add_argument("--keep-temp", action="store_true",
                      help="keep intermediate audio and clips")
    misc.add_argument("-v", "--verbose", action="store_true",
                      help="show the translation agents' conversation")
    misc.add_argument("--version", action="version",
                      version=f"ffmpegsrt {__version__}")

    return parser


def _default_output(input_path: Path) -> Path:
    """Derive ``<name>_out<ext>`` for -b when -o was omitted."""
    return input_path.with_name(f"{input_path.stem}_out{input_path.suffix or '.mp4'}")


def _log(message: str = "") -> None:
    """Progress goes to stderr so stdout stays clean for piping."""
    print(message, file=sys.stderr, flush=True)


def _validate(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """Reject argument combinations that cannot produce output."""
    if not args.srt and not args.burn:
        parser.error(
            "nothing to produce: pass -s FILE to write an SRT, -b to burn "
            "subtitles into the video, or both."
        )
    if args.bilingual and not args.translate:
        parser.error("--bilingual needs a translation target (-t)")
    if args.srt_in and args.language:
        _log("note: -l is ignored when --srt-in is given")


def run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    """Execute the pipeline. Returns a process exit code."""
    _validate(args, parser)
    media.require_tools()

    source_lang = langs.resolve(args.language) if args.language else None
    target_lang = langs.resolve(args.translate) if args.translate else None

    # Resolve credentials before spending minutes on transcription, so a
    # missing key fails in the first second rather than the fortieth minute.
    llm_config = None
    if target_lang:
        llm_config = resolve_llm_config(
            args.api_base, args.api_key, args.llm_model, project_root=Path.cwd()
        )

    input_path = Path(args.input)
    info = media.probe(input_path)
    if info.audio is None:
        raise media.MediaError(f"{input_path} has no audio track to transcribe")

    _log(f"input    : {input_path.name}  "
         f"({info.duration:.1f}s, audio {info.audio.codec} "
         f"{info.audio.channels}ch @ {info.audio.sample_rate}Hz)")

    workdir = Path(tempfile.mkdtemp(prefix="ffmpegsrt-"))
    started = time.monotonic()
    try:
        # A slice is materialised once so transcription and burn-in share a
        # single timeline starting at zero.
        working_video = input_path
        if args.start or args.duration:
            _log(f"trimming : from {args.start or 0:.1f}s "
                 f"for {args.duration or info.duration:.1f}s")
            working_video = media.trim(
                input_path, workdir / f"clip{input_path.suffix or '.mp4'}",
                start=args.start, duration=args.duration,
            )

        cues = _get_cues(args, working_video, workdir, source_lang)

        if target_lang:
            # Checkpoint the transcript before the translation stage. Speech
            # recognition is the expensive half of a run and the endpoint is
            # the unreliable half; an interrupted translation must not cost
            # the transcript too. --srt-in reads it back.
            if args.srt:
                srtlib.write_srt(cues, Path(args.srt), mode="source")
                _log(f"subtitles: {args.srt}  (source transcript, "
                     "overwritten once translated)")
            _translate(args, cues, source_lang, target_lang, llm_config)

        mode = (
            "bilingual" if args.bilingual
            else "translated" if target_lang
            else "source"
        )
        srt_path = Path(args.srt) if args.srt else workdir / "subtitles.srt"
        srtlib.write_srt(cues, srt_path, mode=mode)
        if args.srt:
            _log(f"subtitles: {srt_path}  ({len(cues)} cues, {mode})")

        if args.burn:
            out_path = Path(args.output) if args.output else _default_output(input_path)
            _log(f"burn-in  : encoding {out_path} "
                 f"(x264 crf {args.crf}, preset {args.preset})")
            media.burn_in(
                working_video, srt_path, out_path,
                font=args.font, font_size=args.font_size,
                crf=args.crf, preset=args.preset,
            )
            size_mb = out_path.stat().st_size / 1_048_576
            _log(f"video    : {out_path}  ({size_mb:.1f} MB)")

        _log(f"done in {time.monotonic() - started:.1f}s")
        return 0
    finally:
        if args.keep_temp:
            _log(f"temp kept: {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


def _get_cues(
    args: argparse.Namespace,
    video: Path,
    workdir: Path,
    source_lang: langs.Language | None,
) -> list[Cue]:
    """Load cues from --srt-in, or transcribe the audio."""
    if args.srt_in:
        cues = srtlib.read_srt(args.srt_in)
        if not cues:
            raise media.MediaError(f"no cues found in {args.srt_in}")
        _log(f"subtitles: reusing {args.srt_in} ({len(cues)} cues)")
        return cues

    audio = media.extract_audio(video, workdir / "audio.wav")
    compute_type = args.compute_type or transcribe.default_compute_type(args.device)
    _log(f"asr      : whisper {args.model} on {args.device}/{compute_type}"
         f", language {source_lang.code if source_lang else 'auto'}")

    last_report = [0.0]

    def progress(cue: Cue, total: float) -> None:
        # Segments arrive lazily; report at most once a second so a long film
        # shows movement without flooding the terminal.
        now = time.monotonic()
        if now - last_report[0] < 1.0 and cue.end < total:
            return
        last_report[0] = now
        pct = (cue.end / total * 100) if total else 0
        _log(f"           {cue.end:7.1f}s / {total:.1f}s  ({pct:5.1f}%)")

    result = transcribe.transcribe(
        audio,
        language=source_lang.code if source_lang else None,
        model_size=args.model,
        device=args.device,
        compute_type=compute_type,
        beam_size=args.beam_size,
        vad_filter=not args.no_vad,
        on_progress=progress,
    )
    detected = ""
    if not source_lang:
        detected = f", detected {result.language} " \
                   f"({result.language_probability:.0%} confident)"
    _log(f"asr      : {len(result.cues)} cues{detected}")
    return result.cues


def _translate(
    args: argparse.Namespace,
    cues: list[Cue],
    source_lang: langs.Language | None,
    target_lang: langs.Language,
    llm_config,
) -> None:
    """Run the clawstick harness over the cues and report what happened."""
    batches = (len(cues) + args.batch_size - 1) // args.batch_size
    _log(f"translate: {len(cues)} cues -> {target_lang.name} "
         f"via clawstick ({batches} batches of {args.batch_size}, "
         f"model {llm_config.model})")

    translator = translate_mod.SubtitleTranslator(
        llm_config,
        batch_size=args.batch_size,
        verbose=args.verbose,
        timeout_sec=args.llm_timeout,
        batch_budget_sec=args.batch_timeout,
        on_note=lambda message: _log(f"           ! {message}"),
    )

    def on_batch(batch_no: int, batch_total: int, done: int) -> None:
        _log(f"           batch {batch_no}/{batch_total}  ({done}/{len(cues)} cues)")

    stats = translator.translate(cues, source_lang, target_lang, on_batch=on_batch)

    _log(f"translate: {stats.sessions_run} harness sessions")
    if stats.failed_requests:
        _log(f"           {stats.failed_requests} request(s) never came back "
             "from the endpoint")
    if stats.retried_batches:
        _log(f"           {stats.retried_batches} batch(es) needed a retry")
    if stats.fallback_batches:
        _log(f"           {stats.fallback_batches} batch(es) fell back to a "
             "direct call")
    if stats.failed_ranges:
        _log("warning  : kept source text for untranslated "
             f"{', '.join(stats.failed_ranges)}")
    if stats.glossary:
        _log(f"           glossary: {len(stats.glossary)} term(s)")


def main(argv: list[str] | None = None) -> int:
    """Entry point. Turns expected failures into a message, not a traceback."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return run(args, parser)
    except KeyboardInterrupt:
        _log("\ninterrupted")
        return 130
    except (FfmpegSrtError, ValueError) as exc:
        # ValueError is here for langs.resolve on an unknown language code.
        _log(f"error: {exc}")
        return 1
