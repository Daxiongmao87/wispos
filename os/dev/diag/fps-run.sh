#!/bin/bash
# Dev: the whole Worms frame-rate procedure, ON the device, detached from any ssh session (copy to /storage/wispos/dev/fps-run.sh, then:
#   systemd-run --unit=wisp-fps-run --collect bash /storage/wispos/dev/fps-run.sh >/dev/null; journalctl -fu wisp-fps-run -o cat
# Why detached: a run started through `ssh host bash -s < script` ended when that ssh session did (2026-10-10 00:24, mid-menu), and the game then ran on unobserved.
# What it does: stops Steam, sets Worms' launch option to `GALLIUM_HUD=fps,frametime %command% /nointro` (Steam stopped: it rewrites localconfig.vdf), starts Steam at the default
# output scale 2 and waits for its UI (restarting it up to 2 times if the UI stalls black: no new console-log line for 6 minutes), starts mem-sample.py, opens the game page and runs
# worms-fps.sh (which switches to scale 1 after Play). Env LAUNCH_EXTRA='K=V K=V' adds diagnostic environment settings to the launch option for that run (systemd-run --setenv=LAUNCH_EXTRA=...). Leaves Steam running with the HUD option set: remove it from localconfig.vdf (Steam stopped) before normal use.
# Env PREP_ONLY=1 stops at the home screen after the UI is ready, so a person (or I) can look at the screen before pressing anything (a fresh account). Output: the unit's journal (`journalctl -u wisp-fps-run -o cat`; it keeps earlier invocations too), screenshots and HUD crops in /storage/wispos/shots/fps/, memory samples in /storage/wispos/logs/mem-sample.log.
export XDG_RUNTIME_DIR=/var/run/0-runtime-dir SWAYSOCK=/var/run/0-runtime-dir/sway-ipc.0.sock WAYLAND_DISPLAY=wayland-1 DISPLAY=:0      # a systemd-run unit has no session environment
P=/storage/wispos/dev/wisp-dev-pad; S=/storage/wispos/home/.local/share/Steam; L=$S/logs
touch /run/wisp-steam-watch-off
systemctl stop wisp-mem-sample 2>/dev/null; : > /storage/wispos/logs/mem-sample.log
systemd-run --quiet --collect --unit=wisp-mem-sample python3 /storage/wispos/dev/mem-sample.py /storage/wispos/logs/mem-sample.log 3
/storage/wispos/dev/awake.sh 1500
systemctl stop wisp-steam; n=0; while [ -n "$(pidof steam)" ] && [ $n -lt 30 ]; do sleep 2; n=$((n+1)); done
sh /storage/wispos/dev/kill-game.sh >/dev/null 2>&1
F=$(ls $S/userdata/*/config/localconfig.vdf | head -1)
python3 - "$F" <<'PY'
import sys,re
p=sys.argv[1]; s=open(p).read()
s=re.sub(r'\t+"LaunchOptions"\t\t"GALLIUM_HUD[^\n]*\n','',s)
m=re.search(r'(\t+)"217200"\n\1\{\n', s); assert m
import os
extra=os.environ.get('LAUNCH_EXTRA','').strip()      # DIAGNOSTIC environment settings for one run, e.g. FEX_HALFBARRIERTSOENABLED=0 MESA_SHADER_CACHE_DISABLE=true (never shipped; North Star standing rule)
opt=('GALLIUM_HUD=fps,frametime '+(extra+' ' if extra else '')+'%command% /nointro').replace('%','%%')
s=s[:m.end()]+'%s"LaunchOptions"\t\t"%s"\n' % (m.group(1)+'\t', opt.replace('%%','%'))+s[m.end():]
open(p,'w').write(s); print('HUD launch option set')
PY
swaymsg output DSI-1 scale 2 >/dev/null
tries=0
while :; do
  c0=$(wc -l < $L/console_log.txt); systemctl start wisp-steam --no-block; echo "started $(date +%T) try $tries"
  n=0; while ! tail -n +$((c0+1)) $L/console_log.txt 2>/dev/null | grep -a -q "Apps.RegisterForAppDetails\|GetGameActionDetails" && [ $n -lt 72 ]; do sleep 5; n=$((n+1)); done
  [ $n -lt 72 ] && break
  tries=$((tries+1)); echo "UI stalled (black, no log lines) at $(date +%T): restarting Steam"; [ $tries -ge 3 ] && { echo "giving up"; exit 1; }
  systemctl stop wisp-steam; n=0; while [ -n "$(pidof steam)" ] && [ $n -lt 30 ]; do sleep 2; n=$((n+1)); done; sh /storage/wispos/dev/kill-game.sh >/dev/null 2>&1
done
echo "UI ready $(date +%T) n=$n"; sleep 30; /storage/wispos/dev/wisp-screen wake; sleep 2
[ "${PREP_ONLY:-0}" = 1 ] && { echo "fps-run: PREP_ONLY=1, stopping at the home screen (watchdog off, sampler on, panel held on 900 s, then it blanks)"; /storage/wispos/dev/awake.sh 900; exit 0; }
$P press a; sleep 6; grim /storage/wispos/shots/fp0.png
bash /storage/wispos/dev/worms-fps.sh
[ "${HOLD:-0}" = 1 ] && { echo "fps-run: HOLD=1, leaving everything as it is (watchdog off, sampler on, game running)"; exit 0; }
rm -f /run/wisp-steam-watch-off; /storage/wispos/dev/awake.sh 0
systemctl stop wisp-mem-sample
