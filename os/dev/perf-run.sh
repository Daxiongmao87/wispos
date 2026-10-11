#!/bin/bash
# Dev: one profiled run of the Quake III timedemo, with the profiler layers you ask for, everything kept on the HDD and nothing written on the device's card (the samples live in RAM).
#   os/dev/perf-run.sh LABEL [--layers stream,stat,record|none] [--host IP] [--out DIR] [--settle SECONDS=600] [--on-game-up CMD] [--on-game-down CMD]
# (CMD runs on THIS machine, once when the game window process is up and once when it is over or the run is abandoned: for example `os/dev/perf-set.sh HOST ui-freeze` / `ui-thaw`.)
# Layers (each one costs a little; the run prints the timedemo's own fps so the cost of a layer is the difference to a run with fewer layers):
#   stream   1 Hz system trace (diag/perf-stream.py): per-core load and clock, GPU clock, per-thread CPU, Steam UI CPU, memory pressure
#   stat     hardware counters once a second (perf stat -a -I 1000): cycles, instructions (IPC), branch misses, cache misses
#   record   sampling profile in RAM (diag/perf-record.sh): CPU time by command, by component (kernel / FEX / Wine / Mesa / translated code) and by symbol
# Output: $OUT/LABEL/{stream.jsonl,stat.csv,record.txt,result.txt} ; default OUT=/mnt/hdd/wispos-staging/card-logs-YYYYMMDD/perf. The game is the Q3 timedemo (a TEST VEHICLE: the layers and
# the findings are about the system, and a change is adopted only if a second, unrelated game confirms it). Needs the game installed, autoexec.cfg holding the timedemo, and
# /storage/wispos/dev/timedemo-q3.sh on the device (docs/WISPOS-NOTES.md, Quake III). It takes over the handheld's screen for about 4 minutes.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
HDD=${WISP_HDD:-/mnt/hdd}; KEY=${WISP_DEV_KEY:-$HOME/.ssh/wispos-dev}; HOST=${WISP_DEV_HOST:-}; LAYERS=stream,stat,record; OUT=; LABEL=; UP_CMD=; DOWN_CMD=; UP_RAN=0; SETTLE=600; MIN_AGE=${WISP_PERF_MIN_AGE:-420}
die() { echo "perf-run: $*" >&2; exit 2; }
while [ $# -gt 0 ]; do
    case $1 in
        --layers) [ $# -ge 2 ] || die "--layers needs a value"; LAYERS=$2; shift;;
        --host) [ $# -ge 2 ] || die "--host needs a value"; HOST=$2; shift;;
        --out) [ $# -ge 2 ] || die "--out needs a value"; OUT=$2; shift;;
        --settle) [ $# -ge 2 ] || die "--settle needs a value"; SETTLE=$2; shift;;
        --on-game-up) [ $# -ge 2 ] || die "--on-game-up needs a command"; UP_CMD=$2; shift;;
        --on-game-down) [ $# -ge 2 ] || die "--on-game-down needs a command"; DOWN_CMD=$2; shift;;
        -h|--help) sed -n '2,3p' "$0" | cut -c3-; exit 0;;
        -*) die "unknown argument: $1";;
        *) [ -z "$LABEL" ] || die "one label only"; LABEL=$1;;
    esac
    shift
