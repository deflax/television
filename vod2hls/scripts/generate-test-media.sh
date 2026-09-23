#!/usr/bin/env bash
set -euo pipefail

output_dir="${1:-./sample-media}"
duration_seconds="${DURATION_SECONDS:-2}"

if ! command -v ffmpeg >/dev/null 2>&1; then
  printf 'ffmpeg is required to generate sample media\n' >&2
  exit 127
fi

mkdir -p "${output_dir}"

ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc2=size=1920x1080:rate=30:duration=${duration_seconds}" \
  -f lavfi -i "sine=frequency=1000:sample_rate=48000:duration=${duration_seconds}" \
  -c:v libx264 -pix_fmt yuv420p \
  -c:a aac -shortest \
  "${output_dir}/sample-1080p-with-audio.mp4"

ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc2=size=1280x720:rate=30:duration=${duration_seconds}" \
  -f lavfi -i "sine=frequency=660:sample_rate=48000:duration=${duration_seconds}" \
  -c:v libx264 -pix_fmt yuv420p \
  -c:a aac -shortest \
  "${output_dir}/sample-720p-with-audio.mkv"

ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc2=size=854x480:rate=30:duration=${duration_seconds}" \
  -c:v libx264 -pix_fmt yuv420p \
  -an \
  "${output_dir}/audio less sample video.mp4"

printf 'Generated sample media in %s\n' "${output_dir}"
