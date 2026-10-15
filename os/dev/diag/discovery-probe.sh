#!/bin/bash
# Dev (never shipped): READ-ONLY discovery of what the handheld really has and what Steam really found, before anything is changed (operator, 2026-10-14: "verify your concerns on the
# hardware before making changes"). Every concern in docs/WISPOS-DISCOVERY-PLAN.md is a hypothesis; this gathers the evidence for the ones a shell can answer.
#
#   WISP_DEV_HOST=<handheld address> [WISP_DEV_KEY=~/.ssh/wispos-dev] [WISP_DEV_PORT=22] os/dev/diag/discovery-probe.sh [OUTDIR]
#
# The probe script runs on the device through `ssh ... sh -s` (standard input), so NOTHING is copied to or written on the device: no file, no redirection into a file, no state change.
# Every command is a read: cat, ls, grep, busctl list/status/introspect/get-property, pactl list, nmcli show, systemctl status/is-active/show, journalctl, /proc and /sys reads.
# os/test_discovery_probe.py pins that property (a deny-list of writing commands and redirections, mutation-checked) and runs the script against a fake device.
# Output: OUTDIR/probe.txt (all sections) and one file per section, OUTDIR/INDEX.txt. OUTDIR must be on the HDD (default: the project's device directory, stamped).
set -uo pipefail

# ---- the part that runs ON the device (kept in one place so the test can read exactly this text) -------------------------------------------------------------------------------
read -r -d '' REMOTE <<'REMOTE_EOF'
# POSIX sh (busybox). Read-only. `t` bounds every command so a hung service cannot hang the probe.
t() { timeout 8 "$@" 2>&1; echo "[exit $?]"; }
sec() { printf '\n### %s\n' "$1"; }
cmd() { printf '$ %s\n' "$*"; t "$@"; }
export XDG_RUNTIME_DIR=/var/run/0-runtime-dir WAYLAND_DISPLAY=wayland-1 SWAYSOCK=/var/run/0-runtime-dir/sway-ipc.0.sock
STEAMLOGS=/storage/wispos/home/.local/share/Steam/logs

sec identity
cmd cat /etc/os-release; cmd uname -a; cmd cat /proc/cmdline; cmd uptime; cmd date
printf 'device-tree model: '; tr '\0' ' ' < /proc/device-tree/model 2>&1; echo
cmd systemctl is-system-running; cmd systemctl --failed --no-legend --plain

sec steam-process-and-flags
SP=$(pidof steam | cut -d' ' -f1)
echo "steam pid: ${SP:-none}"
[ -n "$SP" ] && { printf 'cmdline: '; tr '\0' ' ' < /proc/$SP/cmdline; echo; }
WH=$(pgrep -f 'steamwebhelper -nocrashdialog' | head -1)
[ -n "$WH" ] && { printf 'web helper cmdline: '; tr '\0' ' ' < /proc/$WH/cmdline; echo; grep -E 'Cpus_allowed_list|VmRSS|VmSwap' /proc/$WH/status; }
echo "installed wrapper: -clientbeta in code: $(grep -v '^ *#' /usr/bin/wisp-steam-session | grep -c clientbeta)  package/beta writes in code: $(grep -v '^ *#' /usr/bin/wisp-steam-session | grep -c package/beta)"
cmd grep -E 'BUILD_ID|VERSION_ID|WISPOS_VERSION' /etc/os-release
printf 'package/beta: '; cat /storage/wispos/runtime/steam/package/beta 2>&1
cmd grep -nE 'bad CRC|Extracting package|installed version|Client beta changed|Download skipped|Opted in|Overriding client beta|Startup - Steam Client' /storage/wispos/home/.local/share/Steam/logs/bootstrap_log.txt

sec steam-what-it-found
# Steam's own logs of what the OS did and did not answer (the hidden-settings question is answered here first)
for f in steamui_steamos.txt systemmanager.txt systemdisplaymanager.txt systemaudiomanager.txt systemdockmanager.txt systemperfmanager.txt systemperfreporting.txt console_log.txt; do
  echo "=== $f ($(wc -l < $STEAMLOGS/$f 2>/dev/null) lines)"; tail -n 120 $STEAMLOGS/$f 2>&1
