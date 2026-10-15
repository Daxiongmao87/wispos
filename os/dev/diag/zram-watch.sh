#!/bin/sh
# zram-watch.sh: a dev diagnostic. Every 15 s one line of counters for deciding whether zram is the problem or is full: zram `mem_used_total` / `mem_limit` (MiB), `failed_writes` (swap-outs zram
# REJECTED because it is at its cap; the cumulative counter, take differences), pswpout/pswpin, pgscan_direct (processes reclaiming for themselves), workingset_refault_file (file pages read back
# after eviction), MemAvailable, load. Run it on the device with its log in RAM (never on the SD card):
#   scp-or-cat this file to /run/zram-watch.sh; chmod +x; (nohup /run/zram-watch.sh > /run/zram-watch.log 2>&1 &)
# Background: docs/WISPOS-NOTES.md "Slowdown in the aged Steam session" (2026-10-15): the cap was reached, failed_writes ran at about 8,500/s and the device crawled; zram swap traffic itself is cheap.
while :; do
  m=$(cat /sys/block/zram0/mm_stat); u=$(echo $m | cut -d' ' -f3); l=$(echo $m | cut -d' ' -f4); fw=$(awk '{print $2}' /sys/block/zram0/io_stat)
  v=$(awk '/^(pswpout|pswpin|pgscan_direct|workingset_refault_file) /{printf "%s=%s ", $1, $2}' /proc/vmstat)
  echo "$(date +%T) zram=$((u/1048576))/$((l/1048576))MiB failed_writes=$fw $v avail=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)M load=$(cut -d' ' -f1 /proc/loadavg)"
  sleep 15
done
