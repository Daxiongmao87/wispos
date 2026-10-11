#!/bin/bash
# Dev: A/B several GLOBAL settings on the Quake III timedemo, interleaved so thermal and Steam-state drift hit every variant alike. Prints a table of the fps of every run.
#   os/dev/perf-sweep.sh HOST REPS variant [variant ...]        REPS rounds; each round runs every variant once, in the order given
# Variants (all global; per-game settings are never tested or shipped): base | canary (FEX MaxInst=1, must be much slower: proves the FEX config file is read) | x87 (X87ReducedPrecision=1) |
# tso0 (TSOEnabled=0) | mb0 (Multiblock=0) | mb1 | x87tso0 | gov (CPU and GPU governors to performance) | reclaim / reclaimfreeze (the UI web helpers' cgroup ceiling drops while a game runs, and with reclaimfreeze the group is also frozen; needs the daemon up since Steam's start) | uifreeze (the Steam UI's web helpers stopped while the game runs) | uipageout (stopped AND their resident memory pushed to compressed swap). The layer used is `stream` only (the cheapest; the profile of a chosen variant is a
# separate perf-run.sh). Every setting is undone at the end and by the device's own timer. Results: $OUT/sweep-<stamp>.txt next to the per-run directories.
HOST=${1:-}; REPS=${2:-}; shift 2 2>/dev/null; HERE=$(cd "$(dirname "$0")" && pwd)
[ -n "$HOST" ] && [ -n "$REPS" ] && [ $# -ge 1 ] || { echo "usage: perf-sweep.sh HOST REPS variant..." >&2; exit 2; }
case $REPS in ''|*[!0-9]*|0) echo "REPS must be a positive number" >&2; exit 2;; esac
OUT=${WISP_SWEEP_OUT:-/mnt/hdd/wispos-staging/card-logs-$(date +%Y%m%d)/perf}; STAMP=$(date +%H%M%S)
apply() {
    case $1 in
        base) "$HERE/perf-set.sh" "$HOST" default;;
        canary) "$HERE/perf-set.sh" "$HOST" fex MaxInst=1;;
        x87) "$HERE/perf-set.sh" "$HOST" fex X87ReducedPrecision=1;;
        tso0) "$HERE/perf-set.sh" "$HOST" fex TSOEnabled=0;;
        mb0) "$HERE/perf-set.sh" "$HOST" fex Multiblock=0;;
        mb1) "$HERE/perf-set.sh" "$HOST" fex Multiblock=1;;
        x87tso0) "$HERE/perf-set.sh" "$HOST" fex X87ReducedPrecision=1 TSOEnabled=0;;
        gov) "$HERE/perf-set.sh" "$HOST" default >/dev/null; "$HERE/perf-set.sh" "$HOST" all-performance;;
        uifreeze|uipageout) "$HERE/perf-set.sh" "$HOST" default;;
        reclaim) "$HERE/perf-set.sh" "$HOST" default >/dev/null; "$HERE/perf-set.sh" "$HOST" ui-reclaim-on;;
        reclaimfreeze) "$HERE/perf-set.sh" "$HOST" default >/dev/null; "$HERE/perf-set.sh" "$HOST" ui-reclaim-on --freeze;;
        *) echo "unknown variant $1" >&2; return 1;;
    esac
}
for v in "$@"; do case $v in base|canary|x87|tso0|mb0|mb1|x87tso0|gov|uifreeze|uipageout|reclaim|reclaimfreeze) ;; *) echo "unknown variant: $v" >&2; exit 2;; esac; done
trap '"$HERE/perf-set.sh" "$HOST" default >/dev/null 2>&1' EXIT
: > "$OUT/sweep-$STAMP.txt" 2>/dev/null || { mkdir -p "$OUT"; : > "$OUT/sweep-$STAMP.txt"; }
for rep in $(seq 1 "$REPS"); do
    for v in "$@"; do
        label="sw$STAMP-$v-$rep"
        apply "$v" >/dev/null 2>&1 || { echo "$label: could not apply $v" | tee -a "$OUT/sweep-$STAMP.txt"; continue; }
        hooks=(); [ "$v" = uifreeze ] && hooks=(--on-game-up "$HERE/perf-set.sh $HOST ui-freeze" --on-game-down "$HERE/perf-set.sh $HOST ui-thaw")
        [ "$v" = uipageout ] && hooks=(--on-game-up "$HERE/perf-set.sh $HOST ui-pageout" --on-game-down "$HERE/perf-set.sh $HOST ui-thaw")
        bash "$HERE/perf-run.sh" "$label" --host "$HOST" --layers stream --out "$OUT" "${hooks[@]}" > "$OUT/$label.log" 2>&1
        fps=$(grep -a -o '[0-9.]* fps' "$OUT/$label/result.txt" 2>/dev/null | tail -1)
        echo "$label  $v  ${fps:-no result}" | tee -a "$OUT/sweep-$STAMP.txt"
    done
done
echo "--- table ($OUT/sweep-$STAMP.txt)"
python3 -I - "$OUT/sweep-$STAMP.txt" <<'PY'
import collections, re, sys
d = collections.defaultdict(list)
for line in open(sys.argv[1]):
    m = re.match(r'(\S+)\s+(\S+)\s+([0-9.]+) fps', line)
    if m: d[m.group(2)].append(float(m.group(3)))
for v, xs in d.items(): print('%-9s n=%d  mean %.1f fps  (%s)' % (v, len(xs), sum(xs) / len(xs), ' '.join('%.1f' % x for x in xs)))
PY
