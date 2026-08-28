#!/usr/bin/env bash
# Cut a short sample out of a movie so the end-to-end test runs in minutes
# rather than hours.
#
#     ./test/make_sample.sh path/to/movie.mp4 [seconds] [start]
#
# Writes test/sample.mp4, which run_test.sh picks up. Test media is gitignored.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
src="${1:-}"
seconds="${2:-30}"
start="${3:-}"

if [[ -z "$src" ]]; then
    echo "usage: $0 SOURCE_MOVIE [SECONDS] [START_SECONDS]" >&2
    exit 2
fi
if [[ ! -f "$src" ]]; then
    echo "error: no such file: $src" >&2
    exit 1
fi
command -v ffmpeg >/dev/null || { echo "error: ffmpeg not on PATH" >&2; exit 1; }

# Default to a third of the way in: opening credits are usually silent, and a
# sample with no dialogue tells you nothing about transcription.
if [[ -z "$start" ]]; then
    duration=$(ffprobe -v error -show_entries format=duration \
        -of default=nw=1:nk=1 "$src" 2>/dev/null || echo 0)
    start=$(awk -v d="$duration" 'BEGIN { printf "%.0f", (d > 90) ? d / 3 : 0 }')
fi

dest="$here/sample.mp4"
echo "cutting ${seconds}s from ${start}s of $(basename "$src") -> $(basename "$dest")"

# Re-encode rather than -c copy: a stream copy can only cut on a keyframe,
# which drifts the start by up to a GOP and desyncs the timings the test checks.
ffmpeg -y -v error -stats \
    -ss "$start" -i "$src" -t "$seconds" \
    -c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p \
    -c:a aac \
    "$dest"

[[ -s "$dest" ]] || { echo "error: produced no output" >&2; exit 1; }
echo "ok: $dest ($(du -h "$dest" | cut -f1))"
