#!/usr/bin/env bash
# Reproduce the two-instance SIO1 link on one machine (loopback).
# Prereqs: PCSX-Redux AppImage extracted, scph7001.bin, and a decoded .bin+.cue.
# Edit the paths below, then run. Server (left) comes up first, client (right) connects.
set -euo pipefail

BIN="${BIN:?path to squashfs-root/usr/bin/pcsx-redux}"
BIOS="${BIOS:?path to scph7001.bin}"
CUE="${CUE:?path to game .cue}"
ROOT="${ROOT:-$PWD/instances}"          # per-instance HOME/config live here
PORT="${PORT:-6699}"
CLIENT_HOST="${CLIENT_HOST:-127.0.0.1}" # set to the server's Tailscale IP for LAN
DISP="${DISPLAY:-:1}"

gen_cfg() {  # $1=instdir  $2=server|client  $3=posX
  local dir="$1" role="$2" posx="$3"
  mkdir -p "$dir/.config/pcsx-redux"
  # Boot once (headful, 6s) in a throwaway HOME to emit a default pcsx.json, then patch it.
  local seed; seed="$(mktemp -d)"
  HOME="$seed" XDG_CONFIG_HOME="$seed/.config" DISPLAY="$DISP" timeout 6 "$BIN" >/dev/null 2>&1 || true
  python3 - "$seed/.config/pcsx-redux/pcsx.json" "$dir/.config/pcsx-redux/pcsx.json" \
           "$role" "$posx" "$PORT" "$CLIENT_HOST" "$BIOS" <<'PY'
import json,sys
src,dst,role,posx,port,host,bios=sys.argv[1:8]
c=json.load(open(src)); d=c["emulator"]["Debug"]; g=c["gui"]
c["emulator"]["Bios"]=bios; c["emulator"]["FastBoot"]=False
g["ShowSIO1"]=True; g["ShowLog"]=True; g["IdleSwapInterval"]=0  # no unfocused throttle
g["WindowMaximized"]=False; g["WindowPosY"]=120; g["WindowPosX"]=int(posx)
g["WindowSizeX"]=2300; g["WindowSizeY"]=1250
d["SIO1Mode"]=0
if role=="server":
    d["SIO1Server"]=True; d["SIO1ServerPort"]=int(port); d["SIO1Client"]=False; d["SIO1Clienthost"]=""
else:
    d["SIO1Server"]=False; d["SIO1Client"]=True; d["SIO1Clienthost"]=host; d["SIO1ClientPort"]=int(port)
json.dump(c,open(dst,"w"),indent=2)
print(f"{role}: server={d['SIO1Server']} client={d['SIO1Client']} host='{d['SIO1Clienthost']}' port={port}")
PY
}

launch() {  # $1=instdir
  local dir="$1"
  ( cd "$dir" && HOME="$dir" XDG_CONFIG_HOME="$dir/.config" DISPLAY="$DISP" \
      "$BIN" -bios "$BIOS" -iso "$CUE" -run >"$dir/run.log" 2>&1 & )
}

mkdir -p "$ROOT/instA" "$ROOT/instB"
gen_cfg "$ROOT/instA" server 100
gen_cfg "$ROOT/instB" client 2620

echo "launching server (left)…"; launch "$ROOT/instA"
until ss -tln 2>/dev/null | grep -q ":$PORT "; do :; done   # wait for listen
echo "server listening on $PORT; launching client (right)…"; launch "$ROOT/instB"
echo "done. In each window: Start(Enter) → LINK GAME → set side → START SETUP(X)."
echo "watch: ss -tn | grep $PORT   (2 ESTABLISHED = link up)"
