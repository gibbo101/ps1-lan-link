#!/usr/bin/env bash
# Stage 1 of the unified app: a single Game Mode "game" that runs both sides of a PS1 link match.
#
# instA owns the screen. instB runs with -no-ui, which swaps the GUI for a stub UI (PCSX::TUI) that
# creates no window, no GL context and no GLFW input — so gamescope has exactly one app to
# composite and Steam Input has exactly one app to drive. The SIO1 link between them stays on
# loopback, the only channel whose round trip fits inside a frame.
#
# Because instB has no GLFW it can take no controller input; its pad is driven through the
# emulator's HTTP/Lua control surface instead (see instb-ctl.lua and pad.sh).
#
# Usage: ps1-link-gamemode.sh [game]
#   game = a .cue, a .bin/.img (a cue is generated beside it), or a substring matched against the
#          library. With no argument, GAME from gamemode.conf is used.
set -uo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
GM="$DIR/gamemode"
CONF="$GM/gamemode.conf"
LOG="$GM/gamemode.log"

BIN="$DIR/emu/squashfs-root/AppRun"
BIOS="$DIR/bios/scph7001.bin"
ROMS="$DIR/roms"
LIB="${LIB:-$HOME/retrodeck/roms/psx}"

# SIO1 link, loopback only: the client dials 127.0.0.1 and the listener binds it too. That bind
# matters because the listener re-points the link at any later connection, so a listener on a
# routable address would let a host on the LAN displace instB mid-match.
PORT=6699
# Control surface. Kept next to the link port and away from 8080/8081: Steam's own steamwebhelper
# listens on 8080, and the emulator's web server fails to bind silently, so a collision looks like a
# working launch with a dead control surface.
#
# SECURITY: this API is unauthenticated — it reads the screen, drives both pads, dumps emulated RAM
# and, via /api/v1/screen/save?filepath=, writes any file this user can. It is confined to loopback
# by the emulator patch, so reaching it means having access to this machine already; reach it from
# elsewhere with an SSH tunnel, never by setting PCSX_BIND_ADDRESS to a routable address. CTL=0
# turns it off entirely, at the cost of the start-together behaviour below.
WEB_A=6680          # instA  control/verification surface
WEB_B=6681          # instB  control/verification surface — the only way to drive a headless pad

# Stage 2 streaming. instB publishes frames and audio to a unix socket; stream-host encodes them
# and is the only thing here that accepts a LAN connection. It carries media and pad input only —
# never the control surface above, which stays on loopback. Set STREAM=0 for couch play, where
# there is no joiner and the encoder would be spending a Deck's headroom on nobody.
STREAM_SOCK="${XDG_RUNTIME_DIR:-/tmp}/ps1-instb-stream.sock"
STREAM_PORT=6690
STREAM_INPUT_PORT=6691

# Launched from Game Mode there is no terminal, so everything goes to a log we can read over SSH.
exec > >(tee "$LOG") 2>&1
echo "[gm] === launch $(date '+%F %T') ==="

GAME="${1:-}"
# gamemode.conf sets CTL, so a value passed in the environment has to be remembered across the
# source or it is silently overwritten — which would hand the no-control-surface relaunch below the
# same CTL=1 it was trying to escape, and loop forever.
CTL_ENV="${CTL:-}"
# shellcheck disable=SC1090
[ -f "$CONF" ] && source "$CONF"
GAME="${1:-${GAME:-}}"
PAD_ID="${PAD_ID:-0}"
FULLSCREEN="${FULLSCREEN:-1}"
CTL="${CTL_ENV:-${CTL:-1}}"   # set 0 to leave the HTTP control surface off
STREAM="${STREAM:-1}"         # set 0 for couch play — no joiner, so nothing to encode for

# Errors go to stderr, not stdout: resolve_game runs inside a command substitution, and a message on
# stdout there is captured into the variable instead of reaching the player.
die() { echo "[gm] ERROR: $*" >&2; exit 1; }
[ -x "$BIN" ] || die "emulator missing: $BIN"
[ -f "$BIOS" ] || die "BIOS missing: $BIOS"
[ -n "$GAME" ] || die "no game given and none set in $CONF"

# Hash what actually runs. Reporting a different path only agrees while AppRun stays a symlink, and
# this line exists precisely to prove which build ran.
echo "[gm] binary md5: $(md5sum "$(readlink -f "$BIN")" 2>/dev/null | cut -d' ' -f1)"

