#!/bin/bash
# Dev (never shipped), runs on the HOST: freeze versus CPU pinning for the Steam UI while a game is in front (operator idea 2026-10-13: "pin steamui processes onto the second and third core,
# saving the first core for gaming"). One arm per run:
#   os/dev/diag/pin-test.sh F|N|R|S|G|T|P [--measure 180]
#   F  the shipped policy: wisp-ui-reclaim --freeze (ceiling 450 MiB idle / 96 MiB in game, UI frozen while the game is in front); nothing pinned
#   N  the UI is NOT frozen (the ceiling only, as P) and nothing is pinned: the control for P (operator 2026-10-13: "do an a/b performance test assuming you disabled freeze/thaw logic")
#   R  as N, and every thread of every steamwebhelper process is kept at nice 19 (os/dev/diag/ui-nice.py; operator question 2026-10-13: "what about adjusting niceness to deprioritize steamui?")
#   S  a "soft freeze" (operator idea 2026-10-13): as R (web helpers at nice 19) and with a gentler in-game memory ceiling of 250 MiB (--high-game 262144000) instead of 96 MiB
#   G  as S, and the GAME at nice -20 (launch option `nice -n -20 %command%`; operator question 2026-10-13: "what if we also applied -20 niceness to the game")
#   T  the soft freeze that FOLLOWS FOCUS (operator 2026-10-13: "we flip these values during ui interaction like qam and menu button"): as S, but the ceiling follows the focus (wisp-ui-reclaim
#      --follow-focus: 250 MiB while a game is in front, 450 MiB when Steam is) and os/dev/diag/ui-prio.py flips the priorities: game in front UI nice 19 / game nice 0; Steam in front UI nice 0 / game nice 19
#   P  the UI is NOT frozen (wisp-ui-reclaim without --freeze: the ceiling only); Steam's UI (the client and its web helpers) is pinned to cores 2-3 (a /run drop-in CPUAffinity=2-3 on
#      wisp-steam.service); the game is pinned to cores 0-1 (launch option `taskset -c 0-1 %command%`)
# Game: Worms Armageddon (217200) at its splash: the game renders the same picture in a loop, so the Mesa Gallium HUD's fps (GALLIUM_HUD_DUMP_DIR writes the samples to files) is a direct measure
# of the CPU the game's render path gets while the UI does or does not compete. NOT a game benchmark in the usual sense: it compares the two policies under the same load.
# Per arm: the HUD fps over the measurement window, the per-core CPU, memory/CPU pressure and swap traffic (ui-soak-stream.py), and the time from a tap of the pad's Steam button (wisp-home) until
# Steam's page for the game is drawn (the Resume button's blue at a fixed pixel). Output under $OUT. The launch option is the labelled DIAGNOSTIC kind: it is cleared at the end.
ARM=${1:?usage: pin-test.sh F|N|R|S|G|T|P [--measure SECONDS]}; MEASURE=180
[ "${2:-}" = --measure ] && MEASURE=${3:?}
HOST=${WISP_DEV_HOST:?set WISP_DEV_HOST to the handheld address}; KEY=${WISP_DEV_KEY:-$HOME/.ssh/wispos-dev}; ROOT=$(cd "$(dirname "$0")/../../.." && pwd)
OUT=${OUT:-/mnt/hdd/wispos-staging/card-logs-20261012/stream/pin-$ARM-$(date +%H%M)}; mkdir -p "$OUT"
S() { ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=15 "root@$HOST" "$@"; }
say() { echo "$(date +%T) $*" | tee -a "$OUT/log.txt"; }
HUD=/storage/wispos/runtime/steam/steamapps/compatdata/217200/hud
ENVHUD="${EXTRA_ENV:+$EXTRA_ENV }GALLIUM_HUD=fps,frametime GALLIUM_HUD_PERIOD=500000 GALLIUM_HUD_DUMP_DIR=$HUD"
case $ARM in F|N|R|S|T) OPT="$ENVHUD %command%" ;; G) OPT="$ENVHUD nice -n -20 %command%" ;; P) OPT="$ENVHUD taskset -c 0-1 %command%" ;; *) echo "arm F, N, R, S, G, T or P"; exit 2 ;; esac
SWAYENV='export XDG_RUNTIME_DIR=/var/run/0-runtime-dir WAYLAND_DISPLAY=wayland-1 SWAYSOCK=/var/run/0-runtime-dir/sway-ipc.0.sock'

