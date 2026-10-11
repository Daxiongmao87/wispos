#!/bin/bash
# Dev: get Steam's UI up reliably, ON the device (copy to /storage/wispos/dev/). usage: steam-ensure-ui.sh [MAX_ATTEMPTS=4] [PER_ATTEMPT_SECONDS=150]
# Why: a Steam start sometimes never shows its UI (renderer CPU-bound, no window; the product watchdog restarts it only after ~473 s and at most 3 times per 30 min). Good starts take 81-111 s
# (docs/WISPOS-TIMINGS.md), so a start with no UI-ready marker after 150 s is treated as a stall and restarted at once. Every attempt is appended to /storage/wispos/logs/steam-start-stats.log
# (start time, ok/stall, seconds) so the stall rate can be measured instead of guessed. Service restarts only, never a reboot. The product watchdog is held off while this runs.
# The UI-ready marker is Steam's console log line mentioning Apps.RegisterForAppDetails / GetGameActionDetails after the start time.
MAX=${1:-4}; PER=${2:-150}
L=/storage/wispos/home/.local/share/Steam/logs/console_log.txt; STATS=/storage/wispos/logs/steam-start-stats.log
touch /run/wisp-steam-watch-off; trap 'rm -f /run/wisp-steam-watch-off' EXIT
ready() { awk -v t="$1" 'substr($2,1,8) >= t' $L 2>/dev/null | tr -d '[]' | grep -a -q 'Apps.RegisterForAppDetails\|GetGameActionDetails'; }
# already up? (a start younger than PER with the marker, or any start with the marker)
a=1
while [ $a -le $MAX ]; do
    T0=$(date +%T); t0=$(date +%s)
    systemctl stop wisp-steam; n=0; while [ -n "$(pidof steam)" ] && [ $n -lt 30 ]; do sleep 2; n=$((n+1)); done
    sh /storage/wispos/dev/kill-game.sh >/dev/null 2>&1
    T0=$(date +%T); t0=$(date +%s); systemctl start wisp-steam --no-block
    sleep 30
    while ! ready "$T0"; do
        [ $(( $(date +%s) - t0 )) -gt "$PER" ] && break
        sleep 5
    done
    s=$(( $(date +%s) - t0 ))
    if ready "$T0"; then echo "$(date +%F) $T0 ok ${s}s attempt $a" >> $STATS; echo "steam UI ready after ${s} s (attempt $a, start $T0)"; exit 0; fi
    echo "$(date +%F) $T0 STALL >${s}s attempt $a" >> $STATS; echo "no UI after ${s} s (attempt $a, start $T0): restarting"
    a=$((a+1))
done
echo "steam UI did not come up in $MAX attempts"; exit 3
