#!/usr/bin/env bash
# Window driving for the two linked instances: enumerate, screenshot, send keys.
# Every keypress is meant to be followed by a screenshot and a decision on what is actually
# on screen — the menu has an idle timeout, so firing a fixed sequence blind is unreliable.
#
#   ./drive.sh wins                 → "<id> <x> <y> <w> <h>" per instance, left first
#   ./drive.sh shot <id> <out.png>  → capture just that window
#   ./drive.sh key  <id> <key>      → send one key to that window (focus not required)
set -euo pipefail

DISP="${DISPLAY:-:1}"
export DISPLAY="$DISP"

wins() {
  local id x y w h
  for id in $(xdotool search --name "PCSX-Redux" 2>/dev/null); do
    eval "$(xdotool getwindowgeometry --shell "$id" 2>/dev/null)" || continue
    # Real instance windows are full-size; the search also matches tiny helper windows.
    [ "${WIDTH:-0}" -gt 400 ] && [ "${HEIGHT:-0}" -gt 400 ] || continue
    echo "$id $X $Y $WIDTH $HEIGHT"
  done | sort -k2 -n
}

case "${1:-}" in
  wins) wins ;;
  shot)
    id="$2"; out="$3"
    eval "$(xdotool getwindowgeometry --shell "$id")"
    # x11grab refuses any capture area extending past the screen, and a window manager is free to
    # place a window partly off it; capture the on-screen part.
    read -r SW SH < <(xdpyinfo | awk '/dimensions:/ {split($2, d, "x"); print d[1], d[2]}')
    [ "$X" -lt 0 ] && { WIDTH=$((WIDTH + X)); X=0; }
    [ "$Y" -lt 0 ] && { HEIGHT=$((HEIGHT + Y)); Y=0; }
    [ $((X + WIDTH)) -gt "$SW" ] && WIDTH=$((SW - X))
    [ $((Y + HEIGHT)) -gt "$SH" ] && HEIGHT=$((SH - Y))
    ffmpeg -v error -f x11grab -video_size "${WIDTH}x${HEIGHT}" -i "$DISP+$X,$Y" -frames:v 1 "$out" -y
    ;;
  key)
    id="$2"; shift 2
    for k in "$@"; do xdotool key --window "$id" "$k"; sleep 0.15; done
    ;;
  *) echo "usage: $0 {wins|shot <id> <out.png>|key <id> <key...>}" >&2; exit 1 ;;
esac
