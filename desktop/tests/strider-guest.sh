# Helpers for driving Strider inside the desktop VM, sourced over the serial
# console by vm-strider.py (as the live user, in the running session):
#
#   . /mnt/strider-guest.sh; open_at smb://nas.local/ nas.local; shot nas
#
# Coordinates given to click are relative to Strider's window, so they hold
# wherever the compositor put it. Strider logs to /mnt/out/files.log and
# screenshots land in /mnt/out, both visible on the host.

SOCK=$(ls /run/user/1000/fellowship-wayland-*.sock 2>/dev/null | head -1)
M="timeout 10 fellowship-msg --socket $SOCK"
OUT=/mnt/out
LOG=$OUT/files.log

geom() { $M windows | python3 -c '
import json, sys
w = [x for x in json.load(sys.stdin)["windows"] if x["app_id"] == "org.fellowship.Files"]
print(*(w and (w[0]["x"], w[0]["y"], w[0]["title"]) or ()))'; }
title() { geom | cut -d" " -f3-; }
here() { set -- $(geom); X=$1; Y=$2; }
wait_title() { for _ in $(seq 100); do [ "$(title)" = "$1" ] && return 0; sleep 0.2; done; echo "TITLE-MISMATCH: \"$(title)\" not \"$1\""; return 1; }
keys() { for k in "$@"; do $M key "$k"; sleep 0.12; done; sleep 0.6; }
type_text() { printf '%s\n' "$1" | fold -w1 | while IFS= read -r ch; do
    case $ch in
        [A-Z]) $M key "shift+$(echo "$ch" | tr A-Z a-z)" ;;
        :) $M key shift+semicolon ;;
        ' ') $M key space ;;
        /) $M key slash ;;
        .) $M key period ;;
        -) $M key minus ;;
        _) $M key shift+minus ;;
        @) $M key shift+2 ;;
        *) $M key "$ch" ;;
    esac
    sleep 0.05
done; }
click() { here; $M click left $((X + $1)) $((Y + $2)); sleep 0.5; }
rclick() { here; $M click right $((X + $1)) $((Y + $2)); sleep 0.6; }
shot() { $M screenshot "$OUT/$1.png" >/dev/null && echo "shot $1"; }
logged() { grep -q "$1" "$LOG"; }
until_logged() { for _ in $(seq ${2:-100}); do logged "$1" && return 0; sleep 0.2; done; echo "NOT-LOGGED: $1"; return 1; }
open_at() {
    for pid in $(pgrep -x fellowship-file); do kill "$pid"; done
    sleep 0.5
    $M spawn "env RUST_LOG=info NO_COLOR=1 ${EXTRA:-} fellowship-files $1 >>$LOG 2>&1"
    wait_title "$2"
    sleep 2
    here
}
