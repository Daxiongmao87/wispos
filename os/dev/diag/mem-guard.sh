#!/bin/sh
# Dev guard for unattended installs and game tests: stop the Steam session when memory is really exhausted: MemAvailable under 90 MB AND free swap under 300 MB, for 3 samples in a row
# (3 s apart), then wait a minute. Both conditions: with the 2 GiB zram, low MemAvailable alone is survivable (measured 100-150 MB for minutes with swap free); the freeze-like
# state seen on 2026-10-09 was low MemAvailable with swap FULL. (The first version triggered on MemAvailable alone and killed a healthy install-script pass at 66 MB.)
# Why a guard at all: a Steam install with the UI running exhausted memory and swap twice (docs/WISPOS-NOTES.md, Worms install); stopping Steam freed ~600 MB at once.
# Run: systemd-run --unit=wisp-mem-guard /storage/wispos/dev/mem-guard.sh     Log: /storage/wispos/logs/mem-guard.log
n=0
while :; do
  a=$(awk '/MemAvailable/{print $2}' /proc/meminfo)
  s=$(awk '/SwapFree/{print $2}' /proc/meminfo)
  if [ "$a" -lt 92160 ] && [ "$s" -lt 307200 ]; then n=$((n+1)); else n=0; fi
  if [ "$n" -ge 3 ]; then echo "$(date +%T) guard: avail ${a} kB, swap free ${s} kB, stopping wisp-steam" >> /storage/wispos/logs/mem-guard.log; systemctl stop wisp-steam --no-block; n=0; sleep 60; fi
  sleep 3
done
