#!/usr/bin/env bash
# Deploy the joiner to the second Deck: the player who watches instB and drives it.
#
# The joiner needs none of the emulator — no binary, no BIOS, no ROM. It decodes a stream and
# sends pad state back, which is the whole point of the split: the machine that owns the game is
# the one that owns the game's files.
#
# Usage: install-joiner.sh <host> <host-address>
#        install-joiner.sh deck2 10.0.0.1
set -euo pipefail

HOST="${1:-deck2}"
HOST_ADDR="${2:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
REMOTE=/home/deck/ps1-lan-link

[ -n "$HOST_ADDR" ] || { echo "usage: $0 <ssh-host> <address-of-the-hosting-deck>"; exit 1; }
echo "[joiner] host=$HOST  joining=$HOST_ADDR"

# ffplay does the decoding and the fullscreen window. Without it there is nothing to install onto.
ssh "deck@$HOST" 'command -v ffplay >/dev/null' || { echo "[joiner] ffplay missing on $HOST"; exit 1; }
ssh "deck@$HOST" 'command -v python3 >/dev/null' || { echo "[joiner] python3 missing on $HOST"; exit 1; }

ssh "deck@$HOST" "mkdir -p $REMOTE/stream"
scp -q "$HERE/stream-join.sh" "$HERE/pad-forward.py" "deck@$HOST:$REMOTE/stream/"
# The player is optional: a Deck without it falls back to ffplay, which is why this does not fail
# the install when the binary has not been built yet.
[ -x "$HERE/player/ps1-join-player" ] && scp -q "$HERE/player/ps1-join-player" "deck@$HOST:$REMOTE/stream/"
ssh "deck@$HOST" "chmod +x $REMOTE/stream/stream-join.sh $REMOTE/stream/pad-forward.py"

# Written rather than copied, so the address is recorded on the machine that has to use it.
ssh "deck@$HOST" "cat > $REMOTE/stream/join.conf" <<EOF
# Which host to join. An address here is deliberate for now: what identifies a host is the one
# choice the wire format bakes in, so it stays explicit until stage 3 replaces it with LAN
# discovery.
HOST=$HOST_ADDR
PORT=6690
INPUT_PORT=6691
# PAD_DEVICE=/dev/input/js0   # set only if the wrong pad is picked up
EOF

# A Steam shortcut, not an SSH command: in Game Mode gamescope composites one app, so a player
# needs the joiner launched *as* the game or it is neither presented nor given a pad by Steam Input.
scp -q "$HERE/ps1-lan-link-join.desktop" "deck@$HOST:$REMOTE/stream/"
if ssh "deck@$HOST" "command -v steamos-add-to-steam >/dev/null"; then
  ssh "deck@$HOST" "steamos-add-to-steam $REMOTE/stream/ps1-lan-link-join.desktop" \
    && echo "[joiner] registered 'PS1 LAN Link — Join' in the Steam library" \
    || echo "[joiner] WARNING: steamos-add-to-steam failed — add the shortcut by hand in Game Mode"
else
  echo "[joiner] steamos-add-to-steam missing — add $REMOTE/stream/ps1-lan-link-join.desktop by hand"
fi

echo "[joiner] pads visible there:"
ssh "deck@$HOST" 'ls /dev/input/js* 2>/dev/null || echo "  none — connect a controller or use the Deck built-ins in Game Mode"'

echo
echo "Start the host first, then on $HOST run:"
echo "  ssh deck@$HOST $REMOTE/stream/stream-join.sh"
