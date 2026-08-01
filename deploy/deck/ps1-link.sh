#!/bin/bash
# PS1 LAN Link — single entry point for the Steam Deck (Game Mode shortcut target).
# No arguments: reads link.conf for role/peer, launches the patched PCSX-Redux fully
# configured to auto-connect over the link and boot straight into Retaliation.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
CONF="$DIR/link.conf"
LOG="$DIR/run.log"

# Capture everything to a log — launched from Game Mode, stdout/stderr otherwise vanish.
exec > >(tee "$LOG") 2>&1
echo "[ps1-link] === launch $(date '+%F %T' 2>/dev/null || echo now) ==="
trap 'echo "[ps1-link] EXIT code=$?"' EXIT
BIN="$DIR/emu/squashfs-root/AppRun"
BIOS="$DIR/bios/scph7001.bin"
CUE="$DIR/roms/retaliation-allies.cue"

# Role/peer live in link.conf so the Game Mode shortcut never changes when the peer does.
# These are only the fallbacks for a missing link.conf; set the real peer there.
ROLE=client
HOST=127.0.0.1
PORT=6699
[ -f "$CONF" ] && source "$CONF"

for f in "$BIN" "$BIOS" "$CUE"; do
  [ -e "$f" ] || { echo "MISSING: $f — run setup.sh first"; exit 1; }
done

# The emulator binds its listeners to loopback, because it accepts anything that reaches them and
# re-points the link at the newest connection. This launcher is the one case whose peer is on
# another machine, so a server here has to listen on an address that peer can reach.
# This launcher is part of the abandoned deck-to-deck "netpeer" experiment (see HANDOVER: the
# symmetric two-Deck link was measured and ruled out). It survives for experiments only, and it no
# longer defaults to exposing the emulator: the control surface reachable on a wide bind can read
# and write emulated memory and files, so the operator has to choose that exposure explicitly.
if [ "$ROLE" = server ]; then
  [ -n "${BIND_ADDRESS:-}" ] || { echo "[ps1-link] server role needs BIND_ADDRESS=<lan-addr> set explicitly (this exposes the emulator's control surface to that network)"; exit 1; }
  export PCSX_BIND_ADDRESS="$BIND_ADDRESS"
  echo "[ps1-link] server: listening on $PCSX_BIND_ADDRESS:$PORT for the remote client"
fi

# Link stall tuning → env the emulator reads at runtime (no rebuild to retune).
[ -n "${STALL_US:-}" ] && export PCSX_LINK_STALL_US="$STALL_US"
[ -n "${POLL_CYCLES:-}" ] && export PCSX_LINK_POLL_CYCLES="$POLL_CYCLES"
[ -n "${STALL_FLOOR_US:-}" ] && export PCSX_LINK_STALL_FLOOR_US="$STALL_FLOOR_US"
[ -n "${STALL_BACKOFF_AFTER:-}" ] && export PCSX_LINK_STALL_BACKOFF_AFTER="$STALL_BACKOFF_AFTER"
[ -n "${STALL_PROBE_EVERY:-}" ] && export PCSX_LINK_STALL_PROBE_EVERY="$STALL_PROBE_EVERY"
[ -n "${STALL_MAX_SHARE:-}" ] && export PCSX_LINK_STALL_MAX_SHARE="$STALL_MAX_SHARE"
echo "[ps1-link] link tuning: stallUs=${PCSX_LINK_STALL_US:-default} pollCycles=${PCSX_LINK_POLL_CYCLES:-default}"

# Isolated config so our pre-baked pcsx.json (SIO1 client/server + Raw mode) is what loads,
# never the user's global PCSX-Redux config.
export HOME="$DIR/home"
export XDG_CONFIG_HOME="$DIR/home/.config"
CFG="$XDG_CONFIG_HOME/pcsx-redux/pcsx.json"

# Stamp role/peer into the config at launch — one shortcut, reconfigurable via link.conf.
python3 - "$CFG" "$ROLE" "$HOST" "$PORT" <<'PY'
import json,sys
cfg,role,host,port=sys.argv[1],sys.argv[2],sys.argv[3],int(sys.argv[4])
d=json.load(open(cfg))
def setkey(o,k,v):
    hit=False
    if isinstance(o,dict):
        for kk,vv in o.items():
            if kk==k: o[kk]=v; hit=True
            elif isinstance(vv,(dict,list)): hit=setkey(vv,k,v) or hit
    elif isinstance(o,list):
        for it in o: hit=setkey(it,k,v) or hit
    return hit
is_server = role=="server"
setkey(d,"SIO1Server",is_server)
setkey(d,"SIO1Client",not is_server)
setkey(d,"SIO1Clienthost",host)
setkey(d,"SIO1ClientPort",port)
setkey(d,"SIO1ServerPort",port)
json.dump(d,open(cfg,"w"),indent=2)
print(f"[ps1-link] role={role} host={host} port={port}")
PY

echo "[ps1-link] launching PCSX-Redux…"
"$BIN" -stdout -bios "$BIOS" -iso "$CUE" -run
echo "[ps1-link] emulator returned $?"
