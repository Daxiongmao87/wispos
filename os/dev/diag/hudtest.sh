#!/bin/sh
# Gallium HUD with counters dumped to files; panel restored to 60 Hz with the panel's own modeline; extra zram device so the watchdog does not end a longer run.
export XDG_RUNTIME_DIR=/var/run/0-runtime-dir WAYLAND_DISPLAY=wayland-1 SWAYSOCK=/var/run/0-runtime-dir/sway-ipc.0.sock
W=/storage/wispos/dev; O=/storage/wispos/shots; H=/storage/wispos/runtime/steam/steamapps/compatdata/63710/hud
for p in /proc/[0-9]*; do case "$(tr '\0' ' ' < $p/cmdline 2>/dev/null)" in "python3 /storage/wispos/dev/diag/applyhz.py"*) kill ${p#/proc/};; esac; done 2>/dev/null
swaymsg -- output DSI-1 modeline 26.00 720 767 771 813 480 513 517 533 -hsync -vsync >/dev/null 2>&1; sleep 3
echo "$(date +%T) panel: $(swaymsg -t get_outputs -r | grep -E '"refresh"' | head -1 | tr -d ' ')"
(setsid nohup python3 $W/diag/applyhz.py >/storage/wispos/logs/applyhz.log 2>&1 < /dev/null &)
if [ ! -b /dev/zram1 ]; then id=$(cat /sys/class/zram-control/hot_add); Z=/sys/block/zram$id; echo lz4 > $Z/comp_algorithm; echo 300M > $Z/mem_limit; echo 1G > $Z/disksize; mkswap /dev/zram$id >/dev/null && swapon -p 100 /dev/zram$id; fi
echo "$(date +%T) swap: $(tail -n +2 /proc/swaps | awk '{printf "%s %dMB/%dMB  ", $1, $4/1024, $3/1024}')"
sh $W/kill-game.sh >/dev/null 2>&1; rm -rf $H; mkdir -p $H
DB=$(tr '\0' '\n' < /proc/$(pidof steam | cut -d' ' -f1)/environ | grep '^DBUS_SESSION_BUS_ADDRESS=' | cut -d= -f2-)
cd $W; (REPLAY_ENV="DBUS_SESSION_BUS_ADDRESS=$DB WINEDEBUG=-all PROTON_LOG=0 MESA_SHADER_CACHE_DIR=/storage/wispos/runtime/steam/steamapps/compatdata/63710/mesa_cache PROTON_USE_WINED3D=1 WINEDLLOVERRIDES=d3dx9_43,d3dcompiler_43=n,b GALLIUM_HUD=fps,frametime GALLIUM_HUD_PERIOD=500000 GALLIUM_HUD_DUMP_DIR=$H" REPLAY_TAG=-hud3 REPLAY_TIMEOUT=450 setsid nohup python3 replay.py game >/dev/null 2>&1 < /dev/null &)
echo "$(date +%T) game launched"
t=0; while [ $t -lt 420 ]; do
  sleep 15; t=$((t+15))
  alive=no; for p in /proc/[0-9]*; do case "$(tr '\0' ' ' < $p/cmdline 2>/dev/null)" in *RUNNER.exe*) alive=yes; break;; esac; done 2>/dev/null
  win=$(swaymsg -t get_tree | grep -c '"name": "BIT.TRIP RUNNER"')
  [ "$win" -gt 0 ] && swaymsg '[title="BIT.TRIP RUNNER"] focus' >/dev/null 2>&1
  echo "$(date +%T) t=$t alive=$alive window=$win avail=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)MB swapfree=$(awk '/SwapFree/{print int($2/1024)}' /proc/meminfo)MB psi_full=$(awk '/^full/{print $2}' /proc/pressure/memory) hudfiles=$(ls $H 2>/dev/null | tr '\n' ' ')"
  [ $((t % 30)) -eq 0 ] && grim -l 1 $O/hud3-$t.png
  [ "$alive" = no ] && [ $t -gt 60 ] && break
done
sh $W/kill-game.sh >/dev/null 2>&1; echo "$(date +%T) game stopped; steam=$(systemctl is-active wisp-steam)"; ls -la $H | tail -4; echo HUDDONE
