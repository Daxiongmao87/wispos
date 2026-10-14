#!/bin/sh
# Dev (never shipped): put the handheld into one arm of the UI-instability series, ON the device (copy to /storage/wispos/dev/ or pipe it: `ssh root@HOST sh -s ARM < ui-arm.sh`).
#   ui-arm.sh baseline | b | c | d | status
# Arms (docs/WISPOS-NOTES.md "UI instability investigation 2026-10-13", operator idea: pin the swap work to the last core, Steam and the game to the others):
#   baseline  the shipped settings: vm.page-cluster 3 (this build's value), kswapd0 free on all cores, Steam on all cores
#   b         vm.page-cluster 0 (zram has no seek cost: one page per swap-in, not eight)
#   c         kswapd0 pinned to cpu 3, and the Steam session on cpus 0-2 (a /run drop-in CPUAffinity=0-2 on wisp-steam.service: children inherit it, so the web helpers, the game, wineserver
#             and FEX all stay off cpu 3; nothing is written on the card: /run is RAM)
#   d         b and c together
# The arm is set BEFORE a Steam restart (the drop-in only affects processes started after it): `ui-arm.sh d && systemctl restart wisp-steam`. This script never restarts anything itself.
# Limits worth knowing: direct reclaim (compression) runs in the allocating task and swap-in (decompression) in the faulting task; neither can be pinned, only background reclaim can.
DROPIN_DIR=/run/systemd/system/wisp-steam.service.d; DROPIN=$DROPIN_DIR/ui-arm-affinity.conf
arm=${1:-status}
kswapd_pid() { pgrep -x kswapd0 | head -1; }
set_cluster() { printf '%s\n' "$1" > /proc/sys/vm/page-cluster; }
pin_kswapd() { p=$(kswapd_pid); [ -n "$p" ] && taskset -p -c "$1" "$p" >/dev/null; }
pin_steam() { mkdir -p "$DROPIN_DIR"; printf '[Service]\nCPUAffinity=%s\n' "$1" > "$DROPIN"; systemctl daemon-reload; }
unpin_steam() { rm -f "$DROPIN"; rmdir "$DROPIN_DIR" 2>/dev/null; systemctl daemon-reload; }
case $arm in
    baseline) set_cluster 3; pin_kswapd 0-3; unpin_steam ;;
    b)        set_cluster 0; pin_kswapd 0-3; unpin_steam ;;
    c)        set_cluster 3; pin_kswapd 3;   pin_steam 0-2 ;;
    d)        set_cluster 0; pin_kswapd 3;   pin_steam 0-2 ;;
    status) ;;
    *) echo "usage: ui-arm.sh baseline|b|c|d|status" >&2; exit 2 ;;
esac
p=$(kswapd_pid)
echo "page-cluster=$(cat /proc/sys/vm/page-cluster) swappiness=$(cat /proc/sys/vm/swappiness) kswapd0=$(grep Cpus_allowed_list /proc/$p/status | cut -f2) dropin=$( [ -f "$DROPIN" ] && tr '\n' ' ' < "$DROPIN" || echo none )"
sp=$(pidof steam | cut -d' ' -f1); [ -n "$sp" ] && echo "steam client pid $sp cpus=$(grep Cpus_allowed_list /proc/$sp/status | cut -f2)"
