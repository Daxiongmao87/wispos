#!/bin/bash
# Dev: first launch of ANY Steam game with the shipped defaults, then watch it without touching it. Runs ON the device (copy to /storage/wispos/dev/), detached:
#   systemd-run --unit=wisp-game-launch --collect --setenv=APP=282030 bash /storage/wispos/dev/game-first-launch.sh; journalctl -fu wisp-game-launch -o cat
# Precondition: the game's library page is open in Steam's UI with Play focused (check with a screenshot; the screen awake and NOT on Steam's screensaver, docs/WISPOS-NOTES.md
# playbook item 6), no launch option set (this is the shipped-defaults run). Why it exists: the second, unrelated game (the operator's rule: a core fix counts only after a second
# game confirms it) needs the same questions answered as Worms did, without Worms' menu clicks: does it launch, how long to the window, what is on screen, does it stay alive.
# What it does: stops leftovers, presses Play on the virtual pad, switches the output to the native scale 1 three seconds later (the stand-in for the per-game scale switch that is not
# built; the same one worms-fps.sh uses), waits for a NEW sway window of class steam_app_$APP (LAUNCH_TIMEOUT s, default 900), then takes a full screenshot every SHOT_EVERY s (default 15)
# for OBSERVE s (default 120) and logs whether a live (non-zombie) game process remains. The game is LEFT RUNNING (kill it with kill-game.sh); the panel is held on for AWAKE_AFTER s (default 60) and then blanks by itself
# (awake.sh; never an open-ended inhibit) and the output stays at scale 1 so a person or I can look and drive it. Screenshots: /storage/wispos/shots/game-$APP/sNN.png. Env GAME_PAT='regex' names the game's process for the liveness check
# (default: the exe path under steamapps/common).
# Native Linux games (run by Valve's FEX tool) keep their own X window class (Half-Life: hl_linux): pass WIN_CLASS=hl_linux; the focus step below then also uses it.
# No game-specific input here: any menu driving is done by hand afterwards with xin.py (a test driver, never an input mapping).
APP=${APP:?set APP=<steam appid>}; LAUNCH_TIMEOUT=${LAUNCH_TIMEOUT:-900}; OBSERVE=${OBSERVE:-120}; SHOT_EVERY=${SHOT_EVERY:-15}
export XDG_RUNTIME_DIR=/var/run/0-runtime-dir SWAYSOCK=/var/run/0-runtime-dir/sway-ipc.0.sock WAYLAND_DISPLAY=wayland-1 DISPLAY=:0
P=/storage/wispos/dev/wisp-dev-pad; D=/storage/wispos/shots/game-$APP; mkdir -p $D; rm -f $D/*.png
/storage/wispos/dev/awake.sh 60      # short: the panel may blank while the game starts; it is held again only while screenshots are taken (awake.sh, never open-ended)
ids() { swaymsg -t get_tree 2>/dev/null | python3 -c "
import json,sys
t=json.load(sys.stdin); out=[]
def w(n):
    if n.get('window_properties',{}).get('class') in ('steam_app_$APP', '${WIN_CLASS:-steam_app_$APP}'): out.append(str(n['id']))
    for c in n.get('nodes',[])+n.get('floating_nodes',[]): w(c)
w(t); print(' '.join(out))"; }
live() { ps -o stat,args | grep -a "${GAME_PAT:-steamapps/common}" | grep -a -v 'grep\|Z<\|reaper\|pressure-vessel\|pv-adverb\|steam-runtime' | wc -l; }
sh /storage/wispos/dev/kill-game.sh >/dev/null 2>&1; sleep 4
C=/storage/wispos/home/.local/share/Steam/logs/console_log.txt; T=$(date +%T)
# Steam may stop the launch on dialogs that wait for a button (seen with Quake III Arena, whose key is registered on the account): "CD Keys" (focus starts on the key row: down, down, A = Close; the
# "Don't show me this again" box is an account setting and is NOT ticked) and "On-Screen Keyboard" (A = OK). Steam's console log says when it waits, so each is answered only when it appears.
waitlog() { n=0; until awk -v t="$T" 'substr($2,1,8) >= t' $C | grep -a -q "$1"; do n=$((n+1)); [ $n -gt ${2:-60} ] && return 1; sleep 1; done; }
before=$(ids); t0=$(date +%s); $P press a; echo "pressed Play $(date +%T)"
sleep 3; swaymsg output DSI-1 scale 1 >/dev/null; echo "output scale 1 at $(date +%T)"
waitlog "waiting for user response to ShowCDKey" && { sleep 1; $P press down; $P press down; $P press a; echo "CD Keys dialog closed $(date +%T)"; }
waitlog "waiting for user response to ShowInterstitials" 90 && { sleep 1; $P press a; echo "On-Screen Keyboard notice closed $(date +%T)"; }
new=""; while [ $(( $(date +%s) - t0 )) -lt $LAUNCH_TIMEOUT ]; do for i in $(ids); do case " $before " in *" $i "*) ;; *) new=$i;; esac; done; [ -n "$new" ] && break; sleep 3; done
echo "game window id=${new:-none} after $(( $(date +%s) - t0 )) s ($(date +%T))"
if [ -z "$new" ]; then grim $D/s00-no-window.png; echo "NO WINDOW: screenshot s00-no-window.png; live game processes: $(live)"; exit 1; fi
swaymsg "[class=\"${WIN_CLASS:-steam_app_$APP}\"] focus" >/dev/null 2>&1
/storage/wispos/dev/awake.sh $((OBSERVE + 60))      # held only for the screenshots below
n=0; e=0; while [ $e -le $OBSERVE ]; do n=$((n+1)); grim $D/s$(printf %02d $n).png; echo "shot $n at +${e}s ($(date +%T)) live game processes: $(live)"; sleep $SHOT_EVERY; e=$((e+SHOT_EVERY)); done
/storage/wispos/dev/awake.sh ${AWAKE_AFTER:-60}; echo "done: game left running at scale 1; panel held on ${AWAKE_AFTER:-60} s more, then it blanks ($(date +%T))"
