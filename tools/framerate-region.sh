#!/usr/bin/env bash
# Distinct-frame rate for an arbitrary screen region, for when two emulator windows overlap and
# only a strip of each is unobscured. Same metric as framerate.sh: frames that differ from their
# predecessor, so ~60/s means full speed and a handful means the emulator is stalling.
#
#   ./framerate-region.sh <WxH> <X> <Y> [seconds] [label]
set -euo pipefail
export DISPLAY="${DISPLAY:-:1}"
SIZE="${1:?usage: framerate-region.sh <WxH> <X> <Y> [seconds] [label]}"
X="${2:?}"; Y="${3:?}"; SECS="${4:-6}"; LABEL="${5:-region}"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

ffmpeg -v error -f x11grab -framerate 60 -video_size "$SIZE" -i "$DISPLAY+$X,$Y" \
  -t "$SECS" -c:v libx264 -preset ultrafast -y "$TMP/c.mkv"
total=$(ffprobe -v error -select_streams v -count_frames -show_entries stream=nb_read_frames \
  -of csv=p=0 "$TMP/c.mkv")
distinct=$(ffmpeg -i "$TMP/c.mkv" -vf "mpdecimate=hi=200:lo=100:frac=0.02,showinfo" -f null - 2>&1 \
  | grep -c "pts_time" || true)
printf '%-10s %s@%s,%s: %s captured / %s distinct over %ss => %s distinct fps\n' \
  "$LABEL" "$SIZE" "$X" "$Y" "$total" "$distinct" "$SECS" \
  "$(echo "scale=2; $distinct / $SECS" | bc)"
