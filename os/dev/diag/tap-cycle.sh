#!/bin/bash
# Dev (never shipped), runs ON the device with a game already running (journey-launch.sh): taps the Steam button N times (Steam page <-> game) and, each time Steam is shown,
# records what could explain a Steam page that is drawn but ignores the pad (docs/WISPOS-NOTES.md, 2026-10-13): the Resume pixel (blue 26,159,255 = pad focus, grey = none),
# sway's focused window, X input focus + pointer position (xfocus.py), then one `right` press and the pixel again.
# usage: tap-cycle.sh [N=6] [WAIT=8]
export XDG_RUNTIME_DIR=/var/run/0-runtime-dir SWAYSOCK=/var/run/0-runtime-dir/sway-ipc.0.sock WAYLAND_DISPLAY=wayland-1 DISPLAY=:0
P=/storage/wispos/dev/wisp-dev-pad; N=${1:-6}; WAIT=${2:-8}; W=/storage/wispos/dev
px() { set -- $(grim -g '24,213 1x1' -t ppm - 2>/dev/null | tail -c 3 | od -An -tu1); echo "$1,$2,$3"; }
swayfocus() { swaymsg -t get_tree -r | python3 -c '
import json,sys
def f(n):
    if n.get("focused") and not n.get("nodes"): return (n.get("window_properties") or {}).get("class") or n.get("app_id") or "?"
    for c in (n.get("nodes") or []) + (n.get("floating_nodes") or []):
        r = f(c)
        if r: return r
print(f(json.load(sys.stdin)) or "none")'; }
for i in $(seq 1 "$N"); do
    $P press mode --hold 150 >/dev/null 2>&1; sleep "$WAIT"
    a=$(px); sf=$(swayfocus); xf=$(python3 $W/xfocus.py 2>&1)
    $P press right >/dev/null 2>&1; sleep 2; b=$(px)
    echo "cycle $i: Steam shown | pixel $a -> after right $b | sway focus=$sf | X $xf"
    $P press mode --hold 150 >/dev/null 2>&1; sleep "$WAIT"
    echo "cycle $i: back to game | sway focus=$(swayfocus) | X $(python3 $W/xfocus.py 2>&1)"
done
