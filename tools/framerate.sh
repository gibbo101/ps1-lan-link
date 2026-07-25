#!/usr/bin/env bash
# Objective presentation rate for a PCSX-Redux window: capture at 60 fps, then count how many
# frames are not duplicates of their predecessor. A window updating at full speed yields ~60
# distinct frames per second; a stalled emulator yields a handful.
#
# Only meaningful on a screen with motion (title/attract footage, intro movie, gameplay) --
# a genuinely static screen yields ~1 regardless of speed, so check what is on screen first.
#
#   ./framerate.sh <window-id> [seconds]
set -euo pipefail
export DISPLAY="${DISPLAY:-:1}"
WID="${1:?usage: framerate.sh <window-id> [seconds]}"
SECS="${2:-6}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

eval "$(xdotool getwindowgeometry --shell "$WID")"
ffmpeg -v error -f x11grab -framerate 60 -video_size "${WIDTH}x${HEIGHT}" \
  -i "$DISPLAY+$X,$Y" -t "$SECS" -c:v libx264 -preset ultrafast -y "$TMP/cap.mkv"

total=$(ffprobe -v error -select_streams v -count_frames \
  -show_entries stream=nb_read_frames -of csv=p=0 "$TMP/cap.mkv")
distinct=$(ffmpeg -i "$TMP/cap.mkv" -vf "mpdecimate=hi=200:lo=100:frac=0.02,showinfo" \
  -f null - 2>&1 | grep -c "pts_time" || true)

printf 'window %s (%sx%s): %s captured / %s distinct over %ss => %.1f distinct fps\n' \
  "$WID" "$WIDTH" "$HEIGHT" "$total" "$distinct" "$SECS" \
  "$(echo "scale=2; $distinct / $SECS" | bc)"
