#!/usr/bin/env bash
# Measure whether ONE Steam Deck can host both emulator instances (loopback link).
#
# The desktop absorbs the link's wall-clock stall because it emulates ~8x realtime; a Deck has
# almost no headroom, so this asks whether two instances on one Deck leave anything usable.
# Run on the Deck itself. Reports emulated speed per instance; drives no menus.
set -euo pipefail

DIR="$HOME/ps1-lan-link"
BIN="$DIR/emu/squashfs-root/AppRun"
BIOS="$DIR/bios/scph7001.bin"
CUE="$DIR/roms/retaliation-allies.cue"
PORT=6699
SECONDS_TO_RUN="${1:-45}"

for f in "$BIN" "$BIOS" "$CUE"; do
  [ -e "$f" ] || { echo "MISSING: $f"; exit 1; }
done

pkill -x AppRun 2>/dev/null || true
pkill -x pcsx-redux 2>/dev/null || true
sleep 2

# Each instance needs its own config tree, otherwise they share (and overwrite) one pcsx.json.
for inst in instA instB; do
  D="$DIR/dual/$inst"
  mkdir -p "$D/.config"
  cp -r "$DIR/home/.config/pcsx-redux" "$D/.config/" 2>/dev/null || true
done

python3 - "$DIR" <<'PY'
import json, sys
base = sys.argv[1]
for inst, server in (("instA", True), ("instB", False)):
    cfg = f"{base}/dual/{inst}/.config/pcsx-redux/pcsx.json"
    d = json.load(open(cfg))
    def setkey(o, k, v):
        if isinstance(o, dict):
            for kk, vv in o.items():
                if kk == k: o[kk] = v
                elif isinstance(vv, (dict, list)): setkey(vv, k, v)
        elif isinstance(o, list):
            for it in o: setkey(it, k, v)
    setkey(d, "SIO1Server", server)
    setkey(d, "SIO1Client", not server)
    setkey(d, "SIO1Clienthost", "127.0.0.1")
    setkey(d, "SIO1ClientPort", 6699)
    setkey(d, "SIO1ServerPort", 6699)
    # Small windows: this measures CPU cost, and two 1280x800 windows would not fit anyway.
    g = d.setdefault("gui", {})
    g["WindowSizeX"], g["WindowSizeY"] = 640, 480
    g["WindowPosX"] = 0 if server else 640
    g["Fullscreen"] = False
    json.dump(d, open(cfg, "w"), indent=2)
    print(f"{inst}: {'server' if server else 'client'} on 127.0.0.1:6699")
PY

launch() {
  local inst="$1" D="$DIR/dual/$1"
  : > "$D/run.log"
  ( cd "$D" && setsid nohup env HOME="$D" XDG_CONFIG_HOME="$D/.config" DISPLAY="${DISPLAY:-:1}" \
      stdbuf -o0 -e0 "$BIN" -stdout -bios "$BIOS" -iso "$CUE" -run \
      > "$D/run.log" 2>&1 < /dev/null & )
}

echo "[dual] launching server…"; launch instA
timeout 40 bash -c "while [ \$(ss -tln 2>/dev/null | grep -c $PORT) -eq 0 ]; do sleep 1; done" || echo "[dual] server never listened"
echo "[dual] launching client…"; launch instB
timeout 40 bash -c "while [ \$(ss -tn 2>/dev/null | grep -c $PORT) -lt 2 ]; do sleep 1; done" || echo "[dual] link never established"

echo "[dual] both up; measuring for ${SECONDS_TO_RUN}s…"
sleep "$SECONDS_TO_RUN"

echo
echo "=== link ==="; ss -tn 2>/dev/null | grep $PORT || echo "NO LINK"
for inst in instA instB; do
  echo "=== $inst ==="
  grep -a SPEED "$DIR/dual/$inst/run.log" | tail -3 || echo "(no SPEED lines yet)"
done
echo
echo "=== host load ==="; uptime