say "arm $ARM: preparing the device"
S "mkdir -p $HUD; rm -f $HUD/*; sh /storage/wispos/dev/diag/kill-game.sh >/dev/null 2>&1; systemctl stop wisp-journey 2>/dev/null; true" >/dev/null 2>&1
RECLAIM_ARGS="--high-idle 471859200"; { [ "$ARM" = S ] || [ "$ARM" = G ] || [ "$ARM" = T ]; } && RECLAIM_ARGS="$RECLAIM_ARGS --high-game 262144000"
RECLAIM_BIN=/usr/bin/wisp-ui-reclaim; if [ "$ARM" = T ]; then RECLAIM_BIN=/storage/wispos/dev/wisp-ui-reclaim; RECLAIM_ARGS="--follow-focus $RECLAIM_ARGS"; scp -q -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "${WISP_RECLAIM_BIN:?set WISP_RECLAIM_BIN to the aarch64 wisp-ui-reclaim with --follow-focus}" "root@$HOST:$RECLAIM_BIN"; fi
if [ "$ARM" = P ] || [ "$ARM" = N ] || [ "$ARM" = R ] || [ "$ARM" = S ] || [ "$ARM" = G ] || [ "$ARM" = T ]; then
    if [ "$ARM" = P ]; then S 'mkdir -p /run/systemd/system/wisp-steam.service.d; printf "[Service]\nCPUAffinity=2-3\n" > /run/systemd/system/wisp-steam.service.d/pin.conf; systemctl daemon-reload' >/dev/null
    else S 'rm -f /run/systemd/system/wisp-steam.service.d/pin.conf; rmdir /run/systemd/system/wisp-steam.service.d 2>/dev/null; systemctl daemon-reload' >/dev/null; fi
    S 'systemctl stop wisp-reclaim-dev 2>/dev/null
       for p in $(pgrep -f "wisp-ui-reclaim"); do [ "$(tr "\0" " " < /proc/$p/cmdline | cut -c1-30)" = "/usr/bin/wisp-ui-reclaim --fre" ] && kill $p; done; sleep 2
       systemctl reset-failed wisp-reclaim-dev 2>/dev/null
       systemd-run --unit=wisp-reclaim-dev --collect -E XDG_RUNTIME_DIR=/var/run/0-runtime-dir -E SWAYSOCK=/var/run/0-runtime-dir/sway-ipc.0.sock '"$RECLAIM_BIN"' '"$RECLAIM_ARGS"' >/dev/null 2>&1; sleep 2
       ps -eo args | grep "[w]isp-ui-reclaim" | cut -c1-80' | tee -a "$OUT/log.txt"
else
    S 'rm -f /run/systemd/system/wisp-steam.service.d/pin.conf; rmdir /run/systemd/system/wisp-steam.service.d 2>/dev/null; systemctl daemon-reload
       systemctl stop wisp-reclaim-dev 2>/dev/null
       if ! ps -eo args | grep -q "[w]isp-ui-reclaim --freeze"; then systemd-run --unit=wisp-reclaim-dev --collect -E XDG_RUNTIME_DIR=/var/run/0-runtime-dir -E SWAYSOCK=/var/run/0-runtime-dir/sway-ipc.0.sock /usr/bin/wisp-ui-reclaim --freeze --high-idle 471859200 >/dev/null 2>&1; sleep 2; fi
       ps -eo args | grep "[w]isp-ui-reclaim" | cut -c1-80' | tee -a "$OUT/log.txt"
fi
say "setting the launch option (restarts Steam once): $OPT"
S "systemctl reset-failed wisp-opt 2>/dev/null; systemd-run --unit=wisp-opt --collect bash /storage/wispos/dev/diag/set-launch-option.sh 217200 '$OPT' >/dev/null 2>&1; echo started" >/dev/null
sleep 20
for i in $(seq 1 60); do [ "$(S 'systemctl is-active wisp-opt' 2>/dev/null)" != active ] && break; sleep 5; done
T_UP0=$(date +%s); say "Steam restarted; waiting for its UI (helpers up and steady)"
for i in $(seq 1 80); do
    read -r z c < <(S 'ps -eo args | grep -c "[s]teamwebhelper --type=zygote"; pidof steam | wc -w' 2>/dev/null | tr '\n' ' ')
    [ "${z:-0}" -ge 3 ] 2>/dev/null && [ "${c:-0}" -ge 1 ] 2>/dev/null && break; sleep 6
