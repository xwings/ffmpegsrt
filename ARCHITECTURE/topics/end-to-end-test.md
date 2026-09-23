---
eatmycode_version: "2.1.0"
---
# End-to-end test

Owner: [CLI](../modules/cli.md)

Read when: changing `test/run_test.sh` or `test/make_sample.sh`, or verifying a change against real media and a live translation endpoint.

## Contract

The shell pair drives the whole pipeline over user-supplied media and asserts on both outputs. It costs minutes of CPU, a Whisper model download and real API calls, which is why it is separate from `test/test_units.py`.

- `test/make_sample.sh MOVIE [SECONDS=30] [START]` writes `test/sample.mp4`. `START` defaults to a third of the way in when the film is longer than 90 s (`test/make_sample.sh:27`), because opening credits are usually silent. It re-encodes with the same libx265 flags as `media.trim` (`test/make_sample.sh:38`) so the cut lands exactly on `START`.
- `test/run_test.sh [SRC=ja] [DST=zh_cn]` runs `ffmpegsrt.py -i sample -l SRC -t DST -s out/sample.srt -b -o out/sample_out.mp4`, then asserts (`test/run_test.sh:44-109`):
  - the SRT parses with `srt.read_srt` and has at least one cue;
  - every cue ends after it starts, starts no earlier than the previous end, and ends within 1 s of the sample duration;
  - at least 5 characters fall in the target script for `zh`, `ja` or `ko` targets, because a still-ASCII file means translation fell through to source text; other targets print a skip notice;
  - the burned-in video has both `audio` and `video` streams and a duration within 1 s of the sample.
- Pass evidence: exit 0 and a final `PASS` line naming the SRT and video. `*.srt`, `*.wav` and `tmp/` are gitignored; `test/sample.mp4` and `test/out/` are not, so do not commit them.

## Change and Verify

- Changing encoder flags in `media.trim` or `media.burn_in` requires the same change at `test/make_sample.sh:40`; see [Media](../modules/media.md).
- Adding a target language to the script check means extending the `ranges` dict at `test/run_test.sh:74`.
- Syntax without running: `bash -n test/make_sample.sh test/run_test.sh`, exit 0.
- Full run from the repo root: `./test/make_sample.sh path/to/movie.mp4 30 && ./test/run_test.sh ja zh_cn`. Needs ffmpeg with libx265 and libass, `pip install -r requirements.txt`, kerness built, a `.env` with credentials, and a movie.

## Evidence and Gaps

- Not run for this doc refresh: no media, faster-whisper or kerness on this machine. The synthetic no-network path in [CLI](../modules/cli.md#verification) and the burn-in check in [Media](../modules/media.md#verification) were run instead.
- No dry-run mode; every run spends API calls.
- The script check knows only Chinese, Japanese and Korean ranges.
- No CI runs either tier.
