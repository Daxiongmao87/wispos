#!/bin/sh
# Experiment: SIGSTOP Steam's web helper processes while the game runs, always SIGCONT afterwards (also by a safety timer).
H="/storage/wispos/runtime/steam/steamapps/compatdata/63710/hud/frametime_(ms)"
PIDS=$(pidof steamwebhelper)
( sleep 110; kill -CONT $PIDS 2>/dev/null ) >/dev/null 2>&1 &
snap() { echo "$(date +%T) $1: avail=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)MB psi_full=$(awk '/^full/{print $2}' /proc/pressure/memory) zram0=$(awk '/zram0/{print int($4/1024)}' /proc/swaps)MB last8 frametimes(ms): $(tail -8 "$H" | tr '\n' ' ')"; }
snap "before"; kill -STOP $PIDS; echo "$(date +%T) stopped $(echo $PIDS | wc -w) steamwebhelper processes"
for i in 1 2 3 4 5 6; do sleep 14; snap "UI paused +$((i*14))s"; done
kill -CONT $PIDS; echo "$(date +%T) resumed"; sleep 6; snap "after resume"; echo FREEZEDONE
