#!/bin/sh
# Dev: how much of the Steam UI can the kernel push into zram, and how fast does it come back? Runs ON the device (copy to /storage/wispos/dev/), as root, with Steam's UI up and idle.
# Why: docs/WISPOS-NOTES.md "MEMORY FLOOR": the UI (steamwebhelper 460-700 MB) plus sway fill the device before a game starts; the project's definition of done says the UI must
# be reclaimable (cgroup v2 + zram) while a game owns the screen. This measures the lever without a game: one reclaim, then 3 samples.
# Layout facts (read 2026-10-09): cgroups are hybrid; the v2 hierarchy is /sys/fs/cgroup/unified and offers `cpuset memory`, but its cgroup.subtree_control is EMPTY (nothing
# uses the memory controller); systemd manages the v1 name=systemd tree and mirrors units in v2 only for tracking. The kernel is 7.2 (memory.reclaim exists).
# What it does (all reversible, nothing persistent: a reboot undoes it; `cleanup` below puts the helpers back):
#   1. +memory in the v2 root and in system.slice (needed for memory.* files in children; system.slice holds no processes itself),
#   2. a child cgroup system.slice/wisp-ui with the steamwebhelper processes moved into it (v2 only; systemd's own tracking is unchanged),
#   3. reads memory.current / memory.stat, writes SIZE to memory.reclaim, reads again at once, +20 s, +60 s, together with zram's mm_stat and MemAvailable.
# usage: ui-reclaim-test.sh [SIZE=400M]      Never calls sway (its IPC leaks).
SIZE=${1:-400M}; U=/sys/fs/cgroup/unified; G=$U/system.slice/wisp-ui
[ -d $U ] || { echo "no v2 hierarchy at $U"; exit 1; }
grep -qw memory $U/cgroup.controllers || { echo "the v2 hierarchy has no memory controller"; exit 1; }
grep -qw memory $U/cgroup.subtree_control || echo +memory > $U/cgroup.subtree_control || exit 1
grep -qw memory $U/system.slice/cgroup.subtree_control || echo +memory > $U/system.slice/cgroup.subtree_control || exit 1
mkdir -p $G
n=0; for p in $(pidof steamwebhelper); do echo $p > $G/cgroup.procs && n=$((n+1)); done
echo "moved $n steamwebhelper processes into $G; controllers there: $(cat $G/cgroup.controllers)"
[ "$n" -gt 0 ] || { echo "no steamwebhelper running: nothing to measure"; exit 1; }
mb() { echo $(( $1 / 1048576 )); }
stat_of() { awk -v k="$1" '$1==k {print $2}' $G/memory.stat; }
show() {
  cur=$(cat $G/memory.current); sw=$(cat $G/memory.swap.current 2>/dev/null || echo 0)
  zu=$(awk '{print $3}' /sys/block/zram0/mm_stat); zo=$(awk '{print $1}' /sys/block/zram0/mm_stat); av=$(awk '/MemAvailable/{print $2}' /proc/meminfo)
  echo "$1: ui current=$(mb $cur)MB (anon $(mb $(stat_of anon)) file $(mb $(stat_of file)) shmem $(mb $(stat_of shmem))) swapped=$(mb $sw)MB | zram used=$(mb $zu)MB orig=$(mb $zo)MB | MemAvailable=$((av/1024))MB | PSI some10=$(awk '/some/{print $2}' /proc/pressure/memory)"
}
show before
echo "$SIZE" > $G/memory.reclaim; echo "reclaim $SIZE -> exit $? (EAGAIN means the kernel could not free that much)"
show after-reclaim
sleep 20; show plus-20s
sleep 40; show plus-60s
grep -E "^(pgscan|pgsteal|pswpout|pswpin|workingset_refault)" /proc/vmstat | tr '\n' ' '; echo
# cleanup: put the helpers back where systemd put them: for p in $(cat $G/cgroup.procs); do echo $p > $U/system.slice/wisp-steam.service/cgroup.procs; done; rmdir $G
