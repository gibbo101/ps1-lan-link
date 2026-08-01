#!/usr/bin/env bash
# Builds the menu for SteamOS, from a machine that is not SteamOS.
#
# Unlike the player this links only SDL2 and SDL2_ttf, both of which have had stable sonames for
# years and ship on the Deck (libSDL2-2.0.so.0, libSDL2_ttf-2.0.so.0), so the current Arch
# container's libraries are fine — no archive pinning needed.
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
IMAGE=archlinux:base-devel

docker run --rm --user root -v "$DIR":/w -w /w "$IMAGE" bash -c "
  set -e
  pacman -Syu --noconfirm --needed sdl2 sdl2_ttf pkgconf >/dev/null 2>&1
  gcc -O2 -Wall -Wextra -o ps1-link-menu ps1-link-menu.c \
    \$(pkg-config --cflags --libs sdl2 SDL2_ttf)
  chown $(id -u):$(id -g) ps1-link-menu
"

echo "built $DIR/ps1-link-menu"
objdump -p "$DIR/ps1-link-menu" | awk '/NEEDED/ {print "  " $2}'
