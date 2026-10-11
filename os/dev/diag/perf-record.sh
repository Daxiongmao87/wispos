#!/bin/sh
# Dev: the RECORD half of the sampling profile (perf-report.sh is the other half). Piped over ssh by os/dev/perf-run.sh (`sh -s -- MAX_WAIT_S < perf-record.sh`), so the script is not on
# the card; the samples live in /run (tmpfs) with wall-clock timestamps (perf -k realtime), so a report can be cut to any window of the run. Nothing is written to /storage.
# It waits for a `reaper ... SteamLaunch` process (any Steam game: the launcher sits between Steam and the game), records system-wide CPU-clock samples (499 Hz, timer based: the share of CPU TIME
# each component holds, which is what a frame budget is made of) until that process is gone, then stops and leaves /run/wisp-perf.data and /run/wisp-perf.data.pids (the game's pids) for perf-report.sh.
# Components to read the tables with (aarch64 host, x86 guest): "[kernel.kallsyms]" = kernel work for the game (syscalls, page faults, signals); "libwow64fex" / "libarm64ecfex" / "FEX" = the
# emulator's own code (translation, helpers, soft x87); "//anon" or "[unknown]" = translated x86 code (the JIT cache) and Wine's PE code; "ntdll.so" "win32u.so" "winex11.so" = Wine; "libgallium" =
# Mesa; "libc" "libm" = C library. Sampling costs well under 5% CPU at this rate; os/dev/perf-run.sh measures it against an unprofiled run.
# usage: sh -s -- [MAX_WAIT_S=600] [MAX_RECORD_S=600]
MAX_WAIT=${1:-600}; MAX_REC=${2:-600}; D=/run/wisp-perf.data
launcher() {   # prints the pid of every `reaper ... SteamLaunch` process. pidof matches process NAMES without forking per process (a /proc scan in shell cost ~10% of the machine in the first profile)
    for p in $(pidof reaper 2>/dev/null); do
        tr '\0' ' ' < "/proc/$p/cmdline" 2>/dev/null | grep -q SteamLaunch && echo "$p"
    done
}
n=0; while [ -z "$(launcher)" ]; do
    n=$((n + 1)); [ "$n" -gt "$MAX_WAIT" ] && { echo "perf-record: no game started within ${MAX_WAIT}s"; exit 3; }
    sleep 1
done
rm -f "$D" "$D.pids"
descend() {    # $1 = space separated root pids; prints the pids below them (the roots excluded), one per line
    cat /proc/[0-9]*/stat 2>/dev/null | sed -n 's/^\([0-9]*\) (.*) [A-Za-z] \([0-9]*\) .*/\1 \2/p' | awk -v roots="$1" '
        { par[$1] = $2; n++ }
        END { split(roots, r, " "); for (i in r) mark[r[i]] = 1
              do { ch = 0; for (p in par) if (!(p in mark) && (par[p] in mark)) { mark[p] = 1; out[p] = 1; ch = 1 } } while (ch)
              for (p in out) print p }'
}
perf record -q -k realtime -a -F 499 -e cpu-clock -o "$D" -- sleep "$MAX_REC" >/dev/null 2>&1 &
rec=$!
echo "perf-record: game seen after ${n}s, recording (pid $rec)"
t0=$(date +%s)
# every 10 s: the processes below the launcher (the game's tree builds up during the ~2 minute launch; short-lived helpers do not matter)
while roots=$(launcher | tr '\n' ' ') && [ -n "$roots" ] && [ $(( $(date +%s) - t0 )) -lt "$MAX_REC" ]; do
    descend "$roots" >> "$D.pids"; i=0; while [ $i -lt 10 ] && [ -n "$(launcher)" ]; do sleep 1; i=$((i + 1)); done
done
kill -INT "$rec" 2>/dev/null; wait "$rec" 2>/dev/null
echo "perf-record: game gone after $(( $(date +%s) - t0 ))s, $(ls -l "$D" | awk '{print $5}') bytes of samples in RAM, game pids: $(sort -u "$D.pids" 2>/dev/null | wc -l)"
