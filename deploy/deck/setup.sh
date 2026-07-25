#!/bin/bash
# One-time Deck setup: extract the AppImage so we never depend on FUSE (SteamOS lacks
# libfuse2 by default, and --appimage-extract-and-run would re-unpack 87MB every launch).
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
APP="$DIR/PCSX-Redux-HEAD-x86_64.AppImage"
[ -f "$APP" ] || { echo "MISSING: $APP"; exit 1; }
chmod +x "$APP"
mkdir -p "$DIR/emu"
cd "$DIR/emu"
rm -rf squashfs-root
"$APP" --appimage-extract >/dev/null
echo "[setup] extracted to $DIR/emu/squashfs-root"
"$DIR/emu/squashfs-root/AppRun" --version 2>/dev/null || echo "[setup] (no --version; binary present)"

# A PS1 BIOS and the game discs are copyrighted and are never shipped with this. Say so plainly here
# rather than letting the first launch fail with a missing-file error.
missing=0
if ! ls "$DIR"/bios/*.bin >/dev/null 2>&1; then
  echo "[setup] NEEDED: a PS1 BIOS image in $DIR/bios/ (e.g. scph7001.bin)."
  echo "[setup]         Dump it from your own console. PCSX-Redux also ships OpenBIOS as a free"
  echo "[setup]         alternative, though link play is only tested against a retail BIOS."
  missing=1
fi
if ! ls "$DIR"/roms/*.cue "$DIR"/roms/*.bin >/dev/null 2>&1; then
  echo "[setup] NEEDED: a game disc image in $DIR/roms/ (.cue + .bin, or a .bin we generate a cue for)."
  echo "[setup]         Rip it from your own disc. RetroDeck stores discs .ecm-compressed, which"
  echo "[setup]         PCSX-Redux cannot read — decode those with $DIR/ecm2bin first."
  missing=1
fi
[ "$missing" = 0 ] && echo "[setup] BIOS and disc present."

echo "[setup] done. Launch with: $DIR/ps1-link.sh"
