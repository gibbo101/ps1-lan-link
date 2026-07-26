#!/usr/bin/env bash
# Builds the joiner's player for SteamOS, from a machine that is not SteamOS.
#
# A Deck has no compiler, so this cross-builds in a container. The catch is the soname: SteamOS
# 3.7.24 ships ffmpeg 7.1 (libavcodec.so.61, libavutil.so.59) while a current Arch container builds
# against 7.2 (.62/.60), and a binary linked to those simply will not start on a Deck. So the build
# links against ffmpeg 7.1 pulled from the Arch archive, which is the same series the Deck runs.
# Nothing is bundled - the binary uses the libraries the Deck already has.
#
# SDL2 is taken from the current container: its soname (libSDL2-2.0.so.0) has been stable for years
# and matches what SteamOS ships.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
OUT="$DIR/ps1-join-player"
IMAGE=archlinux:base-devel
FFMPEG_PKG="ffmpeg-2:7.1.1-5-x86_64.pkg.tar.zst"

docker run --rm --user root -v "$DIR":/w -w /w "$IMAGE" bash -c "
  set -e
  pacman -Sy --noconfirm --needed sdl2 pkgconf curl >/dev/null 2>&1
  mkdir -p /ff && cd /ff
  curl -sO 'https://archive.archlinux.org/packages/f/ffmpeg/${FFMPEG_PKG}'
  # --force-local: the epoch's colon in 'ffmpeg-2:7.1.1' otherwise reads as a remote host spec.
  tar --force-local --use-compress-program=unzstd -xf '${FFMPEG_PKG}'
  cd /w
  gcc -O2 -Wall -Wextra -o ps1-join-player ps1-join-player.c \
    -I/ff/usr/include \$(pkg-config --cflags sdl2) \
    -L/ff/usr/lib -lavcodec -lavutil \$(pkg-config --libs sdl2) \
    -Wl,--allow-shlib-undefined
  chown $(id -u):$(id -g) ps1-join-player
"

echo "built $OUT"
echo
echo "needs these sonames from the Deck:"
objdump -p "$OUT" | awk '/NEEDED/ && /libav|libSDL2/ {print "  " $2}'
