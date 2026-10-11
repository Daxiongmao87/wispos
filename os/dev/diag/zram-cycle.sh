#!/bin/sh
# usage: zcycle.sh LABEL SIZE_MB LIMIT_MB   (LIMIT 0 = no RAM cap). Steam is stopped for the resize; dev only.
# Benchmark per setting: cold-cache Steam start -> time to first Steam view and to the "Steam Big Picture Mode" view, then a 150 s game run under sampling.
export XDG_RUNTIME_DIR=/var/run/0-runtime-dir WAYLAND_DISPLAY=wayland-1 SWAYSOCK=/var/run/0-runtime-dir/sway-ipc.0.sock
L=$1; SZ=$2; LIM=$3; Z=/sys/block/zram0; W=/storage/wispos/dev
ms() { awk '{printf "%d", $1*1000}' /proc/uptime; }
vmc() { awk -v k=$1 '$1==k{print $2}' /proc/vmstat; }
echo "##### $L: zram ${SZ} MB, RAM cap ${LIM} MB  ($(date +%T))"
systemctl stop wisp-steam; sleep 8; sh $W/kill-game.sh >/dev/null 2>&1; sleep 2
swapoff /dev/zram0 || { echo "swapoff FAILED"; exit 1; }
echo 1 > $Z/reset && echo lz4 > $Z/comp_algorithm && echo ${SZ}M > $Z/disksize || { echo "zram setup FAILED"; exit 1; }
[ "$LIM" -gt 0 ] && echo ${LIM}M > $Z/mem_limit
mkswap /dev/zram0 >/dev/null && swapon -p 200 /dev/zram0
sync; echo 3 > /proc/sys/vm/drop_caches; sleep 2
echo "zram $(cat $Z/disksize) bytes; cold start with MemAvailable $(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo) MB"
attempt=1
while [ $attempt -le 3 ]; do
  t0=$(ms); systemctl start wisp-steam; first=""; bpm=""
  n=0; while [ $n -lt 150 ]; do
    tr=$(swaymsg -t get_tree 2>/dev/null)
    [ -z "$first" ] && echo "$tr" | grep -q '"class": "steam"' && first=$(( ($(ms) - t0) / 1000 ))
    if echo "$tr" | grep -q 'Steam Big Picture Mode'; then bpm=$(( ($(ms) - t0) / 1000 )); break; fi
    n=$((n+1)); sleep 1
  done
  [ -n "$bpm" ] && break
  echo "  attempt $attempt: no UI after 150 s (stalled; mem avail $(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo) MB, zram $(awk '{printf "%d MB in %d MB", $1/1048576, $3/1048576}' $Z/mm_stat))"
  systemctl stop wisp-steam; sleep 12; attempt=$((attempt+1))
done
echo "LOAD TIME: first Steam view ${first:-none} s, Big Picture view ${bpm:-NONE} s, attempts used $attempt"
[ -z "$bpm" ] && { echo "UI never appeared; skipping the game phase"; exit 0; }
sleep 90   # settle: Steam finishes logon and caches
echo "settled: MemAvailable $(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo) MB, zram $(awk '{printf "%d MB held in %d MB RAM", $1/1048576, $3/1048576}' $Z/mm_stat)"
o0=$(vmc pswpout); i0=$(vmc pswpin); f0=$(vmc workingset_refault_file); s0=$(ms); sleep 30
echo "idle 30 s: swapout $(( ($(vmc pswpout) - o0) * 1000 / ($(ms) - s0) )) pg/s, swapin $(( ($(vmc pswpin) - i0) * 1000 / ($(ms) - s0) )) pg/s, file-refault $(( ($(vmc workingset_refault_file) - f0) * 1000 / ($(ms) - s0) )) pg/s"
wl() { journalctl -q -t wisp-dev-watchdog --no-pager 2>/dev/null | grep -c 'stopping wisp-steam'; }   # the watchdog logs to the journal now, not to the card
wl0=$(wl)
DB=$(tr '\0' '\n' < /proc/$(pidof steam | cut -d' ' -f1)/environ | grep '^DBUS_SESSION_BUS_ADDRESS=' | cut -d= -f2-)
cd $W; REPLAY_ENV="DBUS_SESSION_BUS_ADDRESS=$DB PROTON_USE_WINED3D=1 WINEDLLOVERRIDES=d3dx9_43,d3dcompiler_43=n,b" REPLAY_TAG="-zr$L" REPLAY_TIMEOUT=150 python3 replay.py game >/dev/null 2>&1 &
o0=$(vmc pswpout); i0=$(vmc pswpin); s0=$(ms); minav=99999; maxz=0; maxpsi=0; seen=0; t=0
while [ $t -lt 160 ]; do
  sleep 5; t=$((t+5))
  av=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo); [ $av -lt $minav ] && minav=$av
  zr=$(awk '{print int($3/1048576)}' $Z/mm_stat 2>/dev/null); [ "${zr:-0}" -gt $maxz ] && maxz=$zr
  pf=$(awk '/^full/{split($2,a,"="); print int(a[2])}' /proc/pressure/memory); [ "${pf:-0}" -gt $maxpsi ] && maxpsi=$pf
  alive=""; for p in /proc/[0-9]*; do case "$(tr '\0' ' ' < $p/cmdline 2>/dev/null)" in *RUNNER.exe*) alive=y; break;; esac; done 2>/dev/null
  [ -n "$alive" ] && seen=$((seen+1))
  [ $((t % 30)) -eq 0 ] && echo "  t=$t s game_alive=${alive:-no} MemAvailable=${av}MB zram_RAM=${zr}MB psi_full=${pf}"
done 2>/dev/null
echo "game alive in $seen of 32 samples; GAME PHASE: min MemAvailable ${minav} MB, peak zram RAM ${maxz} MB, peak PSI full ${maxpsi}, swapout $(( ($(vmc pswpout) - o0) * 1000 / ($(ms) - s0) )) pg/s avg, swapin $(( ($(vmc pswpin) - i0) * 1000 / ($(ms) - s0) )) pg/s avg"
echo "watchdog events during game: $(( $(wl) - wl0 ))  steam: $(systemctl is-active wisp-steam)"
echo "end state: $(awk '{printf "orig %d MB comp %d MB ratio %.2f, huge pages %d", $1/1048576, $2/1048576, $1/($2+1), $8}' $Z/mm_stat)"
sh $W/kill-game.sh >/dev/null 2>&1
echo "##### $L done $(date +%T)"
