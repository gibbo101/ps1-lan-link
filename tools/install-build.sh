#!/usr/bin/env bash
# Install a freshly compiled pcsx-redux binary into the extracted AppImage tree that both this
# machine and the Deck actually run.
#
# Two things make this more than a copy:
#  - `make appimage` cannot finish in the build container (no FUSE), so only the raw binary is
#    produced; the extracted tree from an earlier successful AppImage supplies the bundled libs.
#  - linuxdeploy patches RUNPATH=$ORIGIN/../lib into the binary it bundles. A raw binary has no
#    RUNPATH and dies on libcapstone.so.4, so the same patch is reapplied here.
set -euo pipefail
PD="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$PD/pcsx-redux/pcsx-redux"
TREE="$PD/work/emu/squashfs-root"
DST="$TREE/usr/bin/pcsx-redux"
IMAGE=ghcr.io/grumpycoders/pcsx-redux-build

[ -f "$SRC" ] || { echo "no built binary at $SRC — run the docker build first"; exit 1; }
[ -d "$TREE/usr/lib" ] || { echo "no extracted tree at $TREE"; exit 1; }

# A running instance makes the copy fail with ETXTBSY, and a silently skipped install means the next
# test measures the previous build. Stop them and wait for the descriptors to go.
pkill -x AppRun 2>/dev/null || true
pkill -x pcsx-redux 2>/dev/null || true
timeout 10 bash -c 'while pgrep -x AppRun >/dev/null || pgrep -x pcsx-redux >/dev/null; do sleep 0.2; done' || true

cp "$SRC" "$DST"
# Plain `make` keeps full debug info, which makes the binary ~358 MB instead of ~14 MB and turns
# every Deck deploy into a multi-minute copy. The diagnostics are printf-based, so nothing here
# needs symbols.
docker run --rm --user "$(id -u):$(id -g)" -v "$TREE":/t "$IMAGE" bash -c \
  'strip /t/usr/bin/pcsx-redux && patchelf --set-rpath "\$ORIGIN/../lib" /t/usr/bin/pcsx-redux'
objdump -p "$DST" | grep -q 'RUNPATH.*ORIGIN/../lib' || { echo "RUNPATH patch did not stick"; exit 1; }
echo "installed $(stat -c%y "$DST") -> $DST"
