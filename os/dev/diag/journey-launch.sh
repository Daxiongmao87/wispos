#!/bin/bash
# Dev (never shipped), runs ON the device: journey 3 of docs/WISPOS-JOURNEYS.md for ANY Steam game, with NOTHING preset: the display scale is left to the shipped mechanism
# (the patched launcher + wisp-game-display), unlike game-first-launch.sh which switches the scale itself. Detached:
#   systemd-run --unit=wisp-journey --collect --setenv=APP=217200 bash /storage/wispos/dev/journey-launch.sh ; journalctl -fu wisp-journey -o cat
# What it does: wakes the UI (Steam's screensaver swallows launch requests; three B presses), opens steam://rungameid/APP, answers the two dialogs Steam may show (CD Keys: down, down, A;
# On-Screen Keyboard notice: A) only when Steam's log says it is waiting, waits for a NEW sway window of class steam_app_APP (LAUNCH_TIMEOUT s, default 900), then every SHOT_EVERY s
# (default 15) for OBSERVE s (default 90) prints the output scale, the game window's size and whether a live game process remains, and takes a screenshot (/storage/wispos/shots/journey-APP/).
# The game is LEFT RUNNING: leave it from the game's own menu (the journey) or kill it with kill-game.sh. Env WIN_CLASS overrides the window class (native Linux games).
APP=${APP:?set APP=<steam appid>}; LAUNCH_TIMEOUT=${LAUNCH_TIMEOUT:-900}; OBSERVE=${OBSERVE:-90}; SHOT_EVERY=${SHOT_EVERY:-15}; CLASS=${WIN_CLASS:-steam_app_$APP}
export XDG_RUNTIME_DIR=/var/run/0-runtime-dir SWAYSOCK=/var/run/0-runtime-dir/sway-ipc.0.sock WAYLAND_DISPLAY=wayland-1 DISPLAY=:0
W=/storage/wispos/dev; P=$W/wisp-dev-pad; D=/storage/wispos/shots/journey-$APP; mkdir -p "$D"; rm -f "$D"/*.png
C=/storage/wispos/home/.local/share/Steam/logs/console_log.txt
sh $W/diag/awake.sh $((OBSERVE + 600)) >/dev/null 2>&1; $P start >/dev/null 2>&1
for i in 1 2 3; do $P press b >/dev/null 2>&1; sleep 2; done; sleep 2
scale() { swaymsg -t get_outputs -r | grep -m1 '"scale"' | tr -d ' ,"scale:'; }
win() { swaymsg -t get_tree -r | python3 -c "
import json,sys
t=json.load(sys.stdin); out=[]
def w(n):
    if (n.get('window_properties') or {}).get('class')=='$CLASS': r=n['rect']; out.append('%dx%d%s' % (r['width'], r['height'], ' focused' if n.get('focused') else ''))
    for c in n.get('nodes',[])+n.get('floating_nodes',[]): w(c)
w(t); print(','.join(out))"; }
mark=$(wc -l < $C); t0=$(date +%s)
echo "$(date +%T) opening steam://rungameid/$APP (scale $(scale))"
sh $W/diag/steam-open.sh steam://rungameid/$APP 2>&1 | tail -1
last_cd=0; last_int=0; relaunches=0; w1=""
cnt() { echo "$new" | grep -a -c "$1"; }
while [ $(( $(date +%s) - t0 )) -lt $LAUNCH_TIMEOUT ]; do
    sleep 3; new=$(tail -n +$((mark + 1)) $C); now=$(date +%s)
    # A dialog is answered whenever Steam is WAITING for it (more "waiting for user response" lines than "continues with user response" ones), at most every 8 s: the first press can be swallowed
    # while the dialog animates in, and a Back press (the wake-up presses) cancels it. CD Keys: down, down, A (Close); On-Screen Keyboard notice: A.
    if [ "$(cnt 'waiting for user response to ShowCDKey')" -gt "$(cnt 'continues with user response "ShowCDKey"')" ] && [ $((now - last_cd)) -ge 8 ]; then
        sleep 2; $P press down; sleep 1; $P press down; sleep 1; $P press a; last_cd=$now; echo "$(date +%T) CD Keys dialog answered"; fi
    if [ "$(cnt 'waiting for user response to ShowInterstitials')" -gt "$(cnt 'continues with user response "ShowInterstitials"')" ] && [ $((now - last_int)) -ge 8 ]; then
        sleep 2; $P press a; last_int=$now; echo "$(date +%T) interstitial answered"; fi
    # the launch was released without a game process (a cancelled dialog, a swallowed request): open it again, at most twice, after 150 s without a game process
    if [ $((now - t0)) -ge $((150 * (relaunches + 1))) ] && [ $relaunches -lt 2 ] && ! echo "$new" | grep -a -q "Game process added"; then
        relaunches=$((relaunches + 1)); echo "$(date +%T) no game process yet: opening the launch again ($relaunches)"
        for k in 1 2 3; do $P press b >/dev/null 2>&1; sleep 2; done; mark=$(wc -l < $C); sh $W/diag/steam-open.sh steam://rungameid/$APP 2>&1 | tail -1
    fi
    # sway keeps about 8 times the size of every reply it gives (docs/WISPOS-NOTES.md): asking for the tree every 3 s for minutes, over many runs, swelled its heap to 535 MB (2026-10-13),
    # so the window is looked for every 15 s only
    tick=$(( ${tick:-0} + 1 )); if [ $((tick % 5)) -eq 0 ]; then w1=$(win); [ -n "$w1" ] && break; fi
done
if [ -z "$w1" ]; then echo "$(date +%T) NO GAME WINDOW after $(( $(date +%s) - t0 )) s (scale $(scale))"; grim "$D/s00-no-window.png"; exit 1; fi
echo "$(date +%T) game window after $(( $(date +%s) - t0 )) s: $w1, scale $(scale)"
n=0; e=0
while [ $e -le $OBSERVE ]; do
    n=$((n + 1)); grim "$D/s$(printf %02d $n).png"
    echo "$(date +%T) +${e}s scale $(scale) window [$(win)] game processes $(ps -o args | grep -a -c "steamapps/common/[^ ]*" )"; sleep $SHOT_EVERY; e=$((e + SHOT_EVERY))
done
echo "$(date +%T) done: game left running; panel held awake for $((OBSERVE + 600)) s from the start"
