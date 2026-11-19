# Strider against a real FellowshipNAS and a USB stick, inside the desktop
# VM. vm-strider.py runs it phase by phase (`net`, then `usb` once the stick
# is plugged in); it prints "ok:" for each check and "FAILED:" for the first
# that doesn't hold. Needs the NAS from vm-strider.py's notes: share "docs"
# for alice (password "alice pw 1"), holding hello.txt.
#
#   . /mnt/strider-guest.sh; . /mnt/strider-vm-test.sh; phase_net

PW="alice pw 1"
# GVfs names the share's FUSE folder from how it was mounted.
share_dir() { ls -d /run/user/1000/gvfs/smb-share:*share=docs* 2>/dev/null | head -1; }
fail() { echo "FAILED: $*"; return 1; }
ok() { echo "ok: $*"; }
asked() { grep -c "password asked" "$LOG"; }
# Answer a password prompt if one comes up; the password field has focus.
answer() {
    before=$1
    for _ in $(seq 30); do
        [ "$(asked)" -gt "$before" ] && { sleep 0.5; type_text "$PW"; keys enter; return 0; }
        logged "$2" && return 0
        sleep 0.2
    done
}
go() { keys ctrl+l; keys ctrl+a; type_text "$1"; keys enter; }

phase_net() {
    : > "$LOG"
    rm -f ~/.config/gtk-3.0/bookmarks ~/fromdesk.txt
    open_at network:/// Network || return 1
    until_logged 'place shown place=network:/// items=\["nas-verify"\]' 100 || return 1
    ok "Network lists the NAS, found by its _fellowship-nas._tcp advert, at once"

    n=$(asked); go smb://alice@nas-verify.local/; answer "$n" "listed uri=smb://"
    # GIO gives the place back without the user name.
    until_logged 'place shown place=smb://[a-z@]*nas-verify.local/ items=\[.*"docs"' 100 || return 1
    ok "the NAS lists its shares once alice signs in"

    n=$(asked); go smb://alice@nas-verify.local/docs/; answer "$n" "folder shown folder=/run/user/1000/gvfs/smb-share"
    until_logged "folder shown folder=/run/user/1000/gvfs/smb-share:.*share=docs" 100 || return 1
    SHARE=$(share_dir)
    [ "$(title)" = "docs on nas-verify.local" ] || fail "title is $(title)" || return 1
    ok "docs opens as an ordinary folder through GVfs's FUSE bridge"

    echo from the desktop > ~/fromdesk.txt
    go /home/user; sleep 1; type_text fromdesk; keys ctrl+c
    keys alt+left; sleep 2; keys ctrl+v
    for _ in $(seq 50); do [ -f "$SHARE/fromdesk.txt" ] && break; sleep 0.2; done
    [ "$(cat "$SHARE/fromdesk.txt" 2>/dev/null)" = "from the desktop" ] || fail "the copy didn't reach the share" || return 1
    ok "a file copies onto the share"
    keys ctrl+z; sleep 1; keys enter
    for _ in $(seq 50); do [ -e "$SHARE/fromdesk.txt" ] || break; sleep 0.2; done
    [ ! -e "$SHARE/fromdesk.txt" ] || fail "undo didn't take the copy off the share" || return 1
    logged "Undo “Copy” by deleting" || true
    ok "Ctrl+Z asks, then deletes the copy (the share has no Trash)"

    keys ctrl+d; sleep 1
    grep -q "^smb://[a-z@]*nas-verify.local/docs/ docs on nas-verify.local$" ~/.config/gtk-3.0/bookmarks || fail "bookmark: $(cat ~/.config/gtk-3.0/bookmarks)" || return 1
    ok "Ctrl+D bookmarks the share by its address"
    shot vmtest-share
}

phase_usb() {
    open_at computer:/// Computer || return 1
    until_logged 'place shown place=computer:/// items=\[.*"STRIDERUSB"' 100 || return 1
    ok "the stick is listed in Computer"
    type_text strider; keys enter
    until_logged "folder shown folder=/media/user/STRIDERUSB" 100 || return 1
    [ "$(cat /media/user/STRIDERUSB/note.txt)" = "on the stick" ] || fail "the stick's file isn't readable" || return 1
    ok "opening it mounts it through UDisks and polkit, and shows it"
    shot vmtest-usb
}
