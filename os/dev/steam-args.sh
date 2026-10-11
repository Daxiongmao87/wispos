#!/bin/bash
# Dev: restart the Steam session with EXTRA command-line switches, to find out how Steam behaves when it is told it is on SteamOS (or given a Chromium switch), without rebuilding the image.
#   os/dev/steam-args.sh HOST show
#   os/dev/steam-args.sh HOST set "-steamos3 -steamdeck"      restart Steam with those switches added to the session's command line
#   os/dev/steam-args.sh HOST reset                           back to the shipped command line (restarts Steam)
# How: the session script lives in the read-only system image, so a COPY with the extra switches goes to RAM (/run/wisp-dev) and is bind-mounted over /usr/bin/wisp-steam-session; nothing is
# written to the card and a reboot undoes it. The product watchdog is switched off for the restart (as set-launch-option.sh does) and on again afterwards. Steam is down for about 3 minutes.
# Why (operator, 2026-10-11): the UI shows a "cast"-like icon where SteamOS and Bazzite show their Wi-Fi icon, which suggests Steam configures itself differently here; Steam's own binary
# recognizes -steamdeck, -steamos3 and -steamos, and a family of -cef-* switches (-cef-disable-gpu, -cef-force-gpu, -cef-use-vulkan ...). This script is the test lever; whatever helps becomes a
# change to wisp-steam-session after the usual checks, never a per-game setting.
HOST=${1:-}; CMD=${2:-show}; ARGS=${3:-}
[ -n "$HOST" ] || { echo "usage: steam-args.sh HOST show|set \"-switch ...\"|reset" >&2; exit 2; }
case $CMD in show|reset) ;; set) [ -n "$ARGS" ] || { echo "set needs the switches" >&2; exit 2; };; *) echo "unknown command $CMD" >&2; exit 2;; esac
case $ARGS in *[!A-Za-z0-9\ =._:/,-]*) echo "refusing switches with shell-special characters: $ARGS" >&2; exit 2;; esac
KEY=${WISP_DEV_KEY:-$HOME/.ssh/wispos-dev}
exec "${WISP_SSH:-ssh}" -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o UserKnownHostsFile="$HOME/.wispos-dev/known_hosts" -o ConnectTimeout=10 "root@$HOST" "CMD=$CMD ARGS='$ARGS' sh -s" <<'REMOTE'
D=/run/wisp-dev; SCRIPT=/usr/bin/wisp-steam-session
cmdline() { tr '\0' ' ' < /proc/$(pidof steam | cut -d' ' -f1)/cmdline 2>/dev/null; }
mounted() { grep -q " $SCRIPT " /proc/mounts; }
# The running session script holds the mounted file open, so the service is stopped BEFORE the mount is changed, then started again.
stop_steam() {
    touch /run/wisp-steam-watch-off
    systemctl stop wisp-steam
    n=0; while [ -n "$(pidof steam)" ] && [ $n -lt 60 ]; do sleep 1; n=$((n + 1)); done
}
start_steam() {
    systemctl start wisp-steam
    n=0; while [ -z "$(pidof steam)" ] && [ $n -lt 60 ]; do sleep 1; n=$((n + 1)); done
    rm -f /run/wisp-steam-watch-off
    echo "steam started: $(cmdline)"
}
case $CMD in
    show) echo "session script: $(mounted && echo 'patched copy (RAM) bind-mounted' || echo 'shipped')"; echo "steam: $(cmdline)";;
    set)
        mkdir -p $D
        stop_steam
        mounted && umount $SCRIPT
        cp $SCRIPT $D/wisp-steam-session.orig
        # the steam exec line: add the extra switches right after -gamepadui
        sed "s|-gamepadui -clientbeta publicbeta|-gamepadui $ARGS -clientbeta publicbeta|" $D/wisp-steam-session.orig > $D/wisp-steam-session
        chmod 755 $D/wisp-steam-session
        if grep -q -- "-gamepadui $ARGS" $D/wisp-steam-session && mount --bind $D/wisp-steam-session $SCRIPT; then start_steam; else echo "could not patch or mount the session script"; start_steam; exit 1; fi;;
    reset)
        stop_steam
        mounted && umount $SCRIPT
        mounted && { echo "still mounted: not restarting a patched session"; exit 1; }
        rm -f $D/wisp-steam-session $D/wisp-steam-session.orig
        start_steam;;
esac
REMOTE
