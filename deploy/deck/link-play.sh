#!/usr/bin/env bash
# Link-cable play for any PS1 game, both sides hosted on this Deck.
#
# Both emulators run here and link over loopback, which is the only channel whose round trip fits
# inside a frame: the driver needs ~182 ordered round trips per payload, so a real network is ~30x
# too slow. Nothing here is game-specific — SIO1 is emulated generically.
#
# Usage: link-play.sh [game]
#          game = a .cue, or a .bin/.img (a cue is generated beside it in roms/), or a substring
#                 matched against the library. With no argument, lists what is available.
set -uo pipefail

DIR="$HOME/ps1-lan-link"
BIN="$DIR/emu/squashfs-root/AppRun"
BIOS="$DIR/bios/scph7001.bin"
ROMS="$DIR/roms"
LIB="$HOME/retrodeck/roms/psx"
PORT=6699

export DISPLAY="${DISPLAY:-:0}"
export XAUTHORITY="${XAUTHORITY:-/run/user/1000/xauth_rDAMDB}"

die() { echo "ERROR: $*" >&2; exit 1; }
[ -x "$BIN" ] || die "emulator missing: $BIN"
[ -f "$BIOS" ] || die "BIOS missing: $BIOS"

list_games() {
  echo "Ready to play (in $ROMS):"
  for c in "$ROMS"/*.cue; do [ -e "$c" ] && echo "  $(basename "$c" .cue)"; done
  echo
  echo "In the library that could be added ($LIB):"
  for f in "$LIB"/*.bin "$LIB"/*.img; do
    [ -e "$f" ] || continue
    echo "  $(basename "$f")"
  done
  echo
  echo "ECM-compressed discs must be decoded first — PCSX-Redux cannot load .ecm:"
  echo "  $DIR/ecm2bin <disc.ecm> $ROMS/<name>.bin"
}

# A cue sheet is required; these images are single-track MODE2/2352, the PS1 norm.
make_cue() {
  local bin="$1" cue="$2" sz
  sz=$(stat -c%s "$bin")
  [ $((sz % 2352)) -eq 0 ] || die "$(basename "$bin") is not a multiple of 2352 bytes — unknown layout"
  printf 'FILE "%s" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n' "$bin" > "$cue"
  echo "[link-play] generated $(basename "$cue")"
}

resolve_game() {
  local want="$1"
  [ -f "$want" ] && case "$want" in
    *.cue) echo "$want"; return ;;
    *.bin|*.img) local c="$ROMS/$(basename "${want%.*}").cue"; make_cue "$want" "$c" >&2; echo "$c"; return ;;
  esac
  local hit
  hit=$(ls "$ROMS"/*.cue 2>/dev/null | grep -i -- "$want" | head -1)
  [ -n "$hit" ] && { echo "$hit"; return; }
  hit=$(ls "$LIB"/*.bin "$LIB"/*.img 2>/dev/null | grep -i -- "$want" | head -1)
  [ -n "$hit" ] || die "no game matching '$want' (run with no arguments to list)"
  local c="$ROMS/$(basename "${hit%.*}" | tr ' ' '-').cue"
  make_cue "$hit" "$c" >&2
  echo "$c"
}

[ $# -eq 0 ] && { list_games; exit 0; }
CUE=$(resolve_game "$1") || exit 1
echo "[link-play] game: $(basename "$CUE")"

# Steam Input consumes the Deck's own controls in Desktop Mode and never feeds its virtual pads, and
# keyboard events do not reach the emulator under Xwayland — so drive it with our own uinput pads.
if ! systemctl --user is-active --quiet ps1-pads; then
  systemctl --user reset-failed ps1-pads 2>/dev/null
  systemd-run --user --collect --unit=ps1-pads python3 "$DIR/virtual-pads.py" >/dev/null 2>&1
  sleep 3
  echo "[link-play] virtual pads started"
fi

# Restart both together: a lone restart leaves the survivor holding a CLOSE-WAIT socket to the dead
# peer and it never sees the new one, whose symptom is no LINK GAME entry on the main menu.
for i in A B; do systemctl --user stop "ps1-inst$i" 2>/dev/null; done
pkill -x AppRun 2>/dev/null
for _ in $(seq 1 30); do pgrep -x AppRun >/dev/null || break; sleep 1; done
for i in A B; do systemctl --user reset-failed "ps1-inst$i" 2>/dev/null; done

# pcsx.json is rewritten on exit, so pad ids are only safe to set while stopped. GLFW does not accept
# Steam's phantom pads as gamepads, which is why ours land on indices 2 and 3.
python3 - "$DIR" <<'PY'
import json, sys
base = sys.argv[1]
for inst, server, pad in (("instA", True, 2), ("instB", False, 3)):
    cfg = f"{base}/dual/{inst}/.config/pcsx-redux/pcsx.json"
    d = json.load(open(cfg))
    def setkey(o, k, v):
        if isinstance(o, dict):
            for kk, vv in o.items():
                if kk == k: o[kk] = v
                elif isinstance(vv, (dict, list)): setkey(vv, k, v)
        elif isinstance(o, list):
            for it in o: setkey(it, k, v)
    setkey(d, "SIO1Server", server)
    setkey(d, "SIO1Client", not server)
    setkey(d, "SIO1Clienthost", "127.0.0.1")
    setkey(d, "SIO1ClientPort", 6699)
    setkey(d, "SIO1ServerPort", 6699)
    d["pads"][0]["ID"] = pad
    d["pads"][0]["Connected"] = True
    g = d.setdefault("gui", {})
    g["WindowSizeX"], g["WindowSizeY"] = 636, 700
    g["WindowPosX"], g["WindowPosY"] = (0 if server else 642), 28
    g["Fullscreen"] = False
    json.dump(d, open(cfg, "w"), indent=2)
PY

for i in A B; do
  D2="$DIR/dual/inst$i"; : > "$D2/run.log"
  systemd-run --user --collect --unit="ps1-inst$i" \
    --setenv=HOME="$D2" --setenv=XDG_CONFIG_HOME="$D2/.config" \
    --setenv=DISPLAY="$DISPLAY" --setenv=XAUTHORITY="$XAUTHORITY" \
    --working-directory="$D2" \
    bash -c "exec stdbuf -o0 -e0 '$BIN' -stdout -bios '$BIOS' -iso '$CUE' -run > '$D2/run.log' 2>&1" \
    >/dev/null 2>&1 || die "failed to start inst$i"
  sleep 7
done
sleep 18

# The window manager places by its own rules, so tile explicitly once both windows exist.
for w in $(xdotool search --class pcsx 2>/dev/null); do
  pid=$(xdotool getwindowpid "$w" 2>/dev/null) || continue
  inst=$(tr '\0' '\n' < "/proc/$pid/environ" | sed -n 's|^HOME=.*/||p')
  xdotool windowstate --remove MAXIMIZED_VERT --remove MAXIMIZED_HORZ "$w" 2>/dev/null
  sleep 0.4
  xdotool windowsize "$w" 636 700
  [ "$inst" = instA ] && xdotool windowmove "$w" 0 28 || xdotool windowmove "$w" 642 28
done

echo "[link-play] link sockets: $(ss -tn 2>/dev/null | grep -c $PORT)  (2 = connected)"
cat <<'EOF'
[link-play] ready.

  LEFT window  = instA, gamepad 0   |   RIGHT window = instB, gamepad 1

Drive with the pads, or from a shell:  echo "0 start" > /tmp/padctl
  buttons: start select cross circle square triangle l1 r1 up down left right

  1. each side: start -> title -> start -> main menu
  2. LINK GAME only appears when the peer is connected; up wraps to it, then cross
  3. both sides press cross at COUNTRY/COLOR (START SETUP)
  4. the side showing "X TO START" presses cross again

Main menus fall back to the attract loop when idle; COUNTRY/COLOR does not, so park one
side there while driving the other.
EOF
