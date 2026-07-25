#!/usr/bin/env bash
# Launch a loopback pair at a given PCSX_LINK_STALL_US, let both sit idle on the menu with the
# link connected but quiet, then report each side's emulation speed from its log.
#
# This reproduces the connected-but-quiet collapse locally: no second machine, no menu driving.
# Speed comes from the t=/cyc= pairs the diagnostics already emit (see tools/emuspeed.py).
#
#   ./stall-sweep.sh <stall_us> [settle_seconds]
set -euo pipefail

PD="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$PD/work/emu/squashfs-root/AppRun"
BIOS="$PD/work/bios/scph7001.bin"
CUE="$PD/work/roms/retaliation-allies.cue"
DISP="${DISPLAY:-:1}"
PORT=6699
STALL="${1:?usage: stall-sweep.sh <stall_us> [settle_seconds]}"
SETTLE="${2:-45}"

for f in "$BIN" "$BIOS" "$CUE"; do [ -e "$f" ] || { echo "MISSING: $f"; exit 1; }; done

# The AppImage runs as comm "AppRun", so a pcsx-redux-only pkill leaves a stale port holder and
# the next pair silently links to it instead.
pkill -x AppRun 2>/dev/null || true
pkill -x pcsx-redux 2>/dev/null || true
timeout 5 bash -c 'while pgrep -x AppRun >/dev/null || pgrep -x pcsx-redux >/dev/null; do :; done' || true
if ss -tln 2>/dev/null | grep -q ":$PORT "; then
  echo "ABORT: $PORT still held"; ss -tlnp 2>/dev/null | grep ":$PORT "; exit 1
fi

launch() {  # $1 = instA|instB
  local D="$PD/work/instances/$1"
  : > "$D/run.log"
  ( cd "$D" && setsid nohup env HOME="$D" XDG_CONFIG_HOME="$D/.config" DISPLAY="$DISP" \
      PCSX_LINK_STALL_US="$STALL" ${POLL_CYCLES:+PCSX_LINK_POLL_CYCLES=$POLL_CYCLES} \
      stdbuf -o0 -e0 "$BIN" -stdout -bios "$BIOS" -iso "$CUE" -run \
      > "$D/run.log" 2>&1 < /dev/null & )
}

echo "=== STALL_US=$STALL, settle ${SETTLE}s ==="
launch instA
timeout 30 bash -c "while ! ss -tln 2>/dev/null | grep -q ':$PORT '; do sleep 0.2; done"
launch instB
timeout 30 bash -c "while [ \$(ss -tn 2>/dev/null | grep -c ':$PORT ') -lt 2 ]; do sleep 0.2; done"
echo "both connected ($(ss -tn 2>/dev/null | grep -c ":$PORT ") conns)"

# Confirm the tuning actually reached the emulator before spending the settle time.
sleep 3
for i in instA instB; do
  grep -am1 "LINK TUNING" "$PD/work/instances/$i/run.log" || echo "  $i: NO LINK TUNING LINE YET"
done

sleep "$SETTLE"
for i in instA instB; do
  echo "--- $i ---"
  python3 "$PD/tools/emuspeed.py" "$PD/work/instances/$i/run.log" 10 2>&1 | head -8
done
