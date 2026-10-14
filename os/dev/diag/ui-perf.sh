#!/bin/sh
# Dev (never shipped), runs ON the device: flip the CPU and GPU frequency governors to find out whether frequency ramp-up is part of the Steam UI's animation stutter
# (operator 2026-10-13: make the UI as responsive as possible outside a game). All of it is live and reversible; nothing is written to disk.
#   ui-perf.sh cpu       every core at its top frequency (governor `performance`; the SoC is capped at 1416 MHz)
#   ui-perf.sh cpu-fast  ondemand, but ramps up at 40% load instead of 95% (up_threshold 40)
#   ui-perf.sh gpu       the GPU (panfrost devfreq) at its top frequency (648 MHz; idle it sits at 420 MHz)
#   ui-perf.sh off       back to what ROCKNIX's powerstate sets: cpu `ondemand` (up_threshold 95), gpu `simple_ondemand`
#   ui-perf.sh status
# ROCKNIX's powerstate service (AC/battery profile) may set the governors again when the charger is plugged or unplugged; run `status` after such an event.
GPU=/sys/class/devfreq/1800000.gpu
cpus() { for c in /sys/devices/system/cpu/cpu[0-9]*/cpufreq; do echo "$1" > $c/scaling_governor; done; }
status() {
    echo "cpu governor: $(sort -u /sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor | tr '\n' ' ') cur MHz: $(for c in /sys/devices/system/cpu/cpu[0-9]*/cpufreq; do echo -n "$(( $(cat $c/scaling_cur_freq) / 1000 )) "; done) up_threshold $(cat /sys/devices/system/cpu/cpufreq/ondemand/up_threshold 2>/dev/null)"
    echo "gpu governor: $(cat $GPU/governor) cur MHz: $(( $(cat $GPU/cur_freq) / 1000000 ))"
}
case "$1" in
    cpu)      cpus performance ;;
    cpu-fast) cpus ondemand; echo 40 > /sys/devices/system/cpu/cpufreq/ondemand/up_threshold ;;
    gpu)      echo performance > $GPU/governor ;;
    off)      cpus ondemand; echo 95 > /sys/devices/system/cpu/cpufreq/ondemand/up_threshold; echo simple_ondemand > $GPU/governor ;;
    status)   ;;
    *) echo "usage: ui-perf.sh cpu|cpu-fast|gpu|off|status"; exit 2 ;;
esac
sleep 1; status
