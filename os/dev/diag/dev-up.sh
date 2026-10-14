#!/bin/sh
# Dev (never shipped), runs ON the device: bring a dev card (an image older than the tree) to the configuration the tree ships, from the copies in /storage/wispos/dev, and re-arm the dev daemons that a
# reboot clears (they are transient units and a /run drop-in). Idempotent; restarts the Steam session once (a running game is lost). Operator 2026-10-14: "set the device with these settings".
#   memory policy   wisp-ui-reclaim --follow-focus, game ceiling 250 MiB (the image's own --freeze daemon is stopped)
#   priority        wisp-ui-priority (UI -10 in front / 19 with a game in front or starting; game -10 in front, 0 otherwise)
#   web helper      the three performance switches on the helper launch line (ui-flags.sh keeps Valve's file size)
#   sysctl          vm.page-cluster 0 (the image does it in wisp-steam-session; an older card does not)
#   session pieces  wisp-home (Steam button), wisp-game-display (full resolution for games), wisp-steam-watch (watchdog), the sway socket shared into the game container
# usage: dev-up.sh            (needs /storage/wispos/dev/{wisp-ui-reclaim,wisp-ui-priority,wisp-home,wisp-game-display,wisp-steam-watch,ui-flags.sh})
W=/storage/wispos/dev
E="-E XDG_RUNTIME_DIR=/var/run/0-runtime-dir -E SWAYSOCK=/var/run/0-runtime-dir/sway-ipc.0.sock"
for f in wisp-ui-reclaim wisp-ui-priority wisp-home wisp-game-display wisp-steam-watch ui-flags.sh; do [ -e $W/$f ] || { echo "missing $W/$f"; exit 1; }; done
printf '0\n' > /proc/sys/vm/page-cluster
for u in wisp-ui-nice wisp-reclaim-dev wisp-ui-priority-dev wisp-home-dev wisp-game-display-dev wisp-steam-watch-dev; do systemctl stop $u 2>/dev/null; systemctl reset-failed $u 2>/dev/null; done
pkill -f '^/usr/bin/wisp-ui-reclaim' 2>/dev/null; sleep 1; echo 0 > /sys/fs/cgroup/unified/wisp-ui/cgroup.freeze 2>/dev/null
mkdir -p /run/systemd/system/wisp-steam.service.d
printf '[Service]\nEnvironment=PRESSURE_VESSEL_FILESYSTEMS_RW=/run/0-runtime-dir/sway-ipc.0.sock\n' > /run/systemd/system/wisp-steam.service.d/pv-sway.conf
systemctl daemon-reload
sh $W/ui-flags.sh set "--enable-gpu-rasterization --enable-zero-copy --enable-low-end-device-mode" || exit 1
# the ceiling daemon must run BEFORE Steam starts: pages are charged to the cgroup that was current when they were allocated
systemd-run --unit=wisp-reclaim-dev --collect $E $W/wisp-ui-reclaim --follow-focus --high-game 262144000 >/dev/null
sleep 2
systemctl restart wisp-steam
systemd-run --unit=wisp-ui-priority-dev --collect $E python3 -u $W/wisp-ui-priority >/dev/null
systemd-run --unit=wisp-home-dev --collect $E python3 -u $W/wisp-home >/dev/null
systemd-run --unit=wisp-game-display-dev --collect $E python3 -u $W/wisp-game-display >/dev/null
systemd-run --unit=wisp-steam-watch-dev --collect $E python3 -u $W/wisp-steam-watch >/dev/null
sleep 5
echo "page-cluster $(cat /proc/sys/vm/page-cluster) | ceiling $(cat /sys/fs/cgroup/unified/wisp-ui/memory.high) freeze=$(cat /sys/fs/cgroup/unified/wisp-ui/cgroup.freeze)"
for u in wisp-steam wisp-reclaim-dev wisp-ui-priority-dev wisp-home-dev wisp-game-display-dev wisp-steam-watch-dev; do printf '%s %s\n' $u "$(systemctl is-active $u)"; done
sh $W/ui-flags.sh show | grep -E "^--enable|size"
