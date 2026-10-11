#!/bin/bash
# Dev (game-specific test tool, never shipped): launch Quake III Arena (app 2200) through Steam with the shipped defaults and answer Steam's two first-launch dialogs, then wait for the demo.
# ON the device, detached:  systemd-run --unit=wisp-q3 --collect bash /storage/wispos/dev/timedemo-q3.sh ; journalctl -fu wisp-q3 -o cat
# Precondition: baseq3/autoexec.cfg holds the DIAGNOSTIC timedemo (`set r_fullscreen 0`, `set logfile 2`, `set timedemo 1`, `set nextdemo quit`, `demo four`): Steam asks for confirmation when a
# launch carries arguments in a steam:// URL ("Launch Game with custom arguments"), and a first launch of a plain game page held the previous attempt for 18 minutes, so the config file is used.
# Why this game: its QVM just-in-time compiler writes x86 into read/write heap memory, the pattern that died with FEX "NoExec instruction in entry block" until the stock FEX modules and Valve's
# wow64.dll were in the tool (docs/WISPOS-NOTES.md). PASS = `VM file ui compiled to N bytes of code` and then `frames, N seconds: X fps` in qconsole.log.
# Dialogs (from Steam's console log): ShowCDKey = down, down, A (Close; "Don't show me this again" stays unticked); ShowInterstitials = A. Prints what it did; leaves the game running.
APP=2200; S=/storage/wispos/runtime/steam; L=/storage/wispos/home/.local/share/Steam/logs/console_log.txt; D="$S/steamapps/common/Quake 3 Arena/baseq3"; P=/usr/bin/wisp-dev-pad
export XDG_RUNTIME_DIR=/var/run/0-runtime-dir WAYLAND_DISPLAY=wayland-1 SWAYSOCK=/var/run/0-runtime-dir/sway-ipc.0.sock
sh /storage/wispos/dev/diag/awake.sh 900 >/dev/null 2>&1; rm -f "$D/qconsole.log" "$D/q3config.cfg"
# Wake the UI first: after a few idle minutes Steam shows its screensaver ("Away"), and a launch request that arrives while it wakes is dropped without a trace (no GameAction line in
# console_log). Three Back presses wake it and leave any modal screen; then the launch is retried until Steam acknowledges it.
$P start >/dev/null 2>&1
for i in 1 2 3; do $P press b >/dev/null 2>&1; sleep 2; done; sleep 2
t0=$(date +%s); ok=0
for attempt in 1 2 3; do
    mark=$(wc -l < $L)
    sh /storage/wispos/dev/diag/steam-open.sh steam://rungameid/$APP 2>&1 | tail -1; echo "launched $(date +%T) (attempt $attempt)"
    for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
        sleep 3; tail -n +$((mark + 1)) $L | grep -a -q "GameAction \[AppID $APP" && { ok=1; break; }
    done
    [ $ok = 1 ] && break
    echo "Steam did not acknowledge the launch within 45 s: waking the UI and trying again"
    for i in 1 2 3; do $P press b >/dev/null 2>&1; sleep 2; done
done
[ $ok = 1 ] || { echo "FAILED: Steam never acknowledged the launch request"; exit 1; }
cd_done=0; int_done=0
while [ $(( $(date +%s) - t0 )) -lt 900 ]; do
    sleep 3; new=$(tail -n +$((mark + 1)) $L)
    # A dialog is answered, then the answer is CHECKED in Steam's log ("continues with user response"): the first press can be swallowed while the dialog animates in (it ticked
    # "Don't show me this again" once instead of pressing Close). Not answered within 8 s: one more Down + A, which is right when the first Down was the lost one.
    [ $cd_done = 0 ] && echo "$new" | grep -a -q "waiting for user response to ShowCDKey" && {
        sleep 3; $P press down; sleep 1; $P press down; sleep 1; $P press a; cd_done=1
        for i in 1 2 3 4; do sleep 2; tail -n +$((mark + 1)) $L | grep -a -q "continues with user response" && break; done
        tail -n +$((mark + 1)) $L | grep -a -q "continues with user response" || { echo "CD Keys not answered: Down + A again"; $P press down; sleep 1; $P press a; }
        echo "CD Keys dialog closed $(date +%T)"; }
    [ $int_done = 0 ] && echo "$new" | grep -a -q "waiting for user response to ShowInterstitials" && { sleep 2; $P press a; int_done=1; echo "interstitial closed $(date +%T)"; }
    ps | grep -q "[q]uake3" && { echo "quake3.exe running after $(( $(date +%s) - t0 )) s"; break; }
done
echo "done $(date +%T)"