done
[ -n "$LABEL" ] || die "usage: perf-run.sh LABEL [--layers stream,stat,record] [--host IP]"
case $LABEL in *[!A-Za-z0-9._-]*) die "bad label";; esac
case ",$LAYERS," in *,none,*) LAYERS=;; esac
for l in $(echo "$LAYERS" | tr , ' '); do case $l in stream|stat|record) ;; *) die "unknown layer: $l";; esac; done
[ -n "$HOST" ] || die "pass --host (or WISP_DEV_HOST): the device's address"
case $HOST in ''|*[!0-9A-Za-z.:_-]*) die "bad host";; esac
[ -n "$OUT" ] || OUT=$HDD/wispos-staging/card-logs-$(date +%Y%m%d)/perf
OUT=$(realpath -m "$OUT"); case $OUT in "$HDD"/*) ;; *) die "refusing $OUT: results go on the HDD ($HDD)";; esac
[ -z "${WISP_HDD:-}" ] && { mountpoint -q "$HDD" || die "$HDD is not mounted"; }
RUN=$OUT/$LABEL; [ ! -e "$RUN" ] || die "$RUN exists: pick a new label"; mkdir -p "$RUN" || die "cannot create $RUN"
SSH=("${WISP_SSH:-ssh}" -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o UserKnownHostsFile="$HOME/.wispos-dev/known_hosts" -o StrictHostKeyChecking=accept-new
     -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 "root@$HOST")
has() { case ",$LAYERS," in *",$1,"*) return 0;; esac; return 1; }
LIFE=${WISP_PERF_LIFE:-900}                          # every layer ends by itself after this many seconds, even if this script is killed

# A game is running when a process NAMED reaper has SteamLaunch in its command line (the same rule as wisp-compat-gate). Matching the text alone would find the checking shell itself.
GAME_RUNNING='for p in $(pidof reaper); do tr "\0" " " < /proc/$p/cmdline 2>/dev/null | grep -q SteamLaunch && exit 0; done; exit 1'
if "${SSH[@]}" "$GAME_RUNNING"; then die "a game is already running on the device"; fi
echo "perf-run: $LABEL on $HOST, layers: ${LAYERS:-none}, results in $RUN"
pids=()
stop() { local p; [ "$UP_RAN" = 1 ] && [ -n "$DOWN_CMD" ] && { bash -c "$DOWN_CMD" >/dev/null 2>&1; UP_RAN=0; }; "${SSH[@]}" 'systemctl start wisp-dev-blackbox-ram 2>/dev/null; true' 2>/dev/null; for p in "${pids[@]}"; do pkill -TERM -P "$p" 2>/dev/null; kill "$p" 2>/dev/null; done; wait 2>/dev/null; "${SSH[@]}" '/usr/bin/wisp-dev-pad stop >/dev/null 2>&1; rm -f /run/wisp-perf.data' 2>/dev/null; }
trap 'stop; exit 130' INT TERM
# Wait for the Steam UI to settle: right after a Steam start it takes 50% of a core for minutes, which would be measured as if it belonged to the game. --settle 0 skips the wait.
# "Settled" = Steam has been up for at least MIN_AGE seconds (its start is two-stage: an updater, then the client, and the compatibility tools register ~8 s after logon; a launch request
# inside that window times out) AND the load average stays under 1 for three checks in a row. A lull in the load alone is not enough: that launched a game into a starting Steam once.
steam_age() { "${SSH[@]}" 'S=$(pidof steam | cut -d" " -f1); [ -n "$S" ] && awk -v up=$(cut -d" " -f1 /proc/uptime) "{print int(up - \$22 / 100)}" /proc/$S/stat' 2>/dev/null; }
# Steam's screensaver ("Away") burns about a core even while the panel is blanked: a Back press on the dev pad wakes it (once a minute: every press makes Steam reload controller
# configs, which is activity of its own). "Quiet" is measured directly: the whole machine under 25% busy over 5 seconds (the 1-minute load average lags and stayed above 1 for
# 10 minutes after a game), three quiet checks in a row, and Steam at least MIN_AGE old.
busy_pct() { "${SSH[@]}" 'a=$(head -1 /proc/stat); sleep 5; b=$(head -1 /proc/stat); echo "$a $b" | awk "{ t1 = 0; t2 = 0; for (i = 2; i <= 9; i++) { t1 += \$i; t2 += \$(i + 11) } i1 = \$5 + \$6; i2 = \$16 + \$17; d = t2 - t1; if (d > 0) b = 100 * (d - (i2 - i1)) / d; else b = 100; printf \"%d\", b }"' 2>/dev/null; }
"${SSH[@]}" '/usr/bin/wisp-dev-pad start >/dev/null 2>&1; true'
waited=0; calm=0; busy=100; age=0; round=0
while [ "$SETTLE" -gt 0 ]; do
    [ $((round % 6)) -eq 0 ] && "${SSH[@]}" '/usr/bin/wisp-dev-pad press b >/dev/null 2>&1; true'
    round=$((round + 1))
    age=$(steam_age); age=${age:-0}
    busy=$(busy_pct); busy=${busy:-100}
    if [ "$age" -ge "$MIN_AGE" ] && [ "$busy" -lt 25 ]; then calm=$((calm + 1)); else calm=0; fi
    [ "$calm" -ge 3 ] && break
    [ "$waited" -ge "$SETTLE" ] && { stop; die "Steam did not settle within ${SETTLE}s (age ${age}s, ${busy}% busy)"; }
    sleep 5; waited=$((waited + 10))
done
echo "perf-run: Steam age ${age}s, machine ${busy}% busy after waiting ${waited}s for the UI to settle"

has stream && { "${SSH[@]}" "python3 -u - 1 $LIFE" < "$HERE/diag/perf-stream.py" > "$RUN/stream.jsonl" 2> "$RUN/stream.err" & pids+=($!); }
has stat   && { "${SSH[@]}" "timeout $LIFE perf stat -a -I 1000 -x, -e cycles,instructions,branch-misses,l1-dcache-load-misses,cache-misses 2>&1" > "$RUN/stat.csv" 2>/dev/null & pids+=($!); }
rec=
has record && { "${SSH[@]}" "sh -s -- 600 $LIFE" < "$HERE/diag/perf-record.sh" > "$RUN/record.log" 2> "$RUN/record.err" & rec=$!; pids+=($rec); }
sleep 3

# The game: the existing launcher script answers Steam's dialogs and returns when quake3 is up; it keeps the panel awake itself (self-expiring).
SINCE=$("${SSH[@]}" 'date +%s'); SINCE=${SINCE:-0}                 # the unit's journal keeps earlier runs' lines: only look at this run's
"${SSH[@]}" 'systemd-run --quiet --unit=wisp-q3-perf --collect bash /storage/wispos/dev/timedemo-q3.sh' || { stop; die "could not start the launcher script on the device"; }
t0=$(date +%s)
# The game must actually appear: the launcher script prints "quake3.exe running" when it does. Without that within 420 s the run is FAILED (a launch that timed out inside Steam otherwise
# leaves every layer waiting for a game that never comes).
up=0; for i in $(seq 1 84); do
    sleep 5
    if "${SSH[@]}" "journalctl -u wisp-q3-perf --since @$SINCE --no-pager -o cat 2>/dev/null | grep -q 'quake3.exe running'"; then up=1; break; fi
    if "${SSH[@]}" "journalctl -u wisp-q3-perf --since @$SINCE --no-pager -o cat 2>/dev/null | grep -q '^FAILED'"; then break; fi
done
if [ "$up" != 1 ]; then echo "perf-run: FAILED: the game did not start within 420 s (see Steam's console_log and the wisp-q3-perf journal)"; "${SSH[@]}" 'systemctl stop wisp-q3-perf 2>/dev/null; true'; stop; exit 1; fi
[ -n "$UP_CMD" ] && { echo "perf-run: game is up, running: $UP_CMD"; bash -c "$UP_CMD"; UP_RAN=1; }
if [ -n "$rec" ]; then wait "$rec"; else
    # no record layer: wait until the game has appeared and gone again
    seen=0; while [ $(( $(date +%s) - t0 )) -lt "$LIFE" ]; do
        sleep 5
        if "${SSH[@]}" "$GAME_RUNNING"; then seen=1; elif [ $seen = 1 ]; then break; fi
    done
fi
echo "perf-run: game finished after $(( $(date +%s) - t0 )) s"
[ "$UP_RAN" = 1 ] && [ -n "$DOWN_CMD" ] && { bash -c "$DOWN_CMD" >/dev/null 2>&1; UP_RAN=0; }
sleep 2
{ "${SSH[@]}" 'D="/storage/wispos/runtime/steam/steamapps/common/Quake 3 Arena/baseq3"; grep -a -E "frames, .* seconds|VM file .* compiled" "$D/qconsole.log" | tail -4'; } > "$RUN/result.txt" 2>&1
cat "$RUN/result.txt"
# the profile is reported for the steady play window found in the 1 Hz trace (the whole recording when there is no trace)
if has record; then
    w=""; has stream && w=$(python3 -I "$HERE/perf-summary.py" --window "$RUN/stream.jsonl")
    echo "perf-run: reporting the profile for window: ${w:-whole recording}"
    "${SSH[@]}" "sh -s -- $w" < "$HERE/diag/perf-report.sh" > "$RUN/record.txt" 2> "$RUN/report.err"
fi
stop
echo "perf-run: done; files:"; ls -la "$RUN" | tail -n +2
python3 -I "$HERE/perf-summary.py" "$RUN" 2>&1 | head -60
