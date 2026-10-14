#!/bin/bash
# Dev (never shipped), runs on the HOST: age the Steam session the way a day of use does, so UI instability can be measured (docs/WISPOS-NOTES.md "UI instability investigation
# 2026-10-13": a FRESH session is stable for 25+ minutes even with a game stuck; the 16 assert dumps came from an aged, heavily used one). Each cycle: native scale, launch Quake III through
# Steam (os/dev/diag/timedemo-q3.sh answers Steam's dialogs), hold the game HOLD seconds after it appears, kill the game, back to the default scale, rest REST seconds, then record how
# big the Steam client and its web helpers are. The game does not need to finish its timedemo: launching, running and killing a heavy Wine/FEX game is the load.
#   os/dev/diag/ui-aging.sh [--host IP] [--cycles 8] [--hold 60] [--rest 30] [--tag A]
# Output (stdout, also appended to $OUT): one line per event with the host time, `cycle N: ...`. Run it next to os/dev/diag/ui-soak-stream.py (assert dumps, stopped client, pressure).
# A remote command line must not contain the words kill-game.sh matches on (the wine server's name, "reaper SteamLaunch", S:\ ...): it would kill its own ssh shell.
HOST=${WISP_DEV_HOST:?set WISP_DEV_HOST to the handheld address}; KEY=${WISP_DEV_KEY:-$HOME/.ssh/wispos-dev}; CYCLES=8; HOLD=60; REST=30; TAG=A
while [ $# -gt 0 ]; do
    case $1 in
        --host) HOST=$2; shift;; --cycles) CYCLES=$2; shift;; --hold) HOLD=$2; shift;; --rest) REST=$2; shift;; --tag) TAG=$2; shift;;
        *) echo "unknown argument: $1" >&2; exit 2;;
    esac
    shift
done
OUT=${OUT:-/mnt/hdd/wispos-staging/card-logs-$(date +%Y%m%d)/stream/ui-aging-$TAG.log}; mkdir -p "$(dirname "$OUT")"
S() { ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=15 "root@$HOST" "$@"; }
say() { echo "$(date +%T) $*" | tee -a "$OUT"; }
SWAY='export XDG_RUNTIME_DIR=/var/run/0-runtime-dir SWAYSOCK=/var/run/0-runtime-dir/sway-ipc.0.sock'
sizes() {                                                                  # Steam client and web helpers: resident and swapped MB, from /proc/PID/status
    S 'c=$(pidof steam | cut -d" " -f1); [ -n "$c" ] && awk "/VmRSS|VmSwap/{s+=\$2} END{printf \"client_mb=%d \", s/1024}" /proc/$c/status
       for p in /proc/[0-9]*; do [ "$(cat $p/comm 2>/dev/null)" = steamwebhelper ] && awk "/RssAnon|VmSwap/{s+=\$2} END{print s}" $p/status; done | awk "{s+=\$1} END{printf \"helpers_mb=%d \", s/1024}"
       echo "avail_mb=$(awk "/MemAvailable/{print int(\$2/1024)}" /proc/meminfo) zram_logical_mb=$(( $(cut -d" " -f1 /sys/block/zram0/mm_stat) / 1048576 )) dumps=$(ls /tmp/dumps/assert_*.dmp 2>/dev/null | wc -l)" 2>&1' 2>&1
}
say "aging series $TAG: $CYCLES cycles, hold ${HOLD}s, rest ${REST}s, host $HOST"
say "start: $(sizes)"
for i in $(seq 1 "$CYCLES"); do
    S "$SWAY"'; swaymsg "output DSI-1 scale 1" >/dev/null; systemctl reset-failed wisp-q3 2>/dev/null; systemd-run --unit=wisp-q3 --collect bash /storage/wispos/dev/diag/timedemo-q3.sh >/dev/null 2>&1; echo launched' >/dev/null 2>&1 || { say "cycle $i: ssh failed at launch"; sleep 20; continue; }
    t0=$(date +%s); up=0
    while [ $(( $(date +%s) - t0 )) -lt 300 ]; do
        sleep 10
        n=$(S 'pidof quake3.exe | wc -w' 2>/dev/null) || n=err
        [ "$n" = err ] && continue
        [ "${n:-0}" -ge 1 ] && { up=1; break; }
    done
    if [ $up = 1 ]; then say "cycle $i: game up after $(( $(date +%s) - t0 )) s, holding ${HOLD}s"; sleep "$HOLD"; else say "cycle $i: NO GAME within 300 s"; fi
    S 'sh /storage/wispos/dev/diag/kill-game.sh >/dev/null 2>&1' >/dev/null 2>&1 || say "cycle $i: ssh dropped during the kill (expected once if the remote shell was matched)"
    sleep 5
    S "$SWAY"'; swaymsg "output DSI-1 scale 2" >/dev/null; echo ok' >/dev/null 2>&1 || say "cycle $i: ssh failed restoring the scale"
    sleep "$REST"
    say "cycle $i done: $(sizes)"
done
say "aging series $TAG finished"
