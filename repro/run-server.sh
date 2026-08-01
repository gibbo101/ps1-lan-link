#!/usr/bin/env bash
# Launch ONE desktop-side PCSX-Redux as the SIO1 server (:6699) for a real-network link test against
# a remote client (Steam Deck over Tailscale/LAN).
#
# Listeners now bind loopback, so a remote client cannot reach this one unless PCSX_BIND_ADDRESS is
# set to a routable address. That is deliberate — the emulator's listeners are unauthenticated — and
# it is exported here rather than in the emulator's default because this script is the one case that
# genuinely needs a peer on another machine.
set -euo pipefail
# Loopback unless the operator explicitly widens it: the surface behind this bind can read and
# write emulated memory and files, and a test rig has no business exposing that to a network.
export PCSX_BIND_ADDRESS="${PCSX_BIND_ADDRESS:-127.0.0.1}"
PD="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$PD/work/emu/squashfs-root/AppRun"
BIOS="$PD/work/bios/scph7001.bin"
CUE="$PD/work/roms/retaliation-allies.cue"
D="$PD/work/instances/instA"
DISP="${DISPLAY:-:1}"
PORT=6699

for f in "$BIN" "$BIOS" "$CUE"; do [ -e "$f" ] || { echo "MISSING: $f"; exit 1; }; done

# The AppImage runs as comm "AppRun" (symlink name), not "pcsx-redux" — match both.
pkill -x AppRun 2>/dev/null || true
pkill -x pcsx-redux 2>/dev/null || true
timeout 5 bash -c 'while pgrep -x AppRun >/dev/null || pgrep -x pcsx-redux >/dev/null; do :; done' || true
if ss -tln 2>/dev/null | grep -q ":$PORT "; then
  echo "ABORT: $PORT still held"; ss -tlnp 2>/dev/null | grep ":$PORT "; exit 1
fi

: > "$D/run.log"
# Real-network peer: match the Deck's link stall tuning (see deploy/deck/link.conf).
STALL_US="${STALL_US:-15000}"
( cd "$D" && setsid nohup env HOME="$D" XDG_CONFIG_HOME="$D/.config" DISPLAY="$DISP" \
    PCSX_LINK_STALL_US="$STALL_US" ${POLL_CYCLES:+PCSX_LINK_POLL_CYCLES=$POLL_CYCLES} \
    stdbuf -o0 -e0 "$BIN" -stdout -bios "$BIOS" -iso "$CUE" -run \
    > "$D/run.log" 2>&1 < /dev/null & )

if timeout 25 bash -c "while ! ss -tln 2>/dev/null | grep -q ':$PORT '; do sleep 0.3; done"; then
  echo "LISTENING on $PORT:"; ss -tlnp 2>/dev/null | grep ":$PORT "
else
  echo "NO LISTENER after 25s — log tail:"; tail -15 "$D/run.log"; exit 1
fi