done
# the UI is ready when the machine has been quiet: load average (1 min) under 3.0 on three reads 10 s apart, at most 10 minutes (a launch while the UI is still loading answers no dialog: 2026-10-13)
quiet=0; for i in $(seq 1 60); do
    l=$(S 'cut -d" " -f1 /proc/loadavg' 2>/dev/null); if awk -v l="${l:-9}" 'BEGIN{exit !(l < 3.0)}'; then quiet=$((quiet + 1)); else quiet=0; fi
    [ $quiet -ge 3 ] && [ $(( $(date +%s) - T_UP0 )) -ge 180 ] && break; sleep 10      # the load average lags: a restart looks quiet for the first minute, so at least 180 s after the restart as well
done
say "Steam up: $(S "$SWAYENV; echo affinity=\$(grep Cpus_allowed_list /proc/\$(pidof steam | cut -d' ' -f1)/status | cut -f2) load=\$(cut -d' ' -f1 /proc/loadavg) frozen=\$(cat /sys/fs/cgroup/unified/wisp-ui/cgroup.freeze)" 2>&1)"
if [ "$ARM" = T ]; then
    scp -q -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$ROOT/os/dev/diag/ui-prio.py" "root@$HOST:/storage/wispos/dev/ui-prio.py"
    S "$SWAYENV"'; systemctl stop wisp-ui-nice 2>/dev/null; systemctl reset-failed wisp-ui-nice 2>/dev/null; systemd-run --unit=wisp-ui-nice --collect -E XDG_RUNTIME_DIR=/var/run/0-runtime-dir -E SWAYSOCK=/var/run/0-runtime-dir/sway-ipc.0.sock python3 -u /storage/wispos/dev/ui-prio.py '"${UI_PRIO_ARGS:-}"' >/dev/null 2>&1; sleep 4; journalctl -u wisp-ui-nice -o cat --no-pager | tail -2' | tee -a "$OUT/log.txt"
elif [ "$ARM" = R ] || [ "$ARM" = S ] || [ "$ARM" = G ]; then
    scp -q -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$ROOT/os/dev/diag/ui-nice.py" "root@$HOST:/storage/wispos/dev/ui-nice.py"
    S 'systemctl stop wisp-ui-nice 2>/dev/null; systemctl reset-failed wisp-ui-nice 2>/dev/null; systemd-run --unit=wisp-ui-nice --collect python3 -u /storage/wispos/dev/ui-nice.py 19 2 >/dev/null 2>&1; sleep 4; journalctl -u wisp-ui-nice -o cat --no-pager | tail -2' | tee -a "$OUT/log.txt"
fi
(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ServerAliveInterval=20 "root@$HOST" "python3 -u - 15 $((MEASURE + 900))" < "$ROOT/os/dev/diag/ui-soak-stream.py" > "$OUT/soak.jsonl" 2> "$OUT/soak.err" &) ; sleep 5
say "launching Worms"
S "$SWAYENV; systemctl reset-failed wisp-journey 2>/dev/null; systemd-run --unit=wisp-journey --collect --setenv=APP=217200 --setenv=OBSERVE=15 bash /storage/wispos/dev/journey-launch.sh >/dev/null 2>&1; echo ok" >/dev/null
for i in $(seq 1 120); do
    L=$(S 'journalctl -u wisp-journey -o cat --no-pager | tail -3' 2>/dev/null)
    case "$L" in *"game window after"*|*"NO GAME"*) break ;; esac; sleep 8
done
say "$(echo "$L" | grep -E 'game window|NO GAME' | tail -1)"
S "sh /storage/wispos/dev/awake.sh $((MEASURE + 400)) >/dev/null 2>&1" >/dev/null
WARM=48; say "warming up 40 s"; sleep 40
FT="$HUD/frametime_(ms)"; t0=$(date +%s)
say "measuring $MEASURE s (game affinity: $(S 'for p in $(pgrep -f "WA.exe"); do grep Cpus_allowed_list /proc/$p/status | cut -f2; done | sort | uniq -c | tr "\n" " "'))"
sleep "$MEASURE"
S "cat '$FT'" > "$OUT/frametime-raw.txt" 2>/dev/null
python3 - "$OUT/frametime-raw.txt" "$((WARM + 0))" "$MEASURE" <<'PY' | tee -a "$OUT/log.txt"
import sys, statistics
ms = [float(l) for l in open(sys.argv[1]) if l.strip()]
skip, span = int(sys.argv[2]) * 1000.0, int(sys.argv[3]) * 1000.0
# the HUD file has one frame time per line and no clock: the window is the stretch of CUMULATIVE frame time from `skip` ms after the first frame to `span` ms later
cum, win = 0.0, []
for v in ms:
    cum += v
    if skip <= cum < skip + span: win.append(v)
