#!/bin/bash
# Dev: watch a dev device WITHOUT writing to its card (operator, 2026-10-11: "we should write to disk sparingly ... capture the log stream over ssh as part of Dev").
#   os/dev/log-stream.sh [--host IP] [--for 8h] [--period 60] [--out DIR]
# Two ssh streams run for the length of --for and reconnect by themselves (a flaky Wi-Fi link, a Steam restart, a lock-up that comes back):
#   journal-<host>.log   `journalctl -f` of the device (kernel messages, every service, the flight recorder's lines), a "# ... connect/disconnected" line at each break; a reconnect
#                        asks for what it missed (--since, 5 s of overlap, so a few lines may repeat; the device journal is RAM only and holds about 2 MB)
#   mem-<host>.jsonl     one JSON line per --period from os/dev/diag/mem-stream.py: memory, zram, pressure, every Steam process, and the KiB written to the boot and TF2 cards
# Nothing is written on the device: the sampler is piped into `python3 -` over ssh and prints to the pipe, so it stops when the connection does (and by itself after --for).
# Everything lands on the HDD (docs/HDD-IMAGE-STORAGE.md), default /mnt/hdd/wispos-staging/card-logs-YYYYMMDD/stream/ ; a directory elsewhere is refused.
# Start it BEFORE a test that might wedge the device: the host keeps what was streamed up to the moment the device went quiet.
# WISP_DEV_HOST / WISP_DEV_KEY are the usual overrides; WISP_SSH (the ssh program), WISP_HDD (the HDD mount) and WISP_STREAM_RETRY exist for the test.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
HDD=${WISP_HDD:-/mnt/hdd}
KEY=${WISP_DEV_KEY:-$HOME/.ssh/wispos-dev}
HOST=${WISP_DEV_HOST:-}; FOR=8h; PERIOD=60; OUT=

die() { echo "log-stream: $*" >&2; exit 2; }
usage() { sed -n '2,3p' "$0" | cut -c3-; exit 0; }

secs() {                                   # 90 | 90s | 15m | 8h -> seconds
    case $1 in
        *h) echo $(( ${1%h} * 3600 ));; *m) echo $(( ${1%m} * 60 ));; *s) echo "${1%s}";; *) echo "$1";;
    esac
}

while [ $# -gt 0 ]; do
    case $1 in
        --host) [ $# -ge 2 ] || die "--host needs a value"; HOST=$2; shift;;
        --for) [ $# -ge 2 ] || die "--for needs a value"; FOR=$2; shift;;
        --period) [ $# -ge 2 ] || die "--period needs a value"; PERIOD=$2; shift;;
        --out) [ $# -ge 2 ] || die "--out needs a value"; OUT=$2; shift;;
        -h|--help) usage;;
        *) die "unknown argument: $1";;
    esac
    shift
done
case $(secs "$FOR") in ''|*[!0-9]*|0) die "--for must be a positive number of seconds, minutes (15m) or hours (8h)";; esac
case $PERIOD in ''|*[!0-9]*|0) die "--period must be a positive number of seconds";; esac
LIFE=$(secs "$FOR")

