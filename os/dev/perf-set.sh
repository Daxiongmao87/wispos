#!/bin/bash
# Dev: switch settings on the device for a performance experiment, GLOBALLY (never per game: operator 2026-10-11, "Doing per-game fex configs is not maintainable"). Runtime only and self-reverting:
# every change arms a timer that puts the original state back after REVERT_S seconds (default 3600), so a forgotten experiment cannot leave the handheld hot, draining, or running a variant.
#   os/dev/perf-set.sh HOST show
#   os/dev/perf-set.sh HOST cpu-performance | gpu-performance | all-performance     the frequency governors (sysfs, gone at reboot)
#   os/dev/perf-set.sh HOST env KEY=VALUE [KEY=VALUE ...]                            environment for EVERY game run with the community Proton tool: written to the tool's own
#                                                                                    `user_settings.py` ("settings here take effect for all games run in this Proton version");
#                                                                                    games read it at their next launch, no Steam restart. Examples: FEX_X87REDUCEDPRECISION=1,
#                                                                                    GALLIUM_HUD=fps,frametime. Values may not contain quotes or backslashes.
#   os/dev/perf-set.sh HOST fex OPTION=VALUE [OPTION=VALUE ...]                      FEX options for EVERY game run with the community Proton tool: a Config.json is written next to
#                                                                                    user_settings.py and the Proton launcher's own `FEX_APP_CONFIG` is pointed at it (the launcher
#                                                                                    otherwise generates an empty one). Option names are FEX's (TSOEnabled, X87ReducedPrecision,
#                                                                                    Multiblock ...); `MaxInst=1` is the CANARY: it cripples the JIT, so a game that does not slow
#                                                                                    down proves the file is not being read.
#   os/dev/perf-set.sh HOST ui-freeze | ui-thaw                                      SIGSTOP / SIGCONT every steamwebhelper (the Steam UI's browser processes; `steam` itself keeps
#                                                                                    running): the upper bound of what the UI costs a running game in CPU and cache. Only ever freeze
#                                                                                    AFTER the game is up (the UI draws Steam's dialogs). The revert timer thaws.
#   os/dev/perf-set.sh HOST ui-pageout                                               ui-freeze, then push the frozen processes' resident pages out to compressed swap
#                                                                                    (process_madvise MADV_PAGEOUT): the RAM a hidden UI holds goes to the game and its page cache.
#                                                                                    A prototype of the locked plan "the UI is reclaimable when a game owns the screen".
#   os/dev/perf-set.sh HOST ui-reclaim-on [--freeze]                                 start os/reclaim/wisp-ui-reclaim (from /run/wisp-dev, RAM) as the transient unit wisp-dev-reclaim:
#                                                                                    the web helpers sit in a cgroup whose memory.high drops while a game runs (the locked plan item
#                                                                                    1). Only helpers the daemon saw YOUNG are charged to that cgroup, so the daemon must have been
#                                                                                    up since Steam started (the tool warns when fewer than 6 helpers are in it). `default` stops it
#                                                                                    (SIGTERM lifts the ceiling to max). --freeze also freezes the group (cgroup.freeze) while a game runs.
#   os/dev/perf-set.sh HOST default                                                  undo everything this script changed
# The originals are remembered in /run/wisp-perf-saved (RAM) the first time something is changed. These are TEST levers for finding what limits a game; a lever that helps becomes a
# GENERAL rule in the product (for example "a game is running" for the governors, a global default for FEX) after a second, unrelated game confirms it, never a per-game setting.
# The one small card write is user_settings.py itself (a few hundred bytes, removed again by `default` or the timer).
HOST=${1:-}; CMD=${2:-show}; shift 2 2>/dev/null; REVERT_S=${REVERT_S:-3600}
usage() { echo "usage: perf-set.sh HOST show|cpu-performance|gpu-performance|all-performance|default|env KEY=VALUE...|ui-reclaim-on" >&2; exit 2; }
[ -n "$HOST" ] || usage
case $CMD in show|cpu-performance|gpu-performance|all-performance|default|env|fex|ui-freeze|ui-thaw|ui-pageout|ui-reclaim-on) ;; *) usage;; esac
B64=; JB64=; RFREEZE=
if [ "$CMD" = ui-reclaim-on ]; then case ${1:-} in '') ;; --freeze) RFREEZE=--freeze;; *) echo "perf-set: ui-reclaim-on takes only --freeze" >&2; exit 2;; esac; fi
if [ "$CMD" = env ]; then
    [ $# -ge 1 ] || usage
    py='user_settings = {\n'
    for kv in "$@"; do
        k=${kv%%=*}; v=${kv#*=}
        case $k in ''|*[!A-Za-z0-9_]*) echo "perf-set: bad variable name: $k" >&2; exit 2;; esac
        case $v in *\"*|*\\*|*$'\n'*) echo "perf-set: value of $k may not contain quotes, backslashes or newlines" >&2; exit 2;; esac
        [ "$kv" != "$k" ] || { echo "perf-set: $kv has no =VALUE" >&2; exit 2; }
        py+="    \"$k\": \"$v\",\n"
    done
    py+='}\n'
    B64=$(printf "$py" | base64 -w0)
fi
if [ "$CMD" = fex ]; then
    [ $# -ge 1 ] || usage
    js='{"Config": {'; sep=
    for kv in "$@"; do
        k=${kv%%=*}; v=${kv#*=}
        case $k in ''|*[!A-Za-z0-9_]*) echo "perf-set: bad option name: $k" >&2; exit 2;; esac
        case $v in ''|*[!A-Za-z0-9_.-]*) echo "perf-set: value of $k must be letters, digits, . _ - only" >&2; exit 2;; esac
        [ "$kv" != "$k" ] || { echo "perf-set: $kv has no =VALUE" >&2; exit 2; }
        js+="$sep\"$k\": \"$v\""; sep=', '
    done
    js+='}, "ThunksDB": {}}'
    JB64=$(printf '%s\n' "$js" | base64 -w0)
fi
KEY=${WISP_DEV_KEY:-$HOME/.ssh/wispos-dev}
exec "${WISP_SSH:-ssh}" -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o UserKnownHostsFile="$HOME/.wispos-dev/known_hosts" -o ConnectTimeout=10 "root@$HOST" "CMD=$CMD REVERT_S=$REVERT_S B64=$B64 JB64=$JB64 RFREEZE=$RFREEZE sh -s" <<'REMOTE'
CPU=/sys/devices/system/cpu/cpufreq/policy0/scaling_governor
GPU=$(ls /sys/class/devfreq/*gpu*/governor 2>/dev/null | head -1)
TOOL=/storage/wispos/runtime/steam/compatibilitytools.d/Proton11-ARM64-A57-v80
US=$TOOL/user_settings.py; FEXJ=$TOOL/wisp-fex-test.json
SAVED=/run/wisp-perf-saved
show() {
    echo "cpu governor: $(cat $CPU) ($(cat /sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq) kHz, max $(cat /sys/devices/system/cpu/cpufreq/policy0/scaling_max_freq))   gpu governor: $(cat $GPU) ($(cat $(dirname $GPU)/cur_freq) Hz, max $(cat $(dirname $GPU)/max_freq))"
    if [ -f $US ]; then echo "game environment (user_settings.py):"; sed -n 's/^ *"\(.*\)": "\(.*\)",$/  \1=\2/p' $US; else echo "game environment: unchanged (no user_settings.py)"; fi
}
FROZEN=/run/wisp-perf-frozen; RESTORE=/run/wisp-perf-restore.sh
# save: remember the originals once, and write ONE restore script that undoes everything (the timer and `default` both run it)
save() {
    [ -f $SAVED ] || { echo "cpu=$(cat $CPU) gpu=$(cat $GPU)" > $SAVED; [ -f $US ] && echo "us_existed=1" >> $SAVED; }
    cat > $RESTORE <<EOF2
. $SAVED 2>/dev/null
[ -n "\$cpu" ] && echo "\$cpu" > $CPU
[ -n "\$gpu" ] && echo "\$gpu" > $GPU
[ -n "\$us_existed" ] || rm -f $US
rm -f $FEXJ
[ -f $FROZEN ] && kill -CONT \$(cat $FROZEN) 2>/dev/null
systemctl stop wisp-dev-reclaim 2>/dev/null
[ ! -e /storage/.config/wispos/ui-reclaim-off ] && systemctl start wisp-ui-reclaim 2>/dev/null     # the image's own daemon (enabled by default) was stopped for the experiment: back on
rm -f $FROZEN $SAVED
EOF2
}
restore() { [ -f $RESTORE ] && sh $RESTORE; rm -f $RESTORE; }
arm() {
    systemctl stop wisp-perf-revert.timer wisp-perf-revert.service 2>/dev/null
    systemd-run --quiet --unit=wisp-perf-revert --on-active="$REVERT_S" sh -c "sh $RESTORE; rm -f $RESTORE" && echo "reverts by itself in ${REVERT_S}s"
}
case $CMD in
    show) show;;
    cpu-performance) save; echo performance > $CPU; arm; show;;
    gpu-performance) save; echo performance > $GPU; arm; show;;
    all-performance) save; echo performance > $CPU; echo performance > $GPU; arm; show;;
    env) save; echo "$B64" | base64 -d > $US; arm; show;;
    fex) save; echo "$JB64" | base64 -d > $FEXJ
         printf 'user_settings = {\n    "FEX_APP_CONFIG": "%s",\n}\n' "$FEXJ" > $US; arm; show; echo "FEX options:"; cat $FEXJ;;
    ui-freeze) save; P=$(pidof steamwebhelper); [ -n "$P" ] || { echo "no steamwebhelper to freeze"; exit 1; }; echo "$P" > $FROZEN; kill -STOP $P; arm; echo "frozen: $(echo $P | wc -w) steamwebhelper processes";;
    ui-pageout) save; P=$(pidof steamwebhelper); [ -n "$P" ] || { echo "no steamwebhelper to page out"; exit 1; }; echo "$P" > $FROZEN; kill -STOP $P; arm
        python3 - $P <<'PY'
