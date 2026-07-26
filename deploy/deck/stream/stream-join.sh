#!/usr/bin/env bash
# Joiner: play the host's stream fullscreen and send this machine's controller back.
#
# The crudest thing that is still a joiner — an address in a conf file, no discovery, no UI. That is
# deliberate: what identifies a host is the one decision the wire format bakes in, so it is an
# address now and a broadcast later, and the swap stays small. Host/Join screens are stage 3.
#
# Usage:  stream-join.sh              # reads join.conf beside this script
#         HOST=10.0.0.1 stream-join.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CONF="$HERE/join.conf"
LOG="${LOG:-$HERE/join.log}"

# A value already in the environment beats the file, so a one-off host can be passed without
# editing anything.
HOST_ENV="${HOST:-}"
# shellcheck disable=SC1090
[ -f "$CONF" ] && source "$CONF"
HOST="${HOST_ENV:-${HOST:-}}"
PORT="${PORT:-6690}"
INPUT_PORT="${INPUT_PORT:-6691}"
PAD_DEVICE="${PAD_DEVICE:-}"

exec > >(tee "$LOG") 2>&1
echo "[join] === $(date '+%F %T') ==="

die() { echo "[join] ERROR: $*" >&2; exit 1; }
[ -n "$HOST" ] || die "no host set — put HOST=<address> in $CONF or pass HOST=<address>"
command -v ffplay >/dev/null || die "ffplay not installed"

PAD_PID=""
cleanup() {
  [ -n "$PAD_PID" ] && kill "$PAD_PID" 2>/dev/null
  echo "[join] exit"
}
trap cleanup EXIT INT TERM

# Input first: the host releases every button when this disconnects, so a joiner that dies mid-press
# cannot leave the other player's game holding a direction.
if [ -n "$(ls /dev/input/js* 2>/dev/null)" ]; then
  PAD_ARGS=(--port "$INPUT_PORT")
  [ -n "$PAD_DEVICE" ] && PAD_ARGS+=(--device "$PAD_DEVICE")
  python3 "$HERE/pad-forward.py" "$HOST" "${PAD_ARGS[@]}" --quiet &
  PAD_PID=$!
  echo "[join] pad forwarder pid=$PAD_PID"
else
  echo "[join] WARNING: no /dev/input/js* — you will see the game but cannot play it"
fi

# -fflags nobuffer and a zero-sized queue trade smoothness for latency, which is the right trade for
# something being played rather than watched. -sync ext keeps audio and video tied to the stream
# clock rather than letting video chase audio.
echo "[join] playing tcp://$HOST:$PORT"
ffplay -hide_banner -loglevel warning \
  -fflags nobuffer -flags low_delay -framedrop -infbuf \
  -sync ext -autoexit -fs \
  -window_title "PS1 LAN Link" \
  "tcp://$HOST:$PORT"
