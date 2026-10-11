#!/bin/bash
# Dev: where does a Steam game launch spend its time? Presses Play (A on the virtual pad) on an open game page and logs the FIRST time each stage's process or Steam's
# own log line appears, to 1 s. Runs ON the device. Precondition: the game page is open with Play focused. Usage: launch-stages.sh [APPID=217200] [OUTPUT_SCALE (default: unchanged)]
# sway 1.11 keeps every IPC reply (~40 kB per get_tree, docs/WISPOS-NOTES.md "MEMORY FLOOR"): the window check below runs every 3 s, not faster.
# NEVER move the mouse before the press: a pointer move puts Steam's UI in mouse mode and takes the focus ring off Play, so the next pad press only re-enters controller
# mode and nothing launches (cost an hour on 2026-10-09). If the screensaver is up, end it with a synthetic mouse move, then press a harmless pad direction, check Play is
# highlighted (screenshot) and only then run this.
# Stages (process-argument patterns, checked every 0.5 s while the launch runs): the two reaper phases, pressure-vessel-wrap, pv-adverb, the locale compiler
# (localedef / pv-locale-gen), Proton's python launcher, the evaluator, wineserver, the game exe. Needs a clean start: leftovers are killed first and the script
# refuses to run if any remain (process-name detection was fooled by leftovers several times: docs/WISPOS-NOTES.md playbook).
APP=${1:-217200}; SCALE=${2:-}        # SCALE: leave empty to NOT touch the output scale (Steam's UI does not always relayout after a scale change and then swallows the Play press)
export XDG_RUNTIME_DIR=/var/run/0-runtime-dir SWAYSOCK=/var/run/0-runtime-dir/sway-ipc.0.sock WAYLAND_DISPLAY=wayland-1
P=/storage/wispos/dev/wisp-dev-pad; S=/storage/wispos/home/.local/share/Steam/logs; OUT=/tmp/stages.seen
live() { ps -o stat,args | grep -a "wineserver\|pv-adverb\|reaper SteamLaunch\|localedef\|pressure-vessel-wrap" | grep -v "grep\|Z<" | wc -l; }
sh /storage/wispos/dev/kill-game.sh >/dev/null 2>&1; sleep 4; sh /storage/wispos/dev/kill-game.sh >/dev/null 2>&1; sleep 3
[ "$(live)" -ne 0 ] && { echo "ABORT: leftover launch processes"; ps -o pid,stat,args | grep -a "wineserver\|pv-adverb\|reaper SteamLaunch\|localedef" | grep -v "grep\|Z<" | cut -c1-110; exit 1; }
[ -n "$SCALE" ] && swaymsg output DSI-1 scale "$SCALE" >/dev/null; /storage/wispos/dev/wisp-screen wake; sleep 3
ids() { swaymsg -t get_tree 2>/dev/null | python3 -c "
import json,sys
t=json.load(sys.stdin); out=[]
def w(n):
    if n.get('window_properties',{}).get('class')=='steam_app_$APP': out.append(str(n['id']))
    for c in n.get('nodes',[])+n.get('floating_nodes',[]): w(c)
w(t); print(' '.join(out))"; }
before=$(ids); : > $OUT; c0=$(wc -l < $S/console_log.txt)
t0=$(date +%s); $P press a; echo "t=0 pressed Play $(date +%T)"
new=""; round=0
while [ $(( $(date +%s) - t0 )) -lt 400 ]; do
  now=$(( $(date +%s) - t0 ))
  ps -o args 2>/dev/null | grep -a -v "grep\|launch-stages" | while IFS= read -r l; do
    case "$l" in
      *"reaper SteamLaunch"*Install=1*) k="reaper phase1 (install script)";;
      *"reaper SteamLaunch"*) k="reaper phase2 (game)";;
      *pressure-vessel-wrap*) k="pressure-vessel-wrap";;
      *pv-adverb*) k="pv-adverb";;
      *localedef*) k="localedef (locale compile)";;
      *pv-locale-gen*) k="pv-locale-gen";;
      *iscriptevaluator*) k="iscriptevaluator.exe";;
      *"/proton run"*) k="proton run (phase 1)";;
      *"/proton waitforexitandrun"*) k="proton waitforexitandrun (phase 2)";;
      *wineserver*) k="wineserver";;
      *"WA.exe"*) k="game exe";;
      *) k="";;
    esac
    [ -n "$k" ] && ! grep -a -q "^t=[0-9]*s $k\$" $OUT && echo "t=${now}s $k" >> $OUT
  done
  round=$((round+1)); if [ $((round % 6)) -eq 0 ]; then for i in $(ids); do case " $before " in *" $i "*) ;; *) new=$i;; esac; done; fi      # sway keeps every IPC reply: ask every 3 s, not every 0.5 s
  [ -n "$new" ] && break
  sleep 0.5
done
echo "NEW game window id=${new:-none} at t=$(( $(date +%s) - t0 ))s"
sort -t= -k2 -n $OUT
echo "--- Steam launch tasks:"; tail -n +$((c0+1)) $S/console_log.txt | grep -a "LaunchApp changed task" | sed 's/.*\[\(.*\)\] GameAction.*task to \([A-Za-z]*\).*/\1 \2/' | cut -c1-60
sleep 6; sh /storage/wispos/dev/kill-game.sh >/dev/null 2>&1; sleep 3; [ -n "$SCALE" ] && swaymsg output DSI-1 scale 2 >/dev/null