# The device's address: --host / WISP_DEV_HOST, else the listener's newest record (and say how old it is: the announcer only reaches a listener that is running).
if [ -z "$HOST" ]; then
    rec=$(python3 -I -c 'import json,os,time
d=json.load(open(os.path.expanduser("~/.wispos-dev/devices.json"))).values()
r=max(d,key=lambda v:v.get("last_seen",0)); print(r["ip"],int(time.time())-int(r.get("last_seen",0)))' 2>/dev/null) || die "no --host and no ~/.wispos-dev/devices.json"
    HOST=${rec% *}; age=${rec#* }
    [ "$age" -lt 900 ] || echo "log-stream: warning: $HOST comes from devices.json, last seen $(( age / 3600 )) h $(( age % 3600 / 60 )) min ago (the announcer needs 'python3 os/dev/listen.py' running); pass --host if it moved" >&2
fi
case $HOST in ''|*[!0-9A-Za-z.:_-]*) die "bad host: $HOST";; esac

# HDD rule: the directory must resolve under the HDD mount, and that mount must be a real mount point.
[ -n "$OUT" ] || OUT=$HDD/wispos-staging/card-logs-$(date +%Y%m%d)/stream
OUT=$(realpath -m "$OUT")
case $OUT in "$HDD"/*) ;; *) die "refusing $OUT: logs go on the HDD ($HDD), never the root filesystem";; esac
[ -z "${WISP_HDD:-}" ] && { mountpoint -q "$HDD" || die "$HDD is not mounted"; }
mkdir -p "$OUT" || die "cannot create $OUT"

SSH=("${WISP_SSH:-ssh}" -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o UserKnownHostsFile="$HOME/.wispos-dev/known_hosts" -o StrictHostKeyChecking=accept-new
     -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3 "root@$HOST")
END=$(( $(date +%s) + LIFE ))
RETRY=${WISP_STREAM_RETRY:-5}                 # seconds before the first reconnect (the test shortens it)

# One stream: stream NAME FILE COMMAND_FN STDIN_FILE. The remote command is built per attempt (a journal that lost its connection asks for what it missed).
# `since` only moves when bytes actually arrived, so failed attempts (device off, Wi-Fi down) do not skip the gap they could not read.
mark() {                                   # a break marker that keeps the file's own format: a # line in the log, a JSON object in the .jsonl
    if [ "${1##*.}" = jsonl ]; then printf '{"event":"%s","t":%d}\n' "$2" "$(date +%s)" >> "$1"; else echo "# $(date -Is) $2" >> "$1"; fi
}
stream() {
    local name=$1 file=$2 mkcmd=$3 stdin=$4 delay=$RETRY started rem cmd since= before after
    while [ "$(date +%s)" -lt "$END" ]; do
        rem=$(( END - $(date +%s) ))
        cmd=$($mkcmd "$since" "$rem")
        mark "$file" "connect ($name)"
        before=$(stat -c %s "$file"); started=$(date +%s)
        timeout "$rem" "${SSH[@]}" "$cmd" < "${stdin:-/dev/null}" >> "$file" 2>> "$OUT/stream-errors.log"
        rc=$?
        after=$(stat -c %s "$file")
        [ "$after" -gt "$before" ] && since=$(( $(stat -c %Y "$file") - 5 ))
        [ "$(date +%s)" -lt "$END" ] || break
        mark "$file" "disconnected ($name) rc=$rc"
        # a connection that lasted is retried quickly, one that failed at once backs off (5, 10, 20, 40, 60 s)
        if [ $(( $(date +%s) - started )) -ge 30 ]; then delay=$RETRY; fi
        sleep "$delay"
        delay=$(( delay * 2 > 12 * RETRY ? 12 * RETRY : delay * 2 ))
    done
}
journal_cmd() { if [ -n "$1" ]; then echo "journalctl -f -o short-iso --no-pager --since @$1"; else echo "journalctl -f -o short-iso --no-pager -n 40"; fi; }
sampler_cmd() { echo "python3 -u - $PERIOD $(( $2 + 120 ))"; }

pids=()
stop() { local p; for p in "${pids[@]}"; do pkill -TERM -P "$p" 2>/dev/null; kill "$p" 2>/dev/null; done; wait 2>/dev/null; }
trap 'stop; exit 130' INT TERM

echo "log-stream: $HOST -> $OUT for $FOR (until $(date -d "@$END" +%H:%M:%S)), sampler every ${PERIOD}s; files journal-$HOST.log, mem-$HOST.jsonl, stream-errors.log"
stream journal "$OUT/journal-$HOST.log" journal_cmd "" & pids+=($!)
stream sampler "$OUT/mem-$HOST.jsonl" sampler_cmd "$HERE/diag/mem-stream.py" & pids+=($!)
wait "${pids[@]}"
echo "log-stream: done $(date -Is)"