done
cmd grep -h -i -E 'steamos-|SteamOSManager|UPower|UDisks|org\.freedesktop|helper|not present|failed to|not supported|unavailable|predates' $STEAMLOGS/console_log.txt $STEAMLOGS/steamui_steamos.txt $STEAMLOGS/systemmanager.txt

sec dbus-system
cmd busctl --system list --no-pager
for n in org.freedesktop.UPower org.freedesktop.UDisks2 org.bluez org.freedesktop.NetworkManager net.connman.iwd org.freedesktop.login1 org.freedesktop.timedate1 com.steampowered.Atomupd1; do
  printf '%s: ' "$n"; busctl --system status "$n" 2>&1 | head -3 | tr '\n' ' '; echo
done
cmd ls -la /usr/share/dbus-1/system-services /usr/share/dbus-1/services

sec dbus-steam-session
if [ -n "${SP:-}" ]; then
  ADDR=$(tr '\0' '\n' < /proc/$SP/environ | sed -n 's/^DBUS_SESSION_BUS_ADDRESS=//p' | head -1); echo "steam session bus: ${ADDR:-none}"
  if [ -n "$ADDR" ]; then
    cmd busctl --address "$ADDR" list --no-pager
    cmd busctl --address "$ADDR" tree com.steampowered.SteamOSManager1
    cmd busctl --address "$ADDR" introspect com.steampowered.SteamOSManager1 /com/steampowered/SteamOSManager1
  fi
fi

