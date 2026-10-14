#!/bin/sh
# Dev (never shipped), runs ON the device: the priority-flip "soft freeze" instead of the shipped cgroup freeze (operator 2026-10-13, docs/WISPOS-NOTES.md).
#   ui-flip.sh on       the Steam UI's memory ceiling follows focus (wisp-ui-reclaim --follow-focus, nothing frozen) and ui-prio.py flips thread priorities with the focus:
#                         game in front (or still launching)   UI nice UI_BACK=19    game nice GAME_FRONT=-20
#                         Steam in front                       UI nice UI_FRONT=-10   game nice GAME_BACK=0
#   ui-flip.sh off      back to the shipped policy: wisp-ui-reclaim --freeze, no priority daemon (helpers return to nice 0 only when Steam restarts; run `renice` yourself if needed)
#   ui-flip.sh status   what runs, the ceiling, the freeze flag and the nice values of the UI helpers
# Values come from the environment: UI_BACK GAME_FRONT UI_FRONT GAME_BACK (nice), CEIL_IDLE CEIL_GAME (bytes). Needs /storage/wispos/dev/{wisp-ui-reclaim,ui-prio.py} (pin-test.sh copies them).
export XDG_RUNTIME_DIR=/var/run/0-runtime-dir SWAYSOCK=/var/run/0-runtime-dir/sway-ipc.0.sock
W=/storage/wispos/dev
UI_BACK=${UI_BACK:-19}; GAME_FRONT=${GAME_FRONT:--20}; UI_FRONT=${UI_FRONT:--10}; GAME_BACK=${GAME_BACK:-0}
CEIL_IDLE=${CEIL_IDLE:-471859200}; CEIL_GAME=${CEIL_GAME:-262144000}
E="-E XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR -E SWAYSOCK=$SWAYSOCK"
stop_all() { systemctl stop wisp-ui-nice wisp-reclaim-dev 2>/dev/null; systemctl reset-failed wisp-ui-nice wisp-reclaim-dev 2>/dev/null; echo 0 > /sys/fs/cgroup/unified/wisp-ui/cgroup.freeze; }
status() {
    echo "reclaim: $(ps -eo args | grep '[w]isp-ui-reclaim' | grep -v 'sh -c' | cut -c1-120)"
    echo "prio:    $(ps -eo args | grep '[u]i-prio.py' | grep -v 'sh -c' | cut -c1-120)"
    echo "ceiling $(cat /sys/fs/cgroup/unified/wisp-ui/memory.high) freeze=$(cat /sys/fs/cgroup/unified/wisp-ui/cgroup.freeze)"
    for p in $(pidof steamwebhelper | tr ' ' '\n' | head -n 3); do echo "helper $p nice $(cut -d' ' -f19 /proc/$p/stat)"; done
}
case "$1" in
    on)
        for f in wisp-ui-reclaim ui-prio.py; do [ -e $W/$f ] || { echo "missing $W/$f"; exit 1; }; done
        stop_all
        systemd-run --unit=wisp-reclaim-dev --collect $E $W/wisp-ui-reclaim --follow-focus --high-idle "$CEIL_IDLE" --high-game "$CEIL_GAME" >/dev/null 2>&1
        systemd-run --unit=wisp-ui-nice --collect $E python3 -u $W/ui-prio.py --ui-back "$UI_BACK" --game-front "$GAME_FRONT" --ui-front "$UI_FRONT" --game-back "$GAME_BACK" >/dev/null 2>&1
        sleep 5; status ;;
    off)
        stop_all
        systemd-run --unit=wisp-reclaim-dev --collect $E /usr/bin/wisp-ui-reclaim --freeze --high-idle "$CEIL_IDLE" >/dev/null 2>&1
        sleep 2; status ;;
    status) status ;;
    *) echo "usage: ui-flip.sh on|off|status"; exit 2 ;;
esac
