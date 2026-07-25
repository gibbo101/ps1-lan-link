#!/usr/bin/env bash
# Drive either instance's pad over HTTP, and grab its screen.
#
# instB is headless, so this is the only way to press a button on it. instA accepts it too, which is
# how a link game can be driven end to end from SSH with nobody holding a controller.
#
# Usage: pad.sh <A|B> <button>...        tap each button in turn
#        pad.sh <A|B> hold <button>      press and leave held
#        pad.sh <A|B> release            release everything
#        pad.sh <A|B> shot <file.png>    save the emulated screen
#
# Buttons: select start up down left right cross circle square triangle l1 l2 r1 r2
#
# The control surface listens on loopback only, so driving a Deck from another machine means
# tunnelling to it first — `ssh -L 6681:127.0.0.1:6681 deck@steamdeck`, then run this locally with
# the default HOST. Pointing HOST at the Deck's own address will not connect.
set -uo pipefail

HOST="${HOST:-127.0.0.1}"
HOLD_MS="${HOLD_MS:-120}"

case "${1:-}" in
  A|a) PORT=6680 ;;
  B|b) PORT=6681 ;;
  *) echo "usage: pad.sh <A|B> <button>... | hold <button> | release | shot <file.png>" >&2; exit 1 ;;
esac
shift

api() { curl -fsS --max-time 5 "http://$HOST:$PORT/api/v1$1"; }

case "${1:-}" in
  release) api "/lua/release"; exit ;;
  hold)    [ -n "${2:-}" ] || { echo "usage: pad.sh <A|B> hold <button>" >&2; exit 1; }
           api "/lua/pad?button=$2&state=down"; exit ;;
  shot)    [ -n "${2:-}" ] || { echo "usage: pad.sh <A|B> shot <file.png>" >&2; exit 1; }
           curl -fsS --max-time 15 -o "$2" "http://$HOST:$PORT/api/v1/screen/still" && echo "saved $2"; exit ;;
esac

# A tap has to span at least a couple of emulated frames or the game never samples it.
for b in "$@"; do
  api "/lua/pad?button=$b&state=down" >/dev/null || { echo "press $b failed" >&2; exit 1; }
  sleep "$(awk "BEGIN{print $HOLD_MS/1000}")"
  api "/lua/pad?button=$b&state=up" >/dev/null || { echo "release $b failed" >&2; exit 1; }
  echo "tapped $b"
  sleep 0.25
done
