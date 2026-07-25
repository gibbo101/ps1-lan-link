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
echo "[setup] done. Launch with: $DIR/ps1-link.sh"
