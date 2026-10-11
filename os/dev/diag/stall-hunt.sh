#!/bin/bash
# Dev: catch the black Steam UI start stall in the act. Runs ON the device, detached:
#   systemd-run --unit=wisp-stall-hunt --collect bash /storage/wispos/dev/stall-hunt.sh [STARTS=8]; journalctl -fu wisp-stall-hunt -o cat
# What it does: restarts wisp-steam STARTS times (service restarts only, never a reboot). Each time it waits for Steam's UI-ready marker in its console log; if the marker has not
# appeared after STALL_AFTER seconds it records the evidence BEFORE the next restart: the web helper's busiest threads and the futex words they wait on (the watchdog's evidence()),
# a program-counter sample of the busiest thread (pc-sample.py), the GPU page-fault count, the last lines of Steam's console and web-helper logs, whether sway has a Steam window,
# and a screenshot. All of it goes to the journal and to /storage/wispos/logs/stall-hunt.log (screenshots: /storage/wispos/logs/stall-N.png).
# Why: about 5 of 13 starts on 2026-10-09/10 came up black (no window in sway, the web helper spinning) and each was restarted before anyone looked
# (docs/WISPOS-NOTES.md). The same day a frozen game turned out to be a Mesa lock held after a fault, so the same evidence may explain the UI.
# After a capture it keeps waiting until GIVE_UP seconds (default 900) and records whether the UI came up (a slow start) or not (a real stall; captured a second time).
# Needs: wisp-steam-watch (the dev copy) and pc-sample.py in /storage/wispos/dev. Disables the watchdog for the run (/run/wisp-steam-watch-off) and restores it at the end.
STARTS=${1:-8}; STALL_AFTER=${STALL_AFTER:-270}; GIVE_UP=${GIVE_UP:-900}
export XDG_RUNTIME_DIR=/var/run/0-runtime-dir SWAYSOCK=/var/run/0-runtime-dir/sway-ipc.0.sock WAYLAND_DISPLAY=wayland-1 DISPLAY=:0
L=/storage/wispos/home/.local/share/Steam/logs; OUT=/storage/wispos/logs/stall-hunt.log; D=/storage/wispos/dev
say() { echo "$*" | tee -a $OUT; }
trap 'rm -f /run/wisp-steam-watch-off; say "=== stall-hunt finished $(date +%T)"' EXIT
touch /run/wisp-steam-watch-off; say "=== stall-hunt $STARTS starts, stall = no UI-ready marker after ${STALL_AFTER}s, $(date +%T)"

capture() {          # $1 = start number
  say "--- STALL on start $1 at $(date +%T), Steam up $(( $(date +%s) - t0 )) s"
  say "GPU page faults this boot: $(dmesg | grep -a -c 'Unhandled Page fault'); uptime $(cut -d. -f1 /proc/uptime) s"
  python3 - <<'PY' 2>&1 | tee -a $OUT
import importlib.machinery, importlib.util, os, re, struct
spec = importlib.util.spec_from_loader('w', importlib.machinery.SourceFileLoader('w', '/storage/wispos/dev/wisp-steam-watch')); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
pids = m.run('pidof', 'steamwebhelper').stdout.split()
print('web helper processes:', len(pids))
for line in m.thread_table('/proc', pids, limit=8): print('  thread:', line[:200])
# futex words: for threads blocked in futex(), read the word at the address and report who else waits on the same address
seen = {}
for pid in pids:
    for tid in sorted(os.listdir('/proc/%s/task' % pid)):
        try: sc = open('/proc/%s/task/%s/syscall' % (pid, tid)).read().split()
        except OSError: continue
        if len(sc) > 3 and sc[0] == '98':
            addr = int(sc[1], 16)
            try:
                with open('/proc/%s/mem' % pid, 'rb') as f: f.seek(addr); word = struct.unpack('<I', f.read(4))[0]
            except (OSError, ValueError): word = None
            seen.setdefault((pid, addr, word), []).append(tid)
for (pid, addr, word), tids in sorted(seen.items(), key=lambda kv: -len(kv[1]))[:6]:
    print('  futex wait: pid %s addr %#x word %s, %d thread(s) (e.g. %s)' % (pid, addr, word, len(tids), tids[0]))
PY
  top=$(python3 - <<'PY'
import importlib.machinery, importlib.util
spec = importlib.util.spec_from_loader('w', importlib.machinery.SourceFileLoader('w', '/storage/wispos/dev/wisp-steam-watch')); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
rows = m.thread_table('/proc', m.run('pidof', 'steamwebhelper').stdout.split(), limit=1)
print(rows[0].split()[0] if rows else '')
PY
)
  if [ -n "$top" ]; then pid=${top%/*}; tid=${top#*/}; say "PC sample of the busiest thread $top:"; python3 $D/pc-sample.py $pid $tid 30 0.1 2>&1 | head -14 | tee -a $OUT; fi
  say "sway windows of class steam: $(swaymsg -t get_tree 2>/dev/null | python3 -c "
import json,sys
try: t=json.load(sys.stdin)
except Exception: print('unknown (swaymsg failed)'); sys.exit()
n=[0]
def w(x):
    if (x.get('window_properties') or {}).get('class')=='steam': n[0]+=1
    for c in x.get('nodes',[])+x.get('floating_nodes',[]): w(c)
w(t); print(n[0])")"
  grim /storage/wispos/logs/stall-$1.png 2>/dev/null && say "screenshot: /storage/wispos/logs/stall-$1.png (hash $(md5sum < /storage/wispos/logs/stall-$1.png | cut -c1-8))"
  say "last Steam console lines:"; tail -n 6 $L/console_log.txt | cut -c1-160 | tee -a $OUT
  say "last web helper log lines:"; tail -n 8 $L/steamwebhelper.log 2>/dev/null | cut -c1-160 | tee -a $OUT
  say "--- end of evidence"
}

for i in $(seq 1 $STARTS); do
  systemctl stop wisp-steam; n=0; while [ -n "$(pidof steam)" ] && [ $n -lt 30 ]; do sleep 2; n=$((n+1)); done
  sh $D/kill-game.sh >/dev/null 2>&1
  c0=$(wc -l < $L/console_log.txt); systemctl start --no-block wisp-steam; t0=$(date +%s)
  ready=0
  while [ $(( $(date +%s) - t0 )) -lt $STALL_AFTER ]; do
    if tail -n +$((c0+1)) $L/console_log.txt 2>/dev/null | grep -a -q "Apps.RegisterForAppDetails\|GetGameActionDetails"; then ready=1; break; fi
    sleep 5
  done
  if [ $ready = 1 ]; then say "start $i: UI ready after $(( $(date +%s) - t0 )) s"; sleep 20; else
    capture $i
    # slow or stuck? keep waiting (the watchdog is off) and record whether and when the UI comes up (the first capture on 2026-10-10 showed Steam's splash spinner still turning at 270 s)
    while [ $(( $(date +%s) - t0 )) -lt $GIVE_UP ]; do
      if tail -n +$((c0+1)) $L/console_log.txt 2>/dev/null | grep -a -q "Apps.RegisterForAppDetails\|GetGameActionDetails"; then ready=2; break; fi
      sleep 5
    done
    if [ $ready = 2 ]; then say "start $i: the UI DID come up, after $(( $(date +%s) - t0 )) s: a slow start, not a stall"; sleep 20
    else say "start $i: still no UI after ${GIVE_UP} s: a real stall"; capture "$i-late"; fi
  fi
done
