#!/usr/bin/env bash
# Launch both instrumented PCSX-Redux instances, linked over loopback SIO1.
# Assumes the AppImage is already built (see HANDOVER.md to rebuild).
# Then in each window: Enter → LINK GAME (5th item, NOT skirmish) → START SETUP (X).
set -euo pipefail

PD="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$PD/work/emu/squashfs-root/usr/bin/pcsx-redux"
BIOS="$PD/work/bios/scph7001.bin"
CUE="$PD/work/roms/retaliation-allies.cue"
DISP="${DISPLAY:-:1}"
PORT=6699

for f in "$BIN" "$BIOS" "$CUE"; do
  [ -e "$f" ] || { echo "MISSING: $f"; exit 1; }
done

# Refresh the extracted binary if a newer AppImage was built.
APP="$PD/pcsx-redux/PCSX-Redux-HEAD-x86_64.AppImage"
if [ -f "$APP" ] && [ "$APP" -nt "$BIN" ]; then
  echo "newer AppImage found — re-extracting…"
  cd "$PD/work/emu" && cp "$APP" . && chmod +x PCSX-Redux-HEAD-x86_64.AppImage
  rm -rf squashfs-root && ./PCSX-Redux-HEAD-x86_64.AppImage --appimage-extract >/dev/null
fi

# An instance that ignores SIGTERM keeps port 6699, so the new server fails to bind and the new
# client silently links to the stale process instead — a mixed-build pair with no warning.
# Escalate to SIGKILL and refuse to launch until the port is genuinely free.
# Launched from the extracted AppImage the process is named AppRun, not pcsx-redux, so killing only
# the latter leaves a stale port holder alive and this script's own pair then fails to bind.
alive() { pgrep -x pcsx-redux >/dev/null || pgrep -x AppRun >/dev/null; }
pkill -x pcsx-redux 2>/dev/null || true
pkill -x AppRun 2>/dev/null || true
timeout 5 bash -c 'while pgrep -x pcsx-redux >/dev/null || pgrep -x AppRun >/dev/null; do :; done' 2>/dev/null || true
if alive; then
  echo "instances ignored SIGTERM; escalating to SIGKILL"
  pkill -9 -x pcsx-redux 2>/dev/null || true
  pkill -9 -x AppRun 2>/dev/null || true
  timeout 5 bash -c 'while pgrep -x pcsx-redux >/dev/null || pgrep -x AppRun >/dev/null; do :; done' 2>/dev/null || true
fi
if alive || ss -tln 2>/dev/null | grep -q ":$PORT "; then
  echo "ABORT: pcsx-redux still running or port $PORT still held — refusing to start a mixed pair"
  ss -tlnp 2>/dev/null | grep ":$PORT " || true
  exit 1
fi

launch() {  # $1 = instA|instB
  local D="$PD/work/instances/$1"
  : > "$D/run.log"                     # fresh log; static counters reset with the process
  ( cd "$D" && setsid nohup env HOME="$D" XDG_CONFIG_HOME="$D/.config" DISPLAY="$DISP" \
      stdbuf -o0 -e0 "$BIN" -stdout -bios "$BIOS" -iso "$CUE" -run \
      > "$D/run.log" 2>&1 < /dev/null & )
}

echo "starting server (left)…"; launch instA
timeout 30 bash -c "while [ \$(ss -tln 2>/dev/null | grep -c $PORT) -eq 0 ]; do :; done"
echo "starting client (right)…"; launch instB
timeout 30 bash -c "while [ \$(ss -tn 2>/dev/null | grep -c $PORT) -lt 2 ]; do :; done"

echo
echo "READY — $(ss -tn 2>/dev/null | grep -c $PORT) established conns on $PORT"
echo "In each window: Enter → LINK GAME (5th item, NOT SKIRMISH) → START SETUP (X)"
echo "Watch: tail -f $PD/work/instances/inst{A,B}/run.log | grep SIO1-DIAG"
