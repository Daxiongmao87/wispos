#!/usr/bin/python3
"""Dev (never shipped), runs ON the device: keep every thread of every `steamwebhelper` process at a low priority (nice NICE, default 19), re-applied every PERIOD seconds because helpers and their
threads come and go. Why (operator question 2026-10-13, "what about adjusting niceness to deprioritize steamui?"): a running thread's nice value CAN be changed (setpriority on the thread id; on Linux
nice is per THREAD, so `renice -p PID` alone only changes the main thread), and it applies at once. Only the web helpers are touched, never the `steam` client: a child inherits its parent's nice
value, so a niced client would start every game niced too. Direct syscalls, no forks: a shell loop of renice calls would cost more CPU than it saves.
usage: ui-nice.py [NICE=19 [PERIOD=2]]    (stop it with SIGTERM; it restores nothing: restart Steam or run it with 0)"""
import os, sys, time

NICE = int(sys.argv[1]) if len(sys.argv) > 1 else 19
PERIOD = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0

while True:
    n = 0
    for pid in os.listdir('/proc'):
        if not pid.isdigit(): continue
        try:
            with open('/proc/%s/comm' % pid) as f:
                if f.read().strip() != 'steamwebhelper': continue
            for tid in os.listdir('/proc/%s/task' % pid):
                try:
                    if os.getpriority(os.PRIO_PROCESS, int(tid)) != NICE: os.setpriority(os.PRIO_PROCESS, int(tid), NICE); n += 1
                except OSError: pass
        except OSError: continue
    if n: print('ui-nice: %d thread(s) set to %d' % (n, NICE), flush=True)
    time.sleep(PERIOD)
