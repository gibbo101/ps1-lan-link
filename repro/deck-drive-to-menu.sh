#!/usr/bin/env bash
# Drive one Deck-hosted instance from the attract loop to the main menu.
#
# Start at the title begins the intro, and Start during the intro returns to the title, so a fixed
# delay just oscillates. Start must land on the black transition at the end of the movie. This polls
# the screen, presses only when it is black, and stops the moment the log shows the game has begun
# polling SIO_STAT (stalls > 0), which only happens from the main menu onward.
#
# Usage: deck-drive-to-menu.sh <window-id> <instA|instB> <window-x> [attempts]
set -uo pipefail

WIN="$1"; INST="$2"; WX="$3"; ATTEMPTS="${4:-40}"
LOG="$HOME/ps1-lan-link/dual/$INST/run.log"
SHOT=/tmp/drive-$INST.png
export DISPLAY=:0 XAUTHORITY=/run/user/1000/xauth_rDAMDB
export XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0

at_menu() {
  local s
  s=$(grep -a SPEED "$LOG" | tail -1 | sed -n 's/.*stalls=\([0-9]*\).*/\1/p')
  [ -n "${s:-}" ] && [ "$s" -gt 0 ]
}

for n in $(seq 1 "$ATTEMPTS"); do
  if at_menu; then echo "[$INST] main menu reached after $((n-1)) presses"; exit 0; fi

  spectacle -b -n -f -o "$SHOT" >/dev/null 2>&1
  sleep 1
  # Mean luma of just this instance's window; the black transition reads near zero.
  yavg=$(ffmpeg -v error -i "$SHOT" \
      -vf "crop=630:450:$((WX+4)):32,signalstats,metadata=print:key=lavfi.signalstats.YAVG" \
      -f null - 2>&1 | sed -n 's/.*YAVG=\([0-9.]*\).*/\1/p' | head -1)
  yavg=${yavg:-999}

  if awk "BEGIN{exit !($yavg < 20)}"; then
    xdotool windowactivate "$WIN" 2>/dev/null
    sleep 0.3
    xdotool key --clearmodifiers Return
    echo "[$INST] black (YAVG=$yavg) -> Start"
    sleep 4
  else
    echo "[$INST] YAVG=$yavg (waiting for black)"
    sleep 2
  fi
done

at_menu && { echo "[$INST] main menu reached"; exit 0; }
echo "[$INST] gave up after $ATTEMPTS attempts"; exit 1