import ctypes, os, sys
libc = ctypes.CDLL(None, use_errno=True)
SYS_PIDFD_OPEN, SYS_PROCESS_MADVISE, MADV_PAGEOUT = 434, 440, 21           # aarch64 numbers
class Iov(ctypes.Structure): _fields_ = [('base', ctypes.c_void_p), ('len', ctypes.c_size_t)]
def rss_kb(pid):
    try: return sum(int(l.split()[1]) for l in open('/proc/%d/status' % pid) if l.startswith(('RssAnon:', 'RssFile:', 'RssShmem:')))
    except OSError: return 0
before = after = 0
for pid in map(int, sys.argv[1:]):
    before += rss_kb(pid)
    fd = libc.syscall(SYS_PIDFD_OPEN, pid, 0)
    if fd < 0: continue
    ranges = []
    for line in open('/proc/%d/maps' % pid):
        f = line.split()
        if f[1] == '---p' or (len(f) > 5 and f[5] in ('[vvar]', '[vectors]', '[vsyscall]')): continue
        a, b = (int(x, 16) for x in f[0].split('-'))
        ranges.append((a, b - a))
    for i in range(0, len(ranges), 512):
        chunk = ranges[i:i + 512]
        iov = (Iov * len(chunk))(*[Iov(a, n) for a, n in chunk])
        libc.syscall(SYS_PROCESS_MADVISE, fd, iov, len(chunk), MADV_PAGEOUT, 0)
    os.close(fd)
    after += rss_kb(pid)
