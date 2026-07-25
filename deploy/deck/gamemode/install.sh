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
ssh "deck@$HOST" "mkdir -p $REMOTE/gamemode"
scp -q "$GM/ps1-link-gamemode.sh" "$GM/instb-ctl.lua" "$GM/pad.sh" "deck@$HOST:$REMOTE/gamemode/"
ssh "deck@$HOST" "chmod +x $REMOTE/gamemode/ps1-link-gamemode.sh $REMOTE/gamemode/pad.sh"

# The Steam shortcut "PS1 LAN Link" already points at ps1-link.sh. Taking that name over is what
# lets Stage 1 launch from the existing Game Mode entry without editing Steam's shortcuts.vdf.
# The previous script is kept beside it; restoring it is one mv.
ssh "deck@$HOST" bash -s <<EOF
set -e
cd $REMOTE
if [ -f ps1-link.sh ] && ! grep -q 'gamemode/ps1-link-gamemode.sh' ps1-link.sh; then
  mv ps1-link.sh ps1-link-netpeer.sh
  echo "[install] kept previous launcher as ps1-link-netpeer.sh"
fi
cat > ps1-link.sh <<'SHIM'
#!/usr/bin/env bash
# Steam shortcut target. Stage 1 of the unified app lives in gamemode/.
exec "\$(dirname "\$0")/gamemode/ps1-link-gamemode.sh" "\$@"
SHIM
chmod +x ps1-link.sh
[ -f gamemode/gamemode.conf ] || printf 'GAME=retaliation\nPAD_ID=0\nFULLSCREEN=1\nCTL=1\n' > gamemode/gamemode.conf
EOF

echo "[install] done. Available discs there:"
ssh "deck@$HOST" "ls $REMOTE/roms/*.cue 2>/dev/null | xargs -n1 basename"
echo
echo "Launch 'PS1 LAN Link' from the Game Mode library, then watch:"
echo "  ssh deck@$HOST tail -f $REMOTE/gamemode/gamemode.log"
