#!/bin/bash
# Dev: frame rate of a Worms Armageddon deathmatch, read from the Gallium HUD labels on screen. Runs ON the device.
# Preconditions: the game's page is open with Play focused (controller mode, see launch-stages.sh), and the game's Steam launch option is `GALLIUM_HUD=fps,frametime %command% /nointro` (/nointro: Worms' documented switch to skip the intro, so no key is needed)
# (set with Steam stopped, in userdata/*/config/localconfig.vdf, app 217200 -> "LaunchOptions"). The output stays at the default half-resolution scale 2 while Steam's UI and the launch run
# (a quarter of the pixels of the native scale, so probably smaller UI buffers: compare with the all-at-scale-1 samples of 2026-10-09 in docs/WISPOS-NOTES.md) and goes to the native scale 1 three seconds after the Play press
# (the game needs the native output before it initialises graphics): the stand-in for the per-game scale switch that is not built yet. The default scale is restored on exit.
# The idle blanking is inhibited for the run (restored on exit). What it does: press Play, wait for the new game window, wait for the intro, skip it (Escape), click through Single player -> Deathmatch -> two teams -> Start (synthetic
# mouse/keyboard from xin.py: a TEST DRIVER, not an input mapping), let the match load, then crop the two HUD labels from 12 screenshots 4 s apart into
# Env LAUNCH_TIMEOUT=SECONDS: how long to wait for the game window (default 400; the first launch on a fresh card also creates the prefix and runs the install script once: allow 900). Env HOLD=1: stop at the main menu with the game running (drive it by hand with xin.py + grim). Env OBSERVE=N: watch the game N seconds before the first key (default 0). /storage/wispos/shots/fps/ (f01..f12 = fps label, t01..t12 = frame-time label; the HUD sits at the top left), plus one full screenshot per step (sNN-<step>.png): the clicks
# are blind and the Steam-launched game reaches its menu later than a hand-launched one, so read the step shots first. Stack the crops into one image on the host.
export XDG_RUNTIME_DIR=/var/run/0-runtime-dir SWAYSOCK=/var/run/0-runtime-dir/sway-ipc.0.sock WAYLAND_DISPLAY=wayland-1 DISPLAY=:0
APP=217200; P=/storage/wispos/dev/wisp-dev-pad; X="python3 /storage/wispos/dev/xin.py"; D=/storage/wispos/shots/fps
mkdir -p $D; rm -f $D/*.png
/storage/wispos/dev/awake.sh 1500; trap '/storage/wispos/dev/awake.sh 0; swaymsg output DSI-1 scale 2 >/dev/null' EXIT      # wisp-screen-idle blanks the panel after 120 s without a button press and grim cannot capture a blanked output
ids() { swaymsg -t get_tree 2>/dev/null | python3 -c "
import json,sys
t=json.load(sys.stdin); out=[]
def w(n):
    if n.get('window_properties',{}).get('class')=='steam_app_$APP': out.append(str(n['id']))
    for c in n.get('nodes',[])+n.get('floating_nodes',[]): w(c)
w(t); print(' '.join(out))"; }
sh /storage/wispos/dev/kill-game.sh >/dev/null 2>&1; sleep 4
before=$(ids); t0=$(date +%s); $P press a; echo "pressed Play $(date +%T)"
sleep 3; swaymsg output DSI-1 scale 1 >/dev/null; echo "output scale 1 at $(date +%T)"      # the game must see the native mode before it initialises graphics (a half-resolution X screen makes Worms refuse its video mode)
new=""; while [ $(( $(date +%s) - t0 )) -lt ${LAUNCH_TIMEOUT:-400} ]; do for i in $(ids); do case " $before " in *" $i "*) ;; *) new=$i;; esac; done; [ -n "$new" ] && break; sleep 3; done
echo "game window id=${new:-none} after $(( $(date +%s) - t0 )) s"; [ -z "$new" ] && exit 1
sleep 25; swaymsg "[class=\"steam_app_$APP\"] focus" >/dev/null 2>&1
n=0; shot() { n=$((n+1)); grim $D/s$(printf %02d $n)-$1.png; echo "step $n $1 $(date +%T) screen=$(/storage/wispos/dev/wisp-screen status 2>&1 | tr '\n' ' ' | cut -c1-60)"; }      # one screenshot per step: the clicks are blind, read these to see where a run went wrong
shot first-frame
OBSERVE=${OBSERVE:-0}; i=0; while [ $i -lt $((OBSERVE / 15)) ]; do sleep 15; i=$((i+1)); shot "observe-$i"; done      # OBSERVE=90: look at the game for 90 s without touching it (does it die by itself? 2026-10-10: it exited 15 s after the Escape)
[ "${ESCAPE:-0}" = 1 ] && $X key Escape      # opt-in: on the MAIN MENU Escape QUITS Worms (it did on 2026-10-10 when the launch was fast enough to be there already); the launch option /nointro starts there
sleep 12; shot main-menu
[ "${HOLD:-0}" = 1 ] && { trap - EXIT; /storage/wispos/dev/awake.sh 900; echo "holding: game left running at the main menu, output at scale 1, panel held on 900 s then it blanks; drive it with xin.py and look with grim"; exit 0; }      # HOLD=1: stop here for interactive driving
$X click 237 185; sleep 8; shot single-player
$X click 490 280; sleep 8; shot deathmatch
$X click 410 242; $X click 410 242; sleep 1; $X click 410 258; $X click 410 258; sleep 3; shot teams      # two teams from the barracks
$X click 512 378; echo "match started $(date +%T)"; sleep 40
shot match
for i in 01 02 03 04 05 06 07 08 09 10 11 12; do
  grim -g "50,108 210x20" $D/f$i.png; grim -g "50,248 210x20" $D/t$i.png; sleep 4
done
echo "samples written to $D; WA live: $(ps -o stat,args | grep -a '[W]A.exe' | grep -vc 'Z<')"
sh /storage/wispos/dev/kill-game.sh >/dev/null 2>&1
