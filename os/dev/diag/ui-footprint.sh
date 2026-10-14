#!/bin/sh
# Dev (never shipped), runs ON the device: what does the Steam UI COST, in CPU time and memory, idle and under a fixed, repeatable navigation? (operator 2026-10-14: flags that cut the UI's CPU and
# RAM are worth keeping for gaming even if they do not cure the stutter.) The load is the dev pad pressing right x4 / left x4 with a fixed gap, so every arm gets the same navigation; nobody must
# touch the device meanwhile. "The UI" = every process in the wisp-ui cgroup (the web helpers). Output, two lines:
#   FOOTPRINT idle  cpu_core_pct=<UI CPU time over the phase as % of one core> cgroup_mb=<memory.current> rss_mb=<sum of RSS> swapped_mb=<sum of VmSwap> mem_avail_mb=<MemAvailable> swap_used_mb=<system>
#   FOOTPRINT nav   the same, over the navigation phase (rss/swapped/cgroup read at its end)
#   both lines end with wait_ms/cpu_ms per group over the phase: Xwayland, the Steam client, sway and the UI (summed over threads; wait = time runnable but not running)
# usage: ui-footprint.sh [IDLE_S=30] [NAV_ROUNDS=8]
export XDG_RUNTIME_DIR=/var/run/0-runtime-dir SWAYSOCK=/var/run/0-runtime-dir/sway-ipc.0.sock WAYLAND_DISPLAY=wayland-1
P=/storage/wispos/dev/wisp-dev-pad; CG=/sys/fs/cgroup/unified/wisp-ui; IDLE=${1:-30}; ROUNDS=${2:-8}
ticks() { t=0; for p in $(cat $CG/cgroup.procs); do set -- $(cut -d' ' -f14,15 /proc/$p/stat 2>/dev/null); t=$((t + ${1:-0} + ${2:-0})); done; echo $t; }
mem() {
    rss=0; sw=0
    for p in $(cat $CG/cgroup.procs); do
        r=$(awk '/^VmRSS/ {print $2}' /proc/$p/status 2>/dev/null); s=$(awk '/^VmSwap/ {print $2}' /proc/$p/status 2>/dev/null); rss=$((rss + ${r:-0})); sw=$((sw + ${s:-0}))
    done
    echo "cgroup_mb=$(( $(cat $CG/memory.current) / 1048576 )) rss_mb=$((rss / 1024)) swapped_mb=$((sw / 1024)) mem_avail_mb=$(( $(awk '/MemAvailable/ {print $2}' /proc/meminfo) / 1024 )) swap_used_mb=$(( ($(awk '/SwapTotal/ {print $2}' /proc/meminfo) - $(awk '/SwapFree/ {print $2}' /proc/meminfo)) / 1024 ))"
}
# time each group spent WAITING on a run queue (run_delay in /proc/PID/task/*/schedstat) and running, summed over its threads: is Xwayland, the Steam client or sway starved while the UI works?
grp() { for p in "$@"; do cat /proc/$p/task/*/schedstat 2>/dev/null; done | awk '{c += $1; w += $2} END {printf "%.0f %.0f ", c, w}'; }
snap() { echo "$(grp $(pidof Xwayland)) $(grp $(pidof steam)) $(grp $(pidof sway)) $(grp $(cat $CG/cgroup.procs))"; }
waits() { echo "$1 $2" | awk '{printf "wait_ms xwayland=%d steam=%d sway=%d ui=%d | cpu_ms xwayland=%d steam=%d sway=%d ui=%d", ($10-$2)/1e6, ($12-$4)/1e6, ($14-$6)/1e6, ($16-$8)/1e6, ($9-$1)/1e6, ($11-$3)/1e6, ($13-$5)/1e6, ($15-$7)/1e6}'; }
$P start >/dev/null 2>&1; $P press right >/dev/null 2>&1; sleep 3                                  # the first press only wakes a sleeping UI
a=$(ticks); s=$(date +%s); q0=$(snap); sleep "$IDLE"; q1=$(snap); b=$(ticks); e=$(date +%s)
echo "FOOTPRINT idle  cpu_core_pct=$(( (b - a) * 100 / ((e - s) * 100) )) $(mem) $(waits "$q0" "$q1")"
a=$(ticks); s=$(date +%s); q0=$(snap)
i=0; while [ $i -lt "$ROUNDS" ]; do $P press right right right right left left left left --gap 600 --hold 100 >/dev/null 2>&1; i=$((i + 1)); done
q1=$(snap); b=$(ticks); e=$(date +%s)
echo "FOOTPRINT nav   cpu_core_pct=$(( (b - a) * 100 / ((e - s) * 100) )) seconds=$((e - s)) $(mem) $(waits "$q0" "$q1")"
