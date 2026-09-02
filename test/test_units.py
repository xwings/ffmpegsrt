#!/usr/bin/env python3
"""Unit tests for the pure logic: no ffmpeg, no model, no API key.

Run either way::

    python3 test/test_units.py
    pytest test/test_units.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ffmpegsrt import cli, config, langs, media, sound, srt, translate  # noqa: E402
from ffmpegsrt.srt import Cue  # noqa: E402


class TestTimestamps(unittest.TestCase):
    def test_format(self):
        self.assertEqual(srt.format_timestamp(0), "00:00:00,000")
        self.assertEqual(srt.format_timestamp(1.5), "00:00:01,500")
        self.assertEqual(srt.format_timestamp(3661.234), "01:01:01,234")

    def test_negative_clamps_to_zero(self):
        self.assertEqual(srt.format_timestamp(-5), "00:00:00,000")

    def test_parse_pads_short_millis(self):
        # "1" in the millis field means .100, not .001.
        self.assertAlmostEqual(srt.parse_timestamp("0", "00", "01", "1"), 1.1)
        self.assertAlmostEqual(srt.parse_timestamp("1", "02", "03", "004"), 3723.004)

    def test_round_trip(self):
        for seconds in (0.0, 0.001, 12.345, 3599.999, 7200.5):
            text = srt.format_timestamp(seconds)
            hours, minutes, rest = text.split(":")
            secs, millis = rest.split(",")
            self.assertAlmostEqual(
                srt.parse_timestamp(hours, minutes, secs, millis), seconds, places=3
            )


class TestSrtIO(unittest.TestCase):
    def _round_trip(self, cues, mode="source"):
        with tempfile.TemporaryDirectory() as tmp:
            path = srt.write_srt(cues, Path(tmp) / "out.srt", mode=mode)
            return path.read_text(encoding="utf-8"), srt.read_srt(path)

    def test_write_then_read(self):
        cues = [Cue(0.0, 1.5, "First line"), Cue(2.0, 3.25, "Second line")]
        _, parsed = self._round_trip(cues)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0].text, "First line")
        self.assertAlmostEqual(parsed[1].end, 3.25, places=3)

    def test_empty_bodies_are_dropped_and_renumbered(self):
        cues = [Cue(0, 1, "kept"), Cue(1, 2, "   "), Cue(2, 3, "also kept")]
        text, parsed = self._round_trip(cues)
        self.assertEqual(len(parsed), 2)
        # Numbering must close over the hole, not skip 2.
        self.assertIn("1\n", text)
        self.assertIn("2\n", text)
        self.assertNotIn("3\n", text)

    def test_multi_line_body_survives(self):
        _, parsed = self._round_trip([Cue(0, 1, "top\nbottom")])
        self.assertEqual(parsed[0].text, "top\nbottom")

    def test_read_tolerates_missing_index_and_dot_millis(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "in.srt"
            path.write_text(
                "00:00:01.000 --> 00:00:02.000\nno index here\n", encoding="utf-8"
            )
            parsed = srt.read_srt(path)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].text, "no index here")

    def test_read_strips_bom(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bom.srt"
            path.write_text(
                "\ufeff1\n00:00:01,000 --> 00:00:02,000\nhello\n", encoding="utf-8"
            )
            self.assertEqual(srt.read_srt(path)[0].text, "hello")


class TestCueDisplay(unittest.TestCase):
    def test_source_mode_ignores_translation(self):
        cue = Cue(0, 1, "source", translated="translated")
        self.assertEqual(cue.display("source"), "source")

    def test_translated_and_bilingual(self):
        cue = Cue(0, 1, "source", translated="translated")
        self.assertEqual(cue.display("translated"), "translated")
        self.assertEqual(cue.display("bilingual"), "source\ntranslated")

    def test_untranslated_falls_back_to_source(self):
        cue = Cue(0, 1, "source")
        self.assertEqual(cue.display("translated"), "source")

    def test_sound_cue_is_bracketed_in_every_mode(self):
        cue = Cue(0, 1, "cry", translated="哭泣", sound=True)
        self.assertEqual(cue.display("source"), "[cry]")
        self.assertEqual(cue.display("translated"), "[哭泣]")
        self.assertEqual(cue.display("bilingual"), "[cry]\n[哭泣]")

    def test_untranslated_sound_cue_keeps_its_label(self):
        self.assertEqual(Cue(0, 1, "music", sound=True).display("translated"),
                         "[music]")


class TestShiftAndClip(unittest.TestCase):
    def test_shifts_onto_clip_timeline(self):
        cues = [Cue(100.0, 102.0, "a"), Cue(105.0, 107.0, "b")]
        shifted = srt.shift_and_clip(cues, start=100.0, duration=10.0)
        self.assertEqual(len(shifted), 2)
        self.assertAlmostEqual(shifted[0].start, 0.0)
        self.assertAlmostEqual(shifted[1].start, 5.0)

    def test_drops_cues_outside_the_window(self):
        cues = [Cue(0, 5, "before"), Cue(100, 102, "inside"), Cue(500, 502, "after")]
        shifted = srt.shift_and_clip(cues, start=99.0, duration=10.0)
        self.assertEqual([c.text for c in shifted], ["inside"])

    def test_straddling_cue_is_clamped_not_dropped(self):
        shifted = srt.shift_and_clip([Cue(8.0, 20.0, "long")], start=0.0, duration=10.0)
        self.assertEqual(len(shifted), 1)
        self.assertAlmostEqual(shifted[0].end, 10.0)

    def test_preserves_sound_flag_and_translation(self):
        cues = [Cue(10, 11, "cry", translated="哭泣", sound=True)]
        shifted = srt.shift_and_clip(cues, start=10.0)
        self.assertTrue(shifted[0].sound)
        self.assertEqual(shifted[0].translated, "哭泣")

    def test_no_args_is_a_copy(self):
        cues = [Cue(1, 2, "a")]
        self.assertEqual(len(srt.shift_and_clip(cues)), 1)


class TestSoundDetection(unittest.TestCase):
    def test_wrapped_markers(self):
        cases = {
            "[Music]": "Music",
            "(laughs)": "laughs",
            "[breathing]": "breathing",
            "(coughs)": "coughs",
            "*sobbing*": "sobbing",
            "（笑）": "笑",
            "【音楽】": "音楽",
            "  [ CRY ]  ": "CRY",
        }
        for text, expected in cases.items():
            self.assertEqual(sound.detect(text), expected, text)

    def test_musical_notes(self):
        self.assertEqual(sound.detect("♪♪"), "music")
        self.assertEqual(sound.detect("♪ la la ♪"), "la la")

    def test_dialogue_is_not_a_sound(self):
        for text in (
            "Hello there",
            "I can hear her breathing",
            "[coughs] Are you okay?",
            "[Music] I cannot wait",
            "",
            "[]",
            "(He said)hi",
        ):
            self.assertIsNone(sound.detect(text), text)

    def test_classify_marks_and_counts(self):
        cues = [Cue(0, 1, "[Music]"), Cue(1, 2, "Hello"), Cue(2, 3, "(laughs)")]
        self.assertEqual(sound.classify(cues), 2)
        self.assertEqual([c.sound for c in cues], [True, False, True])
        # The wrapper is stripped; display() puts it back.
        self.assertEqual(cues[0].text, "Music")
        self.assertEqual(cues[0].display("source"), "[Music]")
        self.assertEqual(cues[1].text, "Hello")

    def test_strip_brackets_unwraps_a_returned_label(self):
        self.assertEqual(sound.strip_brackets("[哭泣]"), "哭泣")
        self.assertEqual(sound.strip_brackets("哭泣"), "哭泣")
        self.assertEqual(sound.strip_brackets("  哭泣  "), "哭泣")

    def test_tagged_cue_survives_an_srt_round_trip(self):
        cues = [Cue(0, 1, "[Music]"), Cue(1, 2, "Hello")]
        sound.classify(cues)
        with tempfile.TemporaryDirectory() as tmp:
            path = srt.write_srt(cues, Path(tmp) / "o.srt", mode="source")
            reparsed = srt.read_srt(path)
        self.assertEqual(reparsed[0].text, "[Music]")
        # Re-classifying restores the flag on the automatic --srt-in path.
        self.assertEqual(sound.classify(reparsed), 1)
        self.assertTrue(reparsed[0].sound)


class TestParser(unittest.TestCase):
    def test_sound_tags_option_is_removed(self):
        parser = cli.build_parser()
        help_text = parser.format_help()
        self.assertNotIn("--sound-tags", help_text)
        self.assertIn("--no-vad", help_text)

        with mock.patch("sys.stderr"), self.assertRaises(SystemExit) as caught:
            parser.parse_args(["-i", "movie.mp4", "--sound-tags"])
        self.assertEqual(caught.exception.code, 2)


class TestGetCues(unittest.TestCase):
    def test_existing_srt_classifies_sound_markers_automatically(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = srt.write_srt(
                [
                    Cue(0, 1, "[breathing]"),
                    Cue(1, 2, "Are you okay?"),
                    Cue(2, 3, "(coughs)"),
                ],
                root / "input.srt",
                mode="source",
            )
            args = cli.build_parser().parse_args(
                [
                    "-i", "movie.mp4", "--srt-in", str(path),
                    "-s", str(root / "out.srt"),
                ]
            )
            with mock.patch.object(cli, "_log"):
                cues = cli._get_cues(args, Path("unused.mp4"), root, None)

        self.assertEqual([cue.sound for cue in cues], [True, False, True])
        self.assertEqual([cue.text for cue in cues],
                         ["breathing", "Are you okay?", "coughs"])

    def test_transcription_classifies_sound_markers_automatically(self):
        args = cli.build_parser().parse_args(
            ["-i", "movie.mp4", "-s", "out.srt"]
        )
        result = cli.transcribe.Transcript(
            cues=[
                Cue(0, 1, "(coughs)"),
                Cue(1, 2, "I am coughing"),
                Cue(2, 3, "[breathing]"),
            ],
            language="en",
            language_probability=0.99,
            duration=3.0,
        )
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(
                cli.media, "extract_audio", return_value=Path("audio.wav")
            ),
            mock.patch.object(
                cli.transcribe, "transcribe", return_value=result
            ) as transcribe,
            mock.patch.object(cli, "_log"),
        ):
            cues = cli._get_cues(args, Path("movie.mp4"), Path(tmp), None)

        self.assertEqual([cue.sound for cue in cues], [True, False, True])
        self.assertEqual([cue.text for cue in cues],
                         ["coughs", "I am coughing", "breathing"])
        self.assertTrue(transcribe.call_args.kwargs["vad_filter"])


class TestLanguages(unittest.TestCase):
    def test_bare_zh_is_simplified(self):
        # Regression: both Chinese entries register the code "zh", and the
        # later one used to win, silently making `-t zh` mean Traditional.
        self.assertEqual(langs.resolve("zh").name, "Simplified Chinese")

    def test_chinese_variants(self):
        self.assertEqual(langs.resolve("zh_cn").name, "Simplified Chinese")
        self.assertEqual(langs.resolve("zh-tw").name, "Traditional Chinese")
        self.assertEqual(langs.resolve("cht").name, "Traditional Chinese")

    def test_aliases_and_case(self):
        self.assertEqual(langs.resolve("JP").code, "ja")
        self.assertEqual(langs.resolve("  Korean ").code, "ko")

    def test_unknown_short_code_passes_through(self):
        # Whisper's language list is longer than the table; do not block it.
        self.assertEqual(langs.resolve("sv").code, "sv")

    def test_nonsense_is_rejected(self):
        with self.assertRaises(ValueError):
            langs.resolve("klingon!!")


class TestConfig(unittest.TestCase):
    def setUp(self):
        # Credential resolution reads the environment and both the working
        # directory and the checkout root, and this checkout has a real .env.
        # Cut every one of those off so the tests measure the code, not the
        # machine they run on.
        empty = Path(tempfile.mkdtemp(prefix="ffmpegsrt-test-"))
        patches = [
            mock.patch.dict(os.environ, {}, clear=True),
            mock.patch.object(config, "PROJECT_ROOT", empty),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(empty.rmdir)
        self.empty = empty

    def test_redact(self):
        self.assertEqual(config.redact(None), "<unset>")
        self.assertEqual(config.redact("short"), "***")
        self.assertEqual(config.redact("sk-abcdefghij"), "sk-***ij")

    def test_dotenv_parsing_and_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text(
                "# comment\n\nFFMPEGSRT_MODEL='from-env'\nBARE\n", encoding="utf-8"
            )
            (root / ".env.local").write_text(
                'FFMPEGSRT_MODEL="from-local"\n', encoding="utf-8"
            )
            values = config.load_dotenv(root)
        self.assertEqual(values["FFMPEGSRT_MODEL"], "from-local")
        self.assertNotIn("BARE", values)

    def test_explicit_args_win_and_are_stripped(self):
        resolved = config.resolve_llm_config(
            " https://x/v1 ", " key ", " model ", project_root=self.empty
        )
        self.assertEqual(resolved.api_base, "https://x/v1")
        self.assertEqual(resolved.model, "model")

    def test_checkout_root_is_consulted_when_cwd_has_no_env(self):
        # Running from wherever the movie lives must still find the .env that
        # sits next to .env.example in the checkout.
        (self.empty / ".env").write_text(
            "FFMPEGSRT_API_BASE=https://root/v1\n"
            "FFMPEGSRT_API_KEY=k\nFFMPEGSRT_MODEL=m\n",
            encoding="utf-8",
        )
        self.addCleanup((self.empty / ".env").unlink)
        with tempfile.TemporaryDirectory() as elsewhere:
            resolved = config.resolve_llm_config(project_root=Path(elsewhere))
        self.assertEqual(resolved.api_base, "https://root/v1")

    def test_repr_does_not_leak_the_key(self):
        cfg = config.LLMConfig("https://x/v1", "sk-supersecretvalue", "m")
        self.assertNotIn("supersecret", repr(cfg))

    def test_missing_values_name_the_flag_and_the_env_var(self):
        with self.assertRaises(config.ConfigError) as caught:
            config.resolve_llm_config("https://x/v1", "", "m",
                                      project_root=self.empty)
        message = str(caught.exception)
        self.assertIn("--api-key", message)
        self.assertIn("FFMPEGSRT_API_KEY", message)


class TestCoerceLines(unittest.TestCase):
    def test_plain_list(self):
        self.assertEqual(translate._coerce_lines(["a", "b"], 2), ["a", "b"])

    def test_wrong_length_is_rejected(self):
        # A short list would desync every following subtitle, so it must not
        # be padded or accepted.
        self.assertIsNone(translate._coerce_lines(["a"], 2))
        self.assertIsNone(translate._coerce_lines(["a", "b", "c"], 2))

    def test_non_list_is_rejected(self):
        self.assertIsNone(translate._coerce_lines("a\nb", 2))
        self.assertIsNone(translate._coerce_lines(None, 2))

    def test_self_numbered_output_is_stripped(self):
        self.assertEqual(translate._coerce_lines(["1. one", "2) two"], 2),
                         ["one", "two"])

    def test_dict_shapes(self):
        raw = [{"text": "one"}, {"translation": "two"}, {"3": "three"}]
        self.assertEqual(translate._coerce_lines(raw, 3), ["one", "two", "three"])

    def test_numbered_renders_sound_cues_bracketed(self):
        cues = [
            Cue(0, 1, "I am coughing"),
            Cue(1, 2, "coughs", sound=True),
            Cue(2, 3, "breathing", sound=True),
        ]
        self.assertEqual(
            translate._numbered(cues),
            "1. I am coughing\n2. [coughs]\n3. [breathing]",
        )


class TestMediaHelpers(unittest.TestCase):
    def test_escape_filter_path(self):
        escaped = media.escape_filter_path(r"C:\clips\a'b.srt")
        self.assertNotIn(":", escaped.replace(r"\:", ""))
        self.assertIn(r"\\", escaped)
        self.assertIn(r"\'", escaped)

    def test_force_style_carries_font_and_size(self):
        style = media.build_force_style("My Font", 33)
        self.assertIn("FontName=My Font", style)
        self.assertIn("FontSize=33", style)
        self.assertIn("Alignment=2", style)

    def test_default_compute_type(self):
        from ffmpegsrt import transcribe
        self.assertEqual(transcribe.default_compute_type("cpu"), "int8")
        self.assertEqual(transcribe.default_compute_type("cuda"), "float16")

    def _capture_ffmpeg(self, call):
        """Run *call* with ``media._run`` stubbed, returning the ffmpeg argv."""
        seen = []

        def fake_run(cmd, what):
            seen.append(cmd)
            Path(cmd[-1]).write_bytes(b"x")   # the callers check the output exists

        with mock.patch.object(media, "_run", fake_run):
            call()
        return seen[0]

    def test_video_is_encoded_as_10_bit_hevc(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "in.srt").write_text("")
            commands = {
                "trim": self._capture_ffmpeg(
                    lambda: media.trim(tmp / "in.mp4", tmp / "clip.mp4", duration=1)
                ),
                "burn_in": self._capture_ffmpeg(
                    lambda: media.burn_in(
                        tmp / "in.mp4", tmp / "in.srt", tmp / "out.mp4"
                    )
                ),
            }

        for what, cmd in commands.items():
            with self.subTest(what):
                self.assertNotIn("libx264", cmd)
                self.assertEqual(cmd[cmd.index("-c:v") + 1], "libx265")
                self.assertEqual(cmd[cmd.index("-pix_fmt") + 1], "yuv420p10le")
                self.assertEqual(cmd[cmd.index("-tag:v") + 1], "hvc1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
