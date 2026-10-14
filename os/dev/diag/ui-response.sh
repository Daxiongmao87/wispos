#!/bin/sh
# Dev probe (never shipped), runs ON the device: how quickly does the Steam UI answer a pad press while a game is running behind it?
# Needs: the game's details page showing (the tap test of pin-test.sh gets there), the dev pad started (wisp-dev-pad start --clone "H700 Gamepad").
# It alternates right/left N times; for each press it times press -> first changed frame in the strip of the page that holds the Resume/stop buttons and the tab row
# (a small region, so the Steam UI's ambient animation elsewhere cannot trigger it; uilat.sh could not use the whole screen for that reason). Whatever has focus there
# (the Resume button, the stop button, a tab) moves or its highlight changes with every press, so the probe does not depend on where the focus starts. A first press in
# mouse mode only brings the focus back: it still changes the strip.
# The caller (pin-test.sh) has already waited for the page; if it is NOT showing, no press changes the strip and every result is NONE (a visible failure, not a number).
# usage: ui-response.sh [N=10]     prints: press->change ms: ... | n median p90 worst
export XDG_RUNTIME_DIR=/var/run/0-runtime-dir WAYLAND_DISPLAY=wayland-1
P=/storage/wispos/dev/wisp-dev-pad; N=${1:-10}
ms() { awk '{printf "%d", $1*1000}' /proc/uptime; }
# grim takes region coordinates in LAYOUT pixels: 720x480 at output scale 1 (a game owns the screen or is still starting), 360x240 at scale 2 (the Steam UI's default). The strip below
# is measured on 720x480 screenshots, so it is divided by the scale; at scale 2 an unscaled strip lies off the screen and every press reads as "no change" (found 2026-10-13).
export SWAYSOCK=/var/run/0-runtime-dir/sway-ipc.0.sock
SC=$(swaymsg -t get_outputs -r 2>/dev/null | awk -F'[:,]' '/"scale"/ {gsub(/ /,"",$2); print int($2 + 0.5); exit}'); [ "${SC:-0}" -ge 1 ] || SC=1
STRIP="0,$((198 / SC)) $((720 / SC))x$((72 / SC))"          # Resume / stop buttons (y~213) and the tab row (y~253), 720x480 coordinates divided by the scale
R=/run/uiresp_ref.ppm; C=/run/uiresp_cur.ppm
sleep 2
lat=""
for k in $(seq 1 "$N"); do
    if [ $((k % 2)) -eq 1 ]; then btn=right; else btn=left; fi
    grim -g "$STRIP" -t ppm "$R"; s=$(ms); $P press $btn >/dev/null 2>&1
    n=0; while :; do
        grim -g "$STRIP" -t ppm "$C"
        if ! cmp -s "$R" "$C"; then lat="$lat $(( $(ms) - s ))"; break; fi
        n=$((n + 1)); [ $n -ge 40 ] && { lat="$lat NONE"; grim -t png /storage/wispos/shots/uiresp-none-$k.png 2>/dev/null; break; }
    done
    sleep 2
done
rm -f "$R" "$C"
sorted=$(for x in $lat; do echo $x; done | grep -v NONE | sort -n)
cnt=$(echo "$sorted" | grep -c .)
if [ "$cnt" -gt 0 ]; then
    med=$(echo "$sorted" | sed -n "$(( (cnt + 1) / 2 ))p"); p90=$(echo "$sorted" | sed -n "$(( (cnt * 9 + 9) / 10 ))p"); worst=$(echo "$sorted" | tail -n 1)
else med=-1; p90=-1; worst=-1; fi
echo "UIRESP press->change ms:$lat | n=$cnt/$N median=$med p90=$p90 worst=$worst"
