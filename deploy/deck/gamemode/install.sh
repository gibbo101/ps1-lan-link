#!/usr/bin/env bash
# Deploy the Game Mode launcher to a Deck, with the emulator binary hash-verified on arrival.
#
# Hash verification is not ceremony: the Decks and this machine have each ended up running a
# different build from the AppImage sitting next to it, and comparing two machines that are not
# running the same code has already cost a session.
#
# Usage: install.sh [host]      host defaults to steamdeck
set -euo pipefail

HOST="${1:-steamdeck}"
PD="$(cd "$(dirname "$0")/../../.." && pwd)"
SRC_BIN="$PD/work/emu/squashfs-root/usr/bin/pcsx-redux"
GM="$PD/deploy/deck/gamemode"
REMOTE=/home/deck/ps1-lan-link

[ -f "$SRC_BIN" ] || { echo "no binary at $SRC_BIN — run tools/install-build.sh first"; exit 1; }
WANT=$(md5sum "$SRC_BIN" | cut -d' ' -f1)
echo "[install] host=$HOST  binary=$WANT"

echo "[install] stopping anything running there"
ssh "deck@$HOST" 'pkill -x AppRun 2>/dev/null; pkill -x pcsx-redux 2>/dev/null; true'

HAVE=$(ssh "deck@$HOST" "md5sum $REMOTE/emu/squashfs-root/usr/bin/pcsx-redux 2>/dev/null | cut -d' ' -f1")
if [ "$HAVE" != "$WANT" ]; then
  echo "[install] binary differs (deck has ${HAVE:-none}) — copying"
  ssh "deck@$HOST" "cp $REMOTE/emu/squashfs-root/usr/bin/pcsx-redux $REMOTE/emu/squashfs-root/usr/bin/pcsx-redux.bak 2>/dev/null; true"
  scp -q "$SRC_BIN" "deck@$HOST:$REMOTE/emu/squashfs-root/usr/bin/pcsx-redux"
  GOT=$(ssh "deck@$HOST" "md5sum $REMOTE/emu/squashfs-root/usr/bin/pcsx-redux | cut -d' ' -f1")
  [ "$GOT" = "$WANT" ] || { echo "[install] FAILED: deck has $GOT, expected $WANT"; exit 1; }
  echo "[install] binary verified $GOT"
else
  echo "[install] binary already matches"
fi

echo "[install] copying launcher"
ssh "deck@$HOST" "mkdir -p $REMOTE/gamemode $REMOTE/stream"
scp -q "$GM/ps1-link-gamemode.sh" "$GM/instb-ctl.lua" "$GM/pad.sh" "deck@$HOST:$REMOTE/gamemode/"
ssh "deck@$HOST" "chmod +x $REMOTE/gamemode/ps1-link-gamemode.sh $REMOTE/gamemode/pad.sh"

echo "[install] copying the streamer"
scp -q "$PD/deploy/deck/stream/stream-host.py" "deck@$HOST:$REMOTE/stream/"
ssh "deck@$HOST" "chmod +x $REMOTE/stream/stream-host.py"

echo "[install] copying the menu and unified launcher"
ssh "deck@$HOST" "mkdir -p $REMOTE/menu"
[ -x "$PD/deploy/deck/menu/ps1-link-menu" ] || { echo "[install] menu binary missing — run deploy/deck/menu/build.sh"; exit 1; }
scp -q "$PD/deploy/deck/menu/ps1-link-menu" "deck@$HOST:$REMOTE/menu/"
scp -q "$PD/deploy/deck/games.conf" "deck@$HOST:$REMOTE/games.conf"

# The Steam shortcut "PS1 LAN Link" already points at ps1-link.sh. Taking that name over is what
# lets the unified app launch from the existing Game Mode entry without editing Steam's
# shortcuts.vdf. A previous foreign script is kept beside it; restoring it is one mv.
ssh "deck@$HOST" bash -s <<EOF
set -e
cd $REMOTE
if [ -f ps1-link.sh ] && ! grep -q 'gamemode/ps1-link-gamemode.sh' ps1-link.sh; then
  mv ps1-link.sh ps1-link-netpeer.sh
  echo "[install] kept previous launcher as ps1-link-netpeer.sh"
fi
chmod +x menu/ps1-link-menu
[ -f gamemode/gamemode.conf ] || printf 'GAME=retaliation\nPAD_ID=0\nFULLSCREEN=1\nCTL=1\nLINK_LOCAL_ACK=1\nLINK_ACK_WINDOW=16\n' > gamemode/gamemode.conf
# Existing installs keep their conf but must gain the validated link tuning (2026-08-01: the ack
# with window 16 is what holds the streamed side at full speed).
grep -q '^LINK_LOCAL_ACK=' gamemode/gamemode.conf || echo 'LINK_LOCAL_ACK=1' >> gamemode/gamemode.conf
grep -q '^LINK_ACK_WINDOW=' gamemode/gamemode.conf || echo 'LINK_ACK_WINDOW=16' >> gamemode/gamemode.conf
EOF
scp -q "$PD/deploy/deck/ps1-link-unified.sh" "deck@$HOST:$REMOTE/ps1-link.sh"
ssh "deck@$HOST" "chmod +x $REMOTE/ps1-link.sh"

echo "[install] done. Available discs there:"
ssh "deck@$HOST" "ls $REMOTE/roms/*.cue 2>/dev/null | xargs -n1 basename"
echo
echo "Launch 'PS1 LAN Link' from the Game Mode library, then watch:"
echo "  ssh deck@$HOST tail -f $REMOTE/gamemode/gamemode.log"
