#!/bin/sh
# LIMITATION: it reported "screen static between frames: NO" on the Steam UI (ambient animation), so the press->frame times are NOT valid press latency. Idle/press swap and CPU counters are fine.
# usage: lat.sh LABEL  -- idle counters for 30 s, then 12 harmless pad presses (down/up) timing "press -> first changed frame".
export XDG_RUNTIME_DIR=/var/run/0-runtime-dir WAYLAND_DISPLAY=wayland-1
P=/storage/wispos/dev/wisp-dev-pad; L=$1
ms() { awk '{printf "%d", $1*1000}' /proc/uptime; }
vm() { awk '/^(pswpin|pswpout|workingset_refault_anon|workingset_refault_file) /{printf "%s=%s ", $1, $2}' /proc/vmstat; awk '$3=="mmcblk0"{printf "rdsect=%s", $6}' /proc/diskstats; }
cpu() { t=0; for n in $(pidof steamwebhelper steam Xwayland sway); do set -- $(cut -d' ' -f14,15 /proc/$n/stat 2>/dev/null); t=$((t + ${1:-0} + ${2:-0})); done; echo $t; }
get() { eval "echo \${$2}" ; }
snapshot() { eval "$(vm | tr ' ' '\n' | sed 's/^/S_/' | tr '\n' ' ' | sed 's/ S_/;S_/g;s/^/ /')" 2>/dev/null; }
rates() { # $1 label, before-string, after-string, seconds, cpu0, cpu1
  echo "$2 $3" | tr ' ' '\n' | awk -F= -v s=$4 -v c0=$5 -v c1=$6 'NF==2{ if (seen[$1]) {d=$2-first[$1]; r[$1]=d} else {seen[$1]=1; first[$1]=$2} } END{ printf "  swapout %.0f pg/s, swapin %.0f pg/s, anon-refault %.0f pg/s, file-refault %.0f pg/s, SD-read %.2f MB/s, steam+xwl+sway CPU %.0f%% of a core\n", r["pswpout"]/s, r["pswpin"]/s, r["workingset_refault_anon"]/s, r["workingset_refault_file"]/s, r["rdsect"]*512/1048576/s, (c1-c0)/s }'; }
echo "=== $L: swappiness=$(cat /proc/sys/vm/swappiness) page-cluster=$(cat /proc/sys/vm/page-cluster) $(grep -E 'MemAvailable' /proc/meminfo | tr -s ' ') zram_used_kB=$(awk '/zram0/{print $4}' /proc/swaps)"
[ -n "$(pidof wisp-dev-pad)" ] || { $P start --clone "H700 Gamepad" >/dev/null 2>&1 & sleep 3; }
grim -t ppm /run/l_a.ppm; sleep 0.6; grim -t ppm /run/l_b.ppm; cmp -s /run/l_a.ppm /run/l_b.ppm && echo "  screen static between frames: yes" || echo "  screen static between frames: NO (ambient animation; latency numbers unreliable)"
b=$(vm); c0=$(cpu); t0=$(ms); sleep 30; a=$(vm); c1=$(cpu); t1=$(ms)
echo " idle 30 s:"; rates idle "$b" "$a" $(( (t1 - t0) / 1000 )) $c0 $c1
lat=""
b=$(vm); c0=$(cpu); t0=$(ms)
for btn in down up down up down up down up down up down up; do
  grim -t ppm /run/l_ref.ppm; s=$(ms); $P press $btn >/dev/null 2>&1
  n=0; while :; do grim -t ppm /run/l_cur.ppm; if ! cmp -s /run/l_ref.ppm /run/l_cur.ppm; then lat="$lat $(( $(ms) - s ))"; break; fi; n=$((n+1)); [ $n -ge 30 ] && { lat="$lat NONE"; break; }; done
  sleep 3
done
a=$(vm); c1=$(cpu); t1=$(ms)
echo " pressing phase ($(( (t1 - t0) / 1000 )) s):"; rates press "$b" "$a" $(( (t1 - t0) / 1000 )) $c0 $c1
echo "  press->first changed frame (ms):$lat"
echo "  sorted: $(for x in $lat; do echo $x; done | grep -v NONE | sort -n | tr '\n' ' ')"
rm -f /run/l_*.ppm
