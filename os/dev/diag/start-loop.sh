#!/bin/sh
# Dev measurement: N plain Steam starts (no flag), classify each (VISIBLE / BLACK_UI / STALL) and, on a stall, sample where the renderer's
# CPU time goes (user vs kernel). Via systemd drop-in, always restores the flagged launcher. Output: /storage/wispos/dev/loop.out
N=${1:-8}; O=/storage/wispos/dev/loop.out; exec >$O 2>&1
D=/storage/wispos/dev; DROP=/run/systemd/system/wisp-steam.service.d; L=/usr/bin/wisp-steam-session
W=/storage/wispos/home/.local/share/Steam/logs/webhelper.txt
export XDG_RUNTIME_DIR=/var/run/0-runtime-dir WAYLAND_DISPLAY=wayland-1 SWAYSOCK=/var/run/0-runtime-dir/sway-ipc.0.sock DISPLAY=:0.0
restore() { systemctl stop wisp-steam; sleep 3; rm -f $D/NO-STEAM-FLAGS; sh /storage/.config/autostart/020-steam-flags; systemctl reset-failed wisp-steam 2>/dev/null; systemctl start --no-block wisp-steam; sleep 2; echo "restored: $(systemctl is-active wisp-steam)"; echo DONE; }
trap restore EXIT
touch $D/NO-STEAM-FLAGS
cp $L $D/session-plain.sh; chmod 755 $D/session-plain.sh
mkdir -p $DROP; printf '[Service]\nExecStart=\nExecStart=%s\n' "$D/session-plain.sh" > $DROP/10-steam-flags.conf; systemctl daemon-reload
cpu() { # pid -> "utime stime" in ticks summed over threads
  awk '{u+=$14; s+=$15} END {print u, s}' /proc/$1/task/*/stat 2>/dev/null; }
topweb() { for p in $(pidof steamwebhelper); do t=$(cpu $p); echo "$t $p"; done | sort -rn | awk '{print $3}' | head -1; }
i=0; vis=0; blk=0; stl=0
while [ $i -lt $N ]; do
  i=$((i+1)); systemctl stop wisp-steam; sleep 4
  woff=$(wc -c < $W); t0=$(date +%s); systemctl reset-failed wisp-steam 2>/dev/null; systemctl start --no-block wisp-steam
  k=0; ok=0; while [ $k -lt 40 ]; do sleep 3; k=$((k+1)); tail -c +$((woff+1)) $W | grep -q "MainMenu_uid2: AfterCreated" && { ok=1; break; }; done
  if [ $ok = 1 ]; then sleep 12; tb=$(swaymsg -t get_tree | wc -c); if [ "$tb" -gt 6500 ]; then r=VISIBLE; vis=$((vis+1)); else r=BLACK_UI; blk=$((blk+1)); fi
    echo "$(date +%T) #$i $r ui_after=$((k*3))s tree=$tb load=$(cut -d' ' -f1 /proc/loadavg) avail=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)MB"
  else stl=$((stl+1)); p=$(topweb); a=$(cpu $p); sleep 15; b=$(cpu $p)
    set -- $a $b; du=$(( $3 - $1 )); ds=$(( $4 - $2 ))
    echo "$(date +%T) #$i STALL no UI after 120s; hottest webhelper pid=$p: in 15 s user=${du} ticks kernel=${ds} ticks (100 ticks = 1 cpu-second); load=$(cut -d' ' -f1 /proc/loadavg) avail=$(awk '/MemAvailable/{print int($2/1024)}' /proc/meminfo)MB"
    echo "   top threads of $p:"; for t in /proc/$p/task/*; do echo "$(awk '{print $14+$15, $14, $15}' $t/stat) $(cat $t/comm)"; done | sort -rn | head -4 | sed 's/^/     /'
  fi
done
echo "SUMMARY plain starts: visible=$vis black_with_ui=$blk stalled=$stl of $N"
