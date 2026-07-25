#!/usr/bin/env bash
# One-command reproduction of the connected-but-quiet collapse, on loopback.
#
# The link stall is paid per hsync whenever the RX FIFO is empty and the game read SIO_STAT
# recently. The game starts polling SIO_STAT on the main menu, so a side that reaches the menu
# with a connected but silent peer burns the whole stall budget every scanline. The peer, still on
# the title screen, is unaffected -- which is what makes this measurable: instB is the control.
#
# Reports distinct-frame rate for both sides. Expect (at STALL_US=15000): instA ~1 fps, instB ~58.
#
#   ./menu-collapse-test.sh <stall_us>
set -euo pipefail
PD="$(cd "$(dirname "$0")/.." && pwd)"
export DISPLAY="${DISPLAY:-:1}"
STALL="${1:?usage: menu-collapse-test.sh <stall_us>}"

"$PD/repro/stall-sweep.sh" "$STALL" 5 >/dev/null 2>&1 || true
sleep 2

# Client windows are the 900x700 ones; the 928x766 matches are the WM frames.
mapfile -t WINS < <(xdotool search --name "PCSX-Redux" | while read -r w; do
  eval "$(xdotool getwindowgeometry --shell "$w")"
  [ "${WIDTH:-0}" = 900 ] && echo "$w $X $Y"
done | sort -k2 -n)
[ "${#WINS[@]}" -eq 2 ] || { echo "expected 2 instance windows, got ${#WINS[@]}"; exit 1; }

read -r WB XB YB <<<"${WINS[0]}"   # left window
read -r WA XA YA <<<"${WINS[1]}"   # right window
echo "left=$WB@$XB,$YB  right=$WA@$XA,$YA"

echo "driving right window to the main menu…"
python3 "$PD/repro/to-menu.py" "$WA" | tail -2

# Overlapping windows: measure each side's unobscured strip rather than its whole client area.
echo "=== STALL_US=$STALL ==="
"$PD/tools/framerate-region.sh" 560x680 $((XA + 336)) $((YA + 6)) 6 "menu-side"
"$PD/tools/framerate-region.sh" 560x680 $((XB + 6))   $((YB + 6)) 6 "title-side"