# A cue sheet is required; these images are single-track MODE2/2352, the PS1 norm.
make_cue() {
  local bin="$1" cue="$2" sz
  sz=$(stat -c%s "$bin")
  [ $((sz % 2352)) -eq 0 ] || die "$(basename "$bin") is not a multiple of 2352 bytes — unknown layout"
  mkdir -p "$(dirname "$cue")" || die "cannot create $(dirname "$cue")"
  # A failed write here would otherwise be reported as a generated cue, and the missing file would
  # not surface until the emulator failed to open it.
  printf 'FILE "%s" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n' "$bin" > "$cue" \
    || die "could not write $cue"
  echo "[gm] generated $(basename "$cue")"
}

resolve_game() {
  local want="$1" hit
  [ -f "$want" ] && case "$want" in
    *.cue) echo "$want"; return ;;
    *.bin|*.img) local c="$ROMS/$(basename "${want%.*}").cue"; make_cue "$want" "$c" >&2; echo "$c"; return ;;
  esac
  hit=$(ls "$ROMS"/*.cue 2>/dev/null | grep -i -- "$want" | head -1)
  [ -n "$hit" ] && { echo "$hit"; return; }
  hit=$(ls "$LIB"/*.bin "$LIB"/*.img 2>/dev/null | grep -i -- "$want" | head -1)
  [ -n "$hit" ] || die "no game matching '$want'"
  local c="$ROMS/$(basename "${hit%.*}" | tr ' ' '-').cue"
  make_cue "$hit" "$c" >&2
  echo "$c"
}

CUE=$(resolve_game "$GAME") || exit 1
echo "[gm] game: $(basename "$CUE")"

# ---------------------------------------------------------------- preflight

# A survivor from a previous run keeps port 6699, so the new server fails to bind and the new client
# links to the stale process instead — a mixed pair with no warning. Refuse to start until it is free.
# Matched on our own binary's path rather than the process name: every AppImage on the system runs
# as "AppRun", so killing by name would take unrelated applications with it.
pkill -f "$BIN" 2>/dev/null
for _ in $(seq 1 40); do
  pgrep -f "$BIN" >/dev/null || break
  sleep 0.25
done
if pgrep -f "$BIN" >/dev/null; then
  echo "[gm] instances ignored SIGTERM; escalating"
  pkill -9 -f "$BIN" 2>/dev/null
  sleep 2
fi
ss -tln 2>/dev/null | grep -q ":$PORT " && die "port $PORT still held — refusing to start a mixed pair"

# virtual-pads.py exists to work around Desktop Mode, where Steam Input never feeds its virtual pads.
# In Game Mode it is pure harm: its uinput pads occupy the low gamepad indices, so PAD_ID=0 binds to
# a pad nobody is holding and the Deck's own controls appear dead.
if systemctl --user is-active --quiet ps1-pads 2>/dev/null; then
  echo "[gm] stopping ps1-pads — its virtual pads would take the low gamepad indices"
  systemctl --user stop ps1-pads
  sleep 2
fi

# PAD_ID indexes the emulator's list of gamepad-capable devices, in this order — not /dev/input/js*
# numbering, and not anything visible in Steam.
echo "[gm] gamepads (PAD_ID -> device):"
idx=0
for d in $(ls -d /sys/class/input/js* 2>/dev/null | sort -V); do
  echo "[gm]   $idx -> $(basename "$d") $(cat "$d/device/name" 2>/dev/null)"
  idx=$((idx + 1))
done
[ "$idx" = 0 ] && echo "[gm]   none found — instA will have no controller"

# ---------------------------------------------------------------- config

# pcsx.json is rewritten on exit and silently reverts anything we set, so every launch re-applies it.
python3 - "$GM" "$DIR" "$PAD_ID" "$FULLSCREEN" "$PORT" "$WEB_A" "$WEB_B" "$CTL" <<'PY' || die "config generation failed"
import json, os, shutil, sys
gm, base, pad_id, fullscreen, port, web_a, web_b, ctl = sys.argv[1:9]
pad_id, fullscreen, port, web_a, web_b, ctl = int(pad_id), int(fullscreen), int(port), int(web_a), int(web_b), int(ctl)
template = f"{base}/home/.config/pcsx-redux/pcsx.json"

def setkey(o, k, v):
    if isinstance(o, dict):
        for kk, vv in o.items():
            if kk == k: o[kk] = v
            elif isinstance(vv, (dict, list)): setkey(vv, k, v)
    elif isinstance(o, list):
        for it in o: setkey(it, k, v)

for inst, server, web in (("instA", True, web_a), ("instB", False, web_b)):
    cfgdir = f"{gm}/{inst}/.config/pcsx-redux"
    os.makedirs(cfgdir, exist_ok=True)
    cfg = f"{cfgdir}/pcsx.json"
    if not os.path.exists(cfg):
        shutil.copy(template, cfg)
    d = json.load(open(cfg))

    setkey(d, "SIO1Server", server)
    setkey(d, "SIO1Client", not server)
    setkey(d, "SIO1Clienthost", "127.0.0.1")
    setkey(d, "SIO1ClientPort", port)
    setkey(d, "SIO1ServerPort", port)
    setkey(d, "WebServer", bool(ctl))
    setkey(d, "WebServerPort", web)

    # instB has no GLFW, so its pad ID is meaningless — but Connected must stay true or read()
    # returns 0xff and the Lua overrides never reach the game.
    d["pads"][0]["Connected"] = True
    d["pads"][0]["ID"] = pad_id if server else 0

    # Being windowless does not make instB silent: its SPU still opens an audio device, and the host
    # hears both sides at once. The host is supposed to see and hear only instA.
    d.setdefault("SPU", {})["Mute"] = not server

    g = d.setdefault("gui", {})
    if server:
        g["Fullscreen"] = bool(fullscreen)
        g["FullWindowRender"] = True
        g["ShowMenu"] = False
        g["WindowSizeX"], g["WindowSizeY"] = 1280, 800
        g["WindowPosX"], g["WindowPosY"] = 0, 0
    json.dump(d, open(cfg, "w"), indent=2)
    print(f"[gm] config {inst}: server={server} web={web if ctl else 'off'} "
          f"pad={d['pads'][0]['ID']} muted={d['SPU']['Mute']}")
PY

# ---------------------------------------------------------------- launch

PID_A=""; PID_B=""; PID_STREAM=""
cleanup() {
  trap - EXIT INT TERM   # a signal runs this and then EXIT would run it again
  echo "[gm] cleaning up"
  [ -n "$PID_STREAM" ] && kill "$PID_STREAM" 2>/dev/null
  for p in $PID_B $PID_A; do kill "$p" 2>/dev/null; done
  for _ in $(seq 1 40); do
    { [ -n "$PID_A" ] && kill -0 "$PID_A" 2>/dev/null; } || { [ -n "$PID_B" ] && kill -0 "$PID_B" 2>/dev/null; } || break
    sleep 0.25
  done
  for p in $PID_B $PID_A; do kill -9 "$p" 2>/dev/null; done
  [ -n "$PID_STREAM" ] && kill -9 "$PID_STREAM" 2>/dev/null
  pkill -f "$BIN" 2>/dev/null
  rm -f "$STREAM_SOCK"
  echo "[gm] exit: $(pgrep -cf "$BIN") emulator processes left, port $PORT held: $(ss -tln 2>/dev/null | grep -c ":$PORT ")"
}
trap cleanup EXIT INT TERM

# Sets LAUNCHED_PID rather than echoing it: a command substitution would background the emulator
# inside a subshell, and the main shell then cannot wait on it.
launch() {  # $1 = instA|instB, rest = extra args
  local inst="$1"; shift
  local D="$GM/$inst"
  : > "$D/run.log"
  env HOME="$D" XDG_CONFIG_HOME="$D/.config" \
    stdbuf -o0 -e0 "$BIN" -stdout -bios "$BIOS" -iso "$CUE" "${RUN_ARGS[@]}" "$@" \
    > "$D/run.log" 2>&1 &
  LAUNCHED_PID=$!
}

# Retaliation builds its main menu once, and LINK GAME is only on it if the peer was already
# connected. Whichever side boots first therefore reaches a menu with no LINK GAME entry, however
# healthy the link is. Starting both paused and resuming them only once the socket is up means
# neither side can get there early. Without a control surface there is no way to resume, so that
# case keeps the old behaviour and accepts the race.
RUN_ARGS=(-run)
[ "$CTL" = 1 ] && RUN_ARGS=()

resume() {  # $1 = port
  curl -fsS --max-time 5 -X POST "http://127.0.0.1:$1/api/v1/execution-flow?function=resume" >/dev/null 2>&1
}

CTL_ARGS=()
[ "$CTL" = 1 ] && CTL_ARGS=(-dofile "$GM/instb-ctl.lua")

# Only instB streams. instA is what the host player is looking at on this screen.
STREAM_ARGS=()
if [ "$STREAM" = 1 ]; then
  rm -f "$STREAM_SOCK"
  STREAM_ARGS=(-stream-socket "$STREAM_SOCK")
fi

# instA is the SIO1 server, so it has to be listening before instB tries to connect.
echo "[gm] starting instA (visible, server)…"
launch instA "${CTL_ARGS[@]}"; PID_A=$LAUNCHED_PID
for _ in $(seq 1 120); do
  ss -tln 2>/dev/null | grep -q ":$PORT " && break
  kill -0 "$PID_A" 2>/dev/null || die "instA died during startup — see $GM/instA/run.log"
  sleep 0.25
done
ss -tln 2>/dev/null | grep -q ":$PORT " || die "instA never listened on $PORT"

echo "[gm] starting instB (headless, client)…"
launch instB -no-ui "${CTL_ARGS[@]}" "${STREAM_ARGS[@]}"; PID_B=$LAUNCHED_PID
for _ in $(seq 1 120); do
  [ "$(ss -tn 2>/dev/null | grep -c ":$PORT ")" -ge 2 ] && break
  kill -0 "$PID_B" 2>/dev/null || die "instB died during startup — see $GM/instB/run.log"
  sleep 0.25
done

echo "[gm] link sockets: $(ss -tn 2>/dev/null | grep -c ":$PORT ") (2 = connected)"
echo "[gm] instA pid=$PID_A  instB pid=$PID_B (headless)"

# The web server gives up quietly if its port is taken. When the instances were started paused it is
# also the only way to start them, so a dead surface is fatal rather than cosmetic.
if [ "$CTL" = 1 ]; then
  ctl_ok=1
  for pair in "instA:$WEB_A" "instB:$WEB_B"; do
    inst=${pair%%:*}; p=${pair##*:}
    if curl -fsS --max-time 3 "http://127.0.0.1:$p/api/v1/lua/ping" >/dev/null 2>&1; then
      echo "[gm] control $inst: http://${HOSTNAME:-127.0.0.1}:$p"
    else
      echo "[gm] WARNING: $inst control surface not answering on $p — port taken?"
      ss -tln 2>/dev/null | grep ":$p " || true
      ctl_ok=0
    fi
  done

  if [ "$ctl_ok" = 1 ]; then
    echo "[gm] link is up — starting both sides together"
    resume "$WEB_B" && resume "$WEB_A" || ctl_ok=0
  fi

  # Paused emulators that cannot be resumed are a black screen, so rather than leave the player
  # with one, start over without the control surface and accept the menu race.
  if [ "$ctl_ok" = 0 ]; then
    echo "[gm] cannot resume — relaunching without the control surface"
    cleanup
    CTL=0 exec "$0" "$GAME"
  fi
fi
# The streamer attaches to instB's export socket and waits. It builds no encoder until a joiner
# actually connects, so a solo or couch session pays nothing for it being here.
if [ "$STREAM" = 1 ]; then
  if [ -x "$DIR/stream/stream-host.py" ]; then
    python3 "$DIR/stream/stream-host.py" \
      --export-socket "$STREAM_SOCK" --port "$STREAM_PORT" --input-port "$STREAM_INPUT_PORT" \
      --control-port "$WEB_B" > "$GM/stream-host.log" 2>&1 &
    PID_STREAM=$!
    echo "[gm] streamer pid=$PID_STREAM — joiners connect to $(hostname -I 2>/dev/null | awk '{print $1}'):$STREAM_PORT"
  else
    echo "[gm] WARNING: STREAM=1 but stream/stream-host.py is missing — no joiner can connect"
  fi
fi

echo "[gm] running — this script exits when instA does"

# instA's lifetime is the game's lifetime; instB is torn down with it by the trap. instB is watched
# too, because if it dies the link is gone and the game still looks fine — the player would be left
# in a session that can never sync, with nothing said about why.
while kill -0 "$PID_A" 2>/dev/null; do
  if ! kill -0 "$PID_B" 2>/dev/null; then
    echo "[gm] WARNING: instB exited — the link is dead; see $GM/instB/run.log"
    break
  fi
  sleep 2
done
wait "$PID_A"