sec power-battery-suspend-rtc
cmd ls -la /sys/class/power_supply; for d in /sys/class/power_supply/*; do echo "== $d"; cat $d/uevent 2>&1; done
cmd cat /sys/power/state; cmd cat /sys/power/mem_sleep; cmd cat /sys/power/disk
cmd systemctl status suspend.target sleep.target --no-pager; cmd cat /etc/systemd/sleep.conf; cmd grep -v '^#' /etc/systemd/logind.conf
cmd systemd-inhibit --list --no-pager; cmd ls -la /dev/rtc0 /sys/class/rtc; cmd cat /proc/driver/rtc; cmd timedatectl; cmd cat /sys/class/rtc/rtc0/name
cmd ls /usr/bin/upower /usr/libexec/upowerd /usr/libexec/udisks2/udisksd

sec backlight-display
cmd ls -la /sys/class/backlight; for d in /sys/class/backlight/*; do echo "== $d: brightness=$(cat $d/brightness) max=$(cat $d/max_brightness) bl_power=$(cat $d/bl_power 2>/dev/null)"; done
cmd swaymsg -t get_outputs; cmd cat /sys/class/drm/card0-DSI-1/status; cmd cat /sys/class/drm/card0-DSI-1/modes
cmd ls -la /sys/class/leds

sec audio
cmd cat /proc/asound/cards; cmd ls -la /dev/snd; cmd pactl info; cmd pactl list short sinks; cmd pactl list short sources; cmd wpctl status
cmd cat /proc/bus/input/devices

sec video-engine-hdmi-memory
# hardware codecs: the candidate kernel has no Cedrus driver and its device tree no video-engine node (host reading of the build, 2026-10-14); this shows what the RUNNING device exposes
cmd ls -la /dev/video0 /dev/video1 /dev/media0 /dev/dri
cmd lsmod; cmd ls /sys/bus/platform/devices; cmd ls /proc/device-tree/soc
cmd ls /sys/class/video4linux /sys/class/media
for b in v4l2-ctl ffmpeg gst-launch-1.0 vainfo; do echo "$b: $(command -v $b || echo ABSENT)"; done
cmd grep -i -E 'cedrus|video engine|cedar|v4l2|vpu|hdmi' /proc/modules /proc/devices /proc/interrupts
cmd grep -i -E 'cma|memtotal|memavailable|swaptotal' /proc/meminfo; cmd od -An -tx1 /proc/device-tree/memory/reg
# HDMI: the device tree describes a connector and an enabled controller; our release gate says the board has none. Which DRM connectors exist, and does one report a cable?
cmd ls -la /sys/class/drm; for c in /sys/class/drm/card*-*; do echo "== $c status=$(cat $c/status 2>/dev/null) enabled=$(cat $c/enabled 2>/dev/null) dpms=$(cat $c/dpms 2>/dev/null)"; cat $c/modes 2>/dev/null | head -5; done
cmd cat /proc/asound/cards
printf 'hdmi@6000000 status: '; tr '\0' ' ' < /proc/device-tree/soc/hdmi@6000000/status 2>&1; echo
cmd ls /proc/device-tree/connector
cmd systemctl is-active hdmi-hotplug.path; cmd udevadm info -q property -p /sys/class/drm/card0-HDMI-A-1

sec network-bluetooth
cmd nmcli -t general; cmd nmcli device; cmd nmcli radio; cmd nmcli -t connection show; cmd rfkill list
cmd bluetoothctl show; cmd bluetoothctl devices; cmd systemctl is-active bluetooth iwd NetworkManager

sec storage-sd
cmd cat /proc/partitions; cmd ls -la /dev/disk/by-label /dev/mmcblk0 /dev/mmcblk1; cmd blkid; cmd cat /proc/mounts; cmd df -h
cmd ls -la /sys/class/mmc_host; cmd grep -E 'gpio-(150|166)' /sys/kernel/debug/gpio
cmd ls -la /usr/lib/udev/rules.d/98-wisp-tf2.rules; cmd systemctl is-active wisp-tf2-mount wisp-tf2-umount rocknix-automount
cmd cat /storage/wispos/runtime/steam/steamapps/libraryfolders.vdf; cmd ls -la /storage/wispos/tf2 /storage/wispos/logs
cmd cat /storage/wispos/logs/steamos-format-sdcard.log

sec steamos-helpers-steam-may-call
for h in steamos-priv-write steamos-set-timezone steamos-set-hostname steamos-select-branch steamos-devkit-mode steamos-mandatory-update steamos-updatelevel jupiter-initial-firmware-update steamos-reboot steamos-session-select steamos-format-sdcard steamos-format-device steamos-trim-devices steamos-update steamos-update-rauc steamos-reboot-other; do
  p=$(command -v $h 2>/dev/null || ls /usr/bin/steamos-polkit-helpers/$h 2>/dev/null); echo "$h: ${p:-ABSENT}"
done
cmd ls -la /usr/bin/steamos-polkit-helpers

sec session-and-ui-state
cmd systemctl status wisp-steam wisp-ui-priority wisp-ui-reclaim wisp-steam-watch wisp-home wisp-screen-idle --no-pager
cmd swaymsg -t get_tree; cmd cat /sys/fs/cgroup/unified/wisp-ui/cgroup.freeze; cmd cat /sys/fs/cgroup/unified/wisp-ui/memory.high
cmd free -m; cmd cat /proc/swaps; cmd cat /proc/pressure/memory; ls /tmp/dumps 2>/dev/null | tail -5

sec journal-warnings
cmd journalctl -b -p warning --no-pager -n 200
REMOTE_EOF

# --print-remote: print exactly the text that runs on the device (os/test_discovery_probe.py reads it this way) and stop; needs no device and no directory
if [ "${1:-}" = "--print-remote" ]; then printf '%s\n' "$REMOTE"; exit 0; fi
HOST=${WISP_DEV_HOST:?set WISP_DEV_HOST to the handheld address}; KEY=${WISP_DEV_KEY:-$HOME/.ssh/wispos-dev}; PORT=${WISP_DEV_PORT:-22}
OUT=${1:-/mnt/hdd/projects/active/wispos-steam-settings/device/probe-$(date +%Y%m%d-%H%M%S)}
case "$(realpath -m "$OUT")" in /mnt/hdd/*) ;; *) echo "discovery-probe: OUTDIR must be on the HDD" >&2; exit 2 ;; esac
mkdir -p "$OUT"
SSH=(ssh -i "$KEY" -p "$PORT" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=15 "root@$HOST")
printf '%s\n' "$REMOTE" | "${SSH[@]}" sh -s > "$OUT/probe.txt" 2> "$OUT/probe.stderr"; rc=$?
echo "discovery-probe: ssh exit $rc, $(wc -l < "$OUT/probe.txt") lines -> $OUT/probe.txt"
awk -v d="$OUT" '/^### /{ if (f) close(f); name=$2; f=d "/" name ".txt"; print "section " name > "/dev/stderr" } { if (f) print > f }' "$OUT/probe.txt" 2> "$OUT/INDEX.txt"
cat "$OUT/INDEX.txt" | tr '\n' ' '; echo
exit $rc
