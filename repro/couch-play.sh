#!/usr/bin/env bash
# Two-player couch play on one host: both emulators local, SIO1 link on loopback, one gamepad each.
#
# Loopback is the only channel whose round trip fits inside a frame — the driver needs ~182 ordered
# round trips per payload, so any real network is ~30x too slow. Keeping both instances here is what
# makes the link hold.
#
# Usage: ./repro/couch-play.sh [screen_width] [screen_height]
set -euo pipefail

PD="$(cd "$(dirname "$0")/.." && pwd)"
DISP="${DISPLAY:-:1}"
SCREEN_W="${1:-5120}"
SCREEN_H="${2:-1440}"

export DISPLAY="$DISP"

# Configs are rewritten by the emulator on exit, so re-apply geometry and pad bindings every launch.
echo "[couch] applying window + gamepad config…"
python3 "$PD/tools/couch-setup.py" "$SCREEN_W" "$SCREEN_H"

pads=$(ls /dev/input/js* 2>/dev/null | wc -l)
if [ "$pads" -lt 2 ]; then
  echo "[couch] WARNING: fewer than 2 joystick devices present."
  echo "        Each DualSense also exposes a motion-sensor device, so two pads normally show 4."
  echo "        Player 2 can use the keyboard on the focused window: arrows, X=cross, Enter=Start."
fi

"$PD/repro/run-link-test.sh"

# Gamepad activity does not reset the idle timer, so a session looks idle however hard it is played.
# X's own screensaver is already off here; the desktop environment's idle handling is what bites.
if command -v systemd-inhibit >/dev/null; then
  setsid systemd-inhibit --what=idle:sleep --why="PS1 LAN Link couch play" \
    bash -c 'while pgrep -x pcsx-redux >/dev/null; do sleep 20; done' >/dev/null 2>&1 &
  echo "[couch] idle/sleep inhibited while the emulators are running"
fi

# The window manager assigns sides by its own placement rules, so report what actually happened
# rather than assuming the configured positions were honoured. Each instance owns one gamepad,
# so the mapping below is what decides where each player sits.
sleep 2
echo
echo "[couch] seating:"
for w in $(xdotool search --class pcsx 2>/dev/null); do
  pid=$(xdotool getwindowpid "$w" 2>/dev/null) || continue
  inst=$(tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | sed -n 's|^HOME=.*/||p')
  x=$(xdotool getwindowgeometry "$w" | sed -n 's/.*Position: \([0-9-]*\),.*/\1/p')
  [ -z "${x:-}" ] && continue
  side=$([ "$x" -lt $((SCREEN_W / 2)) ] && echo LEFT || echo RIGHT)
  case "$inst" in
    instA) role="host  (presses X to start)"; pad=1 ;;
    instB) role="guest (enters LINK GAME first)"; pad=2 ;;
    *)     role="?"; pad="?" ;;
  esac
  printf "  %-5s window  %-6s  gamepad #%s  %s\n" "$side" "$inst" "$pad" "$role"
done

cat <<'EOF'

Menu order (both sides must press START SETUP — skipping the second press is what kills setup):
  1. guest window : Start -> intro -> wait for black -> Start -> Up -> X   (WAITING TO CONNECT)
  2. host  window : same path                                             (COUNTRY/COLOR)
  3. both         : START SETUP.  The sides deliberately swap here.
  4. host         : X to start the match.
EOF
