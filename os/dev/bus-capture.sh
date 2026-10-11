#!/bin/bash
# Dev: record the D-Bus traffic of the device's SYSTEM bus and of Steam's private SESSION bus (Steam runs under dbus-run-session, so its session bus is its own), to see what the UI
# asks the OS for (docs/WISPOS-NOTES.md, "Steam's SteamOS D-Bus calls"). The monitors write to RAM on the device (/run/wisp-dev); nothing goes to the card. Stop copies the files to the
# host HDD and prints a summary.
#   os/dev/bus-capture.sh HOST start             system-bus monitor now; the session-bus monitor as soon as a Steam is up (waits up to 120 s)
#   os/dev/bus-capture.sh HOST stop OUTDIR       stop both, copy to OUTDIR (under /mnt/hdd), summarise with os/dev/diag/bus-summary.py
# The session bus is new at every Steam start: start the capture, THEN restart Steam (os/dev/steam-args.sh) to see the first seconds.
HOST=${1:-}; CMD=${2:-}; OUT=${3:-}
HERE=$(cd "$(dirname "$0")" && pwd)
[ -n "$HOST" ] || { echo "usage: bus-capture.sh HOST start | stop OUTDIR" >&2; exit 2; }
case $CMD in start) ;; stop) case $OUT in /mnt/hdd/*) ;; *) echo "stop needs an OUTDIR under /mnt/hdd (nothing is kept on the root disk)" >&2; exit 2;; esac ;; *) echo "unknown command '$CMD'" >&2; exit 2;; esac
KEY=${WISP_DEV_KEY:-$HOME/.ssh/wispos-dev}
SSH=("${WISP_SSH:-ssh}" -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o UserKnownHostsFile="$HOME/.wispos-dev/known_hosts" -o ConnectTimeout=10 "root@$HOST")
case $CMD in
  start) exec "${SSH[@]}" "sh -s" <<'REMOTE'
D=/run/wisp-dev; mkdir -p $D
for f in system session; do [ -f $D/bus-$f.pid ] && kill $(cat $D/bus-$f.pid) 2>/dev/null; rm -f $D/bus-$f.pid $D/bus-$f.txt; done
(nohup busctl --system monitor > $D/bus-system.txt 2>&1 & echo $! > $D/bus-system.pid)
# the session bus: its address is in Steam's environment; wait for a Steam to exist
n=0; while [ -z "$(pidof steam)" ] && [ $n -lt 120 ]; do sleep 1; n=$((n + 1)); done
p=$(pidof steam | cut -d' ' -f1)
if [ -n "$p" ]; then
    A=$(tr '\0' '\n' < /proc/$p/environ | grep '^DBUS_SESSION_BUS_ADDRESS=' | cut -d= -f2-)
    [ -n "$A" ] && (nohup busctl --address="$A" monitor > $D/bus-session.txt 2>&1 & echo $! > $D/bus-session.pid)
fi
echo "capturing: system bus $(cat $D/bus-system.pid), session bus $(cat $D/bus-session.pid 2>/dev/null || echo 'none (no Steam)')"
REMOTE
  ;;
  stop)
    mkdir -p "$OUT"
    "${SSH[@]}" 'D=/run/wisp-dev; for f in system session; do [ -f $D/bus-$f.pid ] && kill $(cat $D/bus-$f.pid) 2>/dev/null; done; sleep 1' || exit 1
    for f in system session; do "${SSH[@]}" "cat /run/wisp-dev/bus-$f.txt 2>/dev/null" > "$OUT/bus-$f.txt"; done
    python3 "$HERE/diag/bus-summary.py" "$OUT/bus-system.txt" "$OUT/bus-session.txt"
  ;;
esac