print('paged out: resident %d MB -> %d MB (%d processes)' % (before // 1024, after // 1024, len(sys.argv) - 1))
PY
        free -m | sed -n 2p;;
    ui-reclaim-on) save; [ -x /run/wisp-dev/wisp-ui-reclaim ] || { echo "no /run/wisp-dev/wisp-ui-reclaim on the device (copy the aarch64 build there first)"; exit 1; }
        systemctl stop wisp-dev-reclaim wisp-ui-reclaim 2>/dev/null          # the image's own daemon (enabled by default) would fight this one over the same cgroup
        systemd-run --quiet --unit=wisp-dev-reclaim --description="dev: UI reclaim" -E XDG_RUNTIME_DIR=/var/run/0-runtime-dir -E SWAYSOCK=/var/run/0-runtime-dir/sway-ipc.0.sock /run/wisp-dev/wisp-ui-reclaim $RFREEZE || exit 1
        sleep 1; n=$(wc -l < /sys/fs/cgroup/unified/wisp-ui/cgroup.procs 2>/dev/null || echo 0)
        [ "$n" -ge 6 ] || echo "WARNING: only $n process(es) in the wisp-ui cgroup: the web helpers were not moved while young, so the ceiling has little to act on (restart Steam with the daemon already running)"
        arm; echo "reclaim daemon running; helpers in wisp-ui: $n";;
    ui-thaw) [ -f $FROZEN ] && { kill -CONT $(cat $FROZEN) 2>/dev/null; rm -f $FROZEN; echo "thawed"; } || echo "nothing frozen";;
    default) restore; systemctl stop wisp-perf-revert.timer wisp-perf-revert.service 2>/dev/null; show;;
esac
REMOTE
