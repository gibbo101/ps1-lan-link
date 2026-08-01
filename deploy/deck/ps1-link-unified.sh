#!/usr/bin/env bash
# The unified app. One Steam shortcut on every Deck: the menu picks a role and a game, the
# existing stage scripts run the session, and when the session ends the menu comes back.
#
# The menu binary decides nothing - it prints the payload of the chosen entry and exits. This
# script owns the mapping from payloads to the tested host/join paths, so the session plumbing
# stays exactly what has been validated in play.
#
# A Deck with no roms (a pure joiner) simply gets "Host a game" greyed out: the same app is
# installed everywhere and capability comes from what is on disk, not from which shortcut was
# clicked. Installed as ps1-link.sh by the installers.
set -uo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
MENU="$DIR/menu/ps1-link-menu"
CATALOG="$DIR/games.conf"

# Half-deployed Deck (no menu binary yet): behave exactly as the app always did.
[ -x "$MENU" ] || exec "$DIR/gamemode/ps1-link-gamemode.sh" "$@"

catalog_lookup() {  # $1 = cue basename; prints "label|status", empty if unlisted
  [ -f "$CATALOG" ] || return 0
  awk -F'|' -v b="$1" '/^[^#]/ && $2 == b { print $1 "|" $3; exit }' "$CATALOG"
}

build_entries() {  # one menu argument per line
  local cue base label status line
  for cue in "$DIR"/roms/*.cue; do
    [ -e "$cue" ] || continue
    base="$(basename "$cue")"
    line="$(catalog_lookup "$base")"
    label="${line%%|*}"
    status="${line##*|}"
    [ -n "$label" ] || { label="${base%.cue}"; status=ok; }
    case "$status" in
      hidden) ;;
      broken) printf '!%s (link broken)|\n' "$label" ;;
      *)      printf '%s|HOST %s\n' "$label" "$cue" ;;
    esac
  done
}

while :; do
  mapfile -t ENTRIES < <(build_entries)
  choice="$("$MENU" ${ENTRIES[@]+"${ENTRIES[@]}"})" || exit 0
  case "$choice" in
    HOST\ *)
      cue="${choice#HOST }"
      base="$(basename "$cue" .cue)"
      label="$(catalog_lookup "$base.cue")"
      label="${label%%|*}"
      # The beacon carries this name to every joiner's menu, so it should be the display name.
      GAME_LABEL="${label:-$base}" "$DIR/gamemode/ps1-link-gamemode.sh" "$cue"
      ;;
    JOIN\ *)
      HOST="${choice#JOIN }" "$DIR/stream/stream-join.sh"
      ;;
  esac
done
