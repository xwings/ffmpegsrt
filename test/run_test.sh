#!/usr/bin/env bash
# End-to-end test: transcribe -> translate -> burn in, over test/sample.mp4.
#
#     ./test/make_sample.sh path/to/movie.mp4 30
#     ./test/run_test.sh [SOURCE_LANG] [TARGET_LANG]
#
# Needs ffmpeg, the whisper model, and translation credentials (.env). Unlike
# test_units.py this one costs time and API calls.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
root="$(dirname "$here")"
sample="$here/sample.mp4"
out="$here/out"
src_lang="${1:-ja}"
dst_lang="${2:-zh_cn}"

fail() { echo "FAIL: $*" >&2; exit 1; }
pass() { echo "  ok: $*"; }

[[ -f "$sample" ]] || fail "no $sample — run ./test/make_sample.sh MOVIE first"
command -v ffprobe >/dev/null || fail "ffprobe not on PATH"

mkdir -p "$out"
srt="$out/sample.srt"
video="$out/sample_out.mp4"
rm -f "$srt" "$video"

echo "== running the pipeline (${src_lang} -> ${dst_lang}, with sound tags) =="
python3 "$root/ffmpegsrt.py" \
    -i "$sample" -l "$src_lang" -t "$dst_lang" \
    --sound-tags -s "$srt" -b -o "$video" \
    || fail "pipeline exited non-zero"

echo "== checking the SRT =="
[[ -s "$srt" ]] || fail "no SRT was written"

sample_duration=$(ffprobe -v error -show_entries format=duration \
    -of default=nw=1:nk=1 "$sample")

# Parse with the library that wrote it, then assert on the result: cues exist,
# timings are monotonic and inside the sample, and the text is in the target
# script rather than a passthrough of the source.
python3 - "$srt" "$sample_duration" "$dst_lang" <<'PY' || fail "SRT checks failed"
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path.cwd()))
from ffmpegsrt import srt as srtlib

path, duration, target = sys.argv[1], float(sys.argv[2]), sys.argv[3]
cues = srtlib.read_srt(path)

if not cues:
    sys.exit("  no cues parsed out of the SRT")
print(f"  ok: {len(cues)} cues parsed")

prev_end = -1.0
for i, cue in enumerate(cues, 1):
    if cue.start > cue.end:
        sys.exit(f"  cue {i} ends before it starts: {cue.start} > {cue.end}")
    if cue.start < prev_end - 0.001:
        sys.exit(f"  cue {i} starts before cue {i-1} ended")
    # One second of slack: the last cue can run just past the cut.
    if cue.end > duration + 1.0:
        sys.exit(f"  cue {i} ends at {cue.end:.1f}s, past the {duration:.1f}s sample")
    prev_end = cue.end
print("  ok: timings are monotonic and inside the sample")

# Target-script check. A translated file that is still all ASCII means the
# translation stage silently fell through to the source text.
text = "".join(c.text for c in cues)
ranges = {
    "zh": ((0x4E00, 0x9FFF),),
    "ja": ((0x3040, 0x30FF), (0x4E00, 0x9FFF)),
    "ko": ((0xAC00, 0xD7AF),),
}
prefix = target.split("_")[0].lower()
if prefix in ranges:
    hits = sum(
        1 for ch in text
        if any(lo <= ord(ch) <= hi for lo, hi in ranges[prefix])
    )
    if hits < 5:
        sys.exit(f"  only {hits} {prefix} characters — did translation run?")
    print(f"  ok: text is in the {prefix} script ({hits} characters)")
else:
    print(f"  skip: no script check for {target}")

tags = [c for c in cues if c.text.startswith("[") and c.text.endswith("]")]
print(f"  ok: {len(tags)} sound tag(s) in the output")
PY

echo "== checking the burned-in video =="
[[ -s "$video" ]] || fail "no output video was written"

streams=$(ffprobe -v error -show_entries stream=codec_type \
    -of default=nw=1:nk=1 "$video" | sort -u | tr '\n' ' ')
[[ "$streams" == *audio* ]] || fail "output lost its audio stream (got: $streams)"
[[ "$streams" == *video* ]] || fail "output lost its video stream (got: $streams)"
pass "output kept both streams ($streams)"

out_duration=$(ffprobe -v error -show_entries format=duration \
    -of default=nw=1:nk=1 "$video")
awk -v a="$sample_duration" -v b="$out_duration" \
    'BEGIN { exit (b < a - 1.0 || b > a + 1.0) }' \
    || fail "output is ${out_duration}s, sample is ${sample_duration}s"
pass "output duration matches the sample (${out_duration}s)"

echo
echo "PASS — $srt and $video"
