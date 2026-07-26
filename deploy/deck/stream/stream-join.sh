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

# Video and audio arrive separately and neither waits for the other.
#
# Video is a bare H.264 stream with no container and no timestamps, so the player decodes and shows
# each frame as it arrives - there is no clock to lock onto and nothing to buffer against. Audio is
# raw PCM paced by this machine's own sound card. Every attempt to carry both in one container ended
# with the player reporting "no reference clock" and spending its time syncing instead of playing.
AUDIO_PORT="${AUDIO_PORT:-6692}"
FPS="${FPS:-60}"

AUDIO_PID=""
if command -v aplay >/dev/null && command -v socat >/dev/null; then
  socat -u "TCP:$HOST:$AUDIO_PORT" - 2>/dev/null | aplay -q -f S16_LE -r 44100 -c 2 -t raw - &
  AUDIO_PID=$!
  echo "[join] audio pid=$AUDIO_PID from $HOST:$AUDIO_PORT"
else
  echo "[join] WARNING: aplay or socat missing - no sound"
fi

stop_audio() { [ -n "$AUDIO_PID" ] && kill "$AUDIO_PID" 2>/dev/null; }
trap 'stop_audio; cleanup' EXIT INT TERM

echo "[join] playing tcp://$HOST:$PORT"

# The bespoke player is preferred because it has no clock and no queue: it drains the socket every
# pass, decodes all of it and draws only the newest frame, so it sits about two frames behind the
# host and cannot accumulate. ffplay is kept as the fallback for a Deck that has not had the player
# deployed yet - set PLAYER=ffplay to force it.
# Not exec: this shell owns the pad forwarder and the audio pipeline, and replacing it would leave
# both running after the player quits.
PLAYER="${PLAYER:-auto}"
if [ "$PLAYER" != "ffplay" ] && [ -x "$HERE/ps1-join-player" ]; then
  echo "[join] using the bespoke player"
  "$HERE/ps1-join-player" "tcp://$HOST:$PORT"
else
  echo "[join] using ffplay"
  # -framerate matters more than it looks. A bare H.264 stream carries no timing at all, so the
  # demuxer falls back to 25 fps: frames arrive at 60 a second and are paced out at 25, the backlog
  # grows for as long as the session lasts, and every button press waits behind it.
  ffplay -hide_banner -loglevel warning \
    -f h264 -framerate "$FPS" -flags low_delay -fflags nobuffer -framedrop \
    -autoexit -fs -window_title "PS1 LAN Link" \
    "tcp://$HOST:$PORT"
fi
