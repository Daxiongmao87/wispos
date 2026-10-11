#!/bin/sh
# Dev: the REPORT half of the sampling profile: reads /run/wisp-perf.data (left by perf-record.sh) for the wall-clock window T0 T1 (epoch seconds) and prints, as text on stdout:
#   1. the whole system by thread name (percent of the samples in the window)
#   2. the game (every process below the launcher, from /run/wisp-perf.data.pids) by thread name
#   3. the game's components (shared object / mapped file), for all of it and for its busiest thread name
#   4. the game's top symbols, and the busiest thread's top symbols
# then deletes the data (env PERF_KEEP=1 keeps it). Piped over ssh by os/dev/perf-run.sh (`sh -s -- T0 T1 < perf-report.sh`); nothing is written to /storage.
# Reading the components (aarch64 host, x86 guest): "[kernel.kallsyms]" = kernel work for the game (syscalls, page faults, signals, zram/squashfs decompression of evicted pages);
# "libwow64fex" / "libarm64ecfex" = FEX's own code (translation, helpers, soft x87); "[JIT] tid N" = translated x86 code (FEX's JIT cache) and Wine's PE code; "ntdll.so" "win32u.so" "winex11.so" =
# Wine; "libgallium" = Mesa; "libc" "libm" = the C library. Without T0 T1 the whole recording is reported.
T0=${1:-}; T1=${2:-}; D=/run/wisp-perf.data
[ -f "$D" ] || { echo "perf-report: no $D (run perf-record.sh first)"; exit 3; }
W=""; [ -n "$T0" ] && [ -n "$T1" ] && W="--time $T0,$T1"
G=$(sort -u "$D.pids" 2>/dev/null | tr '\n' ',' | sed 's/,$//')      # not paste: on this OS it is a link to pastebinit
R="perf report -i $D --stdio --no-children -q $W"
echo "=== window: ${T0:-start} .. ${T1:-end} (epoch seconds)"
echo; echo "=== 1. whole system: samples per thread name (percent of all samples in the window)"
$R --sort comm 2>/dev/null | head -14
if [ -z "$G" ]; then echo "(no game processes recorded)"; else
    echo; echo "=== 2. the game ($(echo "$G" | tr ',' '\n' | wc -l) processes): percent of ALL samples in the window, by thread name"
    $R --pid="$G" --sort comm 2>/dev/null | head -14
    echo; echo "=== 3a. the game: components"
    $R --pid="$G" --sort dso 2>/dev/null | head -16
    top=$($R --pid="$G" --sort comm 2>/dev/null | awk 'NR==1 {print $2}')
    if [ -n "$top" ]; then
        echo; echo "=== 3b. the busiest game thread ($top): components"; $R --pid="$G" --comms "$top" --sort dso 2>/dev/null | head -12
        echo; echo "=== 4b. the busiest game thread ($top): top symbols"; $R --pid="$G" --comms "$top" --sort dso,sym 2>/dev/null | head -34
    fi
    echo; echo "=== 4a. the game: top symbols"; $R --pid="$G" --sort dso,sym 2>/dev/null | head -22
fi
[ "${PERF_KEEP:-0}" = 1 ] || rm -f "$D" "$D.pids"
