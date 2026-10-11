# Dev: follow the gamescope stand-in's log and apply each newly chosen refresh rate to the panel (`swaymsg -- output DSI-1 modeline ...`).
# Lets the quick-access menu's Frame Limit slider change the real refresh without restarting Steam (the stand-in itself only logs unless started with --apply).
# NOT a frame limiter: the frame rate of Steam's UI and of games is not capped here (open item in docs/WISPOS-DISPLAY-FINDINGS.md).
import os, re, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gsstub import modeline_args
LOG = sys.argv[1] if len(sys.argv) > 1 else '/storage/wispos/logs/gsstub.log'
os.environ.setdefault('XDG_RUNTIME_DIR', '/var/run/0-runtime-dir'); os.environ.setdefault('SWAYSOCK', '/var/run/0-runtime-dir/sway-ipc.0.sock')
current = 60; f = open(LOG); f.seek(0, 2)
print('%s following %s' % (time.strftime('%H:%M:%S'), LOG), flush=True)
while True:
    line = f.readline()
    if not line: time.sleep(0.2); continue
    m = re.search(r'chosen refresh (\d+) Hz', line)
    if not m or int(m.group(1)) == current: continue
    hz = int(m.group(1))
    cmd = modeline_args(hz)          # also for 60 Hz: `output DSI-1 mode 720x480@60Hz` did not take after a custom modeline (the panel stayed at 45 Hz)
    r = subprocess.run(cmd, capture_output=True, text=True); current = hz
    print('%s applied %d Hz: %s' % (time.strftime('%H:%M:%S'), hz, r.stdout.strip().replace('\n', ' ')[:60]), flush=True)
