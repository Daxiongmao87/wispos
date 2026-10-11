#!/bin/bash
# Dev: time a Steam-launched game from the Play press to a NEW game window, with Steam's own timestamped launch lines. Runs ON the device (copy to /storage/wispos/dev/).
# Precondition: the game's page is open in the Steam UI with Play focused (a clean state: the script first kills leftovers with kill-game.sh).
# Why this way: detecting the game by process names was fooled by leftover processes several times (docs/WISPOS-NOTES.md, playbook); a NEW sway container id for the
# game's window class and the timestamps Steam itself writes cannot be. Usage: launch-time.sh [APPID=217200] [OUTPUT_SCALE=1]; leaves the game killed and the output at scale 2.
APP=${1:-217200}; SCALE=${2:-1}
export XDG_RUNTIME_DIR=/var/run/0-runtime-dir SWAYSOCK=/var/run/0-runtime-dir/sway-ipc.0.sock WAYLAND_DISPLAY=wayland-1
P=/storage/wispos/dev/wisp-dev-pad; S=/storage/wispos/home/.local/share/Steam/logs
ids() { swaymsg -t get_tree 2>/dev/null | python3 -c "
import json,sys
t=json.load(sys.stdin); out=[]
def w(n):
    if n.get('window_properties',{}).get('class')=='steam_app_$APP': out.append(str(n['id']))
    for c in n.get('nodes',[])+n.get('floating_nodes',[]): w(c)
w(t); print(' '.join(out))"; }
sh /storage/wispos/dev/kill-game.sh >/dev/null 2>&1; sleep 4
before=$(ids); echo "steam_app_$APP windows before: [$before]"
swaymsg output DSI-1 scale "$SCALE" >/dev/null; /storage/wispos/dev/wisp-screen wake; sleep 3
g=$S/gameprocess_log.txt; c=$S/console_log.txt; g0=$(wc -l < $g); c0=$(wc -l < $c)
t0=$(date +%s); $P press a; echo "pressed Play $(date +%T)"
new=""
while [ $(( $(date +%s) - t0 )) -lt 330 ]; do
  for i in $(ids); do case " $before " in *" $i "*) ;; *) new=$i;; esac; done
  [ -n "$new" ] && break
  sleep 3
done
echo "NEW game window id=${new:-none} after $(( $(date +%s) - t0 )) s"
echo "--- Steam console log (launch tasks):"; tail -n +$((c0+1)) $c | grep -a "LaunchApp changed task\|Running install script" | cut -c1-120 | head -8
echo "--- Steam tracked-process log (first 6 lines):"; tail -n +$((g0+1)) $g | grep -a "adding PID" | cut -c1-70 | head -6
sleep 6; sh /storage/wispos/dev/kill-game.sh >/dev/null 2>&1; sleep 3; swaymsg output DSI-1 scale 2 >/dev/null
