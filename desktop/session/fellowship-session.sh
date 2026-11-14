# A login on a text console starts the FellowshipOS desktop, the way a
# workstation does — on whichever console autologins or is logged into first.
# SSH logins, another desktop's terminal and root are left alone, and a second
# console login finds the session already running and stays a shell, which is
# the way in when the session needs looking at. FELLOWSHIP_NO_SESSION=1 skips
# it once.
if [ -z "${WAYLAND_DISPLAY:-}" ] && [ -z "${FELLOWSHIP_NO_SESSION:-}" ] &&
   [ "$(id -u)" -ne 0 ] && [ -x /usr/bin/fellowship-session ]; then
    case "$(tty 2>/dev/null)" in
        /dev/tty[0-9]*)
            # Not exec: when the desktop ends, the shell is still there
            /usr/bin/fellowship-session
            ;;
    esac
fi