w = sorted(win)
if w: print('frames in the window: %d over %.0f s: mean fps %.1f; frame time median %.1f ms, p95 %.1f ms, p99 %.1f ms, max %.1f ms; 1%% low %.1f fps' % (
    len(win), sum(win) / 1000, 1000.0 * len(win) / sum(win), statistics.median(win), w[int(len(w) * .95) - 1], w[int(len(w) * .99) - 1], w[-1], 1000.0 / w[int(len(w) * .99) - 1]))
else: print('NO HUD SAMPLES')
PY
say "tap test: the Steam button, then wait for the game's page (Resume button drawn)"
tt=$(date +%s)
S "$SWAYENV; /storage/wispos/dev/wisp-dev-pad press mode --hold 150 >/dev/null 2>&1" >/dev/null
found=-1
for i in $(seq 1 40); do
    px=$(S "$SWAYENV; grim -g '24,213 1x1' -t ppm - 2>/dev/null | tail -c 3 | od -An -tu1" 2>/dev/null)
    set -- $px; if [ $# -ge 3 ] && [ "$3" -gt 220 ] && [ "$2" -gt 130 ] && [ "$1" -lt 60 ]; then found=$(( $(date +%s) - tt )); break; fi
    sleep 3
done
say "time from the tap to the game's page drawn: ${found}s (-1 = not within 120 s); last pixel: $px"
S "$SWAYENV; grim -l 1 /storage/wispos/shots/pin-$(basename "$OUT")-page.png" >/dev/null 2>&1
# how fast the Steam UI answers pad presses with the game running behind it (operator 2026-10-13: niceness is meant to buy UI responsiveness, and nothing measured that)
scp -q -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$ROOT/os/dev/diag/ui-response.sh" "root@$HOST:/storage/wispos/dev/ui-response.sh" || say "PROBE NOT RUN: could not copy ui-response.sh to the device"
say "UI responsiveness probe: 10 pad presses on the page's tab row, game running behind"
S "$SWAYENV; sh /storage/wispos/dev/ui-response.sh 10" 2>&1 | tee -a "$OUT/log.txt"
S "$SWAYENV; /storage/wispos/dev/wisp-dev-pad press mode --hold 150 >/dev/null 2>&1; sleep 4; sh /storage/wispos/dev/diag/kill-game.sh >/dev/null 2>&1" >/dev/null 2>&1
sleep 10
S "systemctl stop wisp-ui-nice 2>/dev/null; true" >/dev/null 2>&1
say "game stopped; clearing the diagnostic launch option and the pin drop-in (one Steam restart)"
S "rm -f /run/systemd/system/wisp-steam.service.d/pin.conf; rmdir /run/systemd/system/wisp-steam.service.d 2>/dev/null; systemctl daemon-reload; systemctl reset-failed wisp-opt 2>/dev/null; systemd-run --unit=wisp-opt --collect bash /storage/wispos/dev/diag/set-launch-option.sh 217200 '' >/dev/null 2>&1; echo started" >/dev/null
python3 - "$OUT/soak.jsonl" "$t0" "$MEASURE" <<'PY' | tee -a "$OUT/log.txt"
import json, sys
rows = [json.loads(l) for l in open(sys.argv[1]) if l.startswith('{')]
t0, m = int(sys.argv[2]), int(sys.argv[3])
w = [r for r in rows if t0 <= r['t'] <= t0 + m]
if w:
    cores = list(zip(*[r['cpu'] for r in w]))
    print('per-core CPU%% (mean over the window): %s' % ' '.join('cpu%d=%.0f' % (i, sum(c) / len(c)) for i, c in enumerate(cores)))
    print('memory PSI some: mean %.1f max %.1f | cpu PSI some: mean %.1f | swap in/out max %d/%d pages/s | asserts %d -> %d' % (
        sum(r['psi']['memory'][0] or 0 for r in w) / len(w), max(r['psi']['memory'][0] or 0 for r in w), sum(r['psi']['cpu'][0] or 0 for r in w) / len(w),
        max(r['swapin'] for r in w), max(r['swapout'] for r in w), w[0]['dumps'], w[-1]['dumps']))
PY
say "arm $ARM done; results in $OUT"
