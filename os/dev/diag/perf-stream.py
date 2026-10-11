#!/usr/bin/python3
"""Dev: where does the time go while a game runs? One JSON line per second on stdout, nothing written on the device (piped in with `python3 -u - PERIOD LIFETIME < perf-stream.py`, see os/dev/perf-run.sh).
usage: perf-stream.py [PERIOD_S=1 [LIFETIME_S=no limit]]
A line: t (epoch), dt (s since the previous line),
  cpu    per core [busy %, user %, system+irq %] of that core's time; freq per core (MHz); gpu_mhz (devfreq current clock: the governor raises it only above ~90% GPU load, so a clock
         pinned at the floor means the GPU is not saturated); temp_c (hottest zone); load1; psi_cpu / psi_mem_some / psi_mem_full (avg10 %); avail_mb; swin / swout (pages per second)
  game   everything below the Steam launcher `reaper ... SteamLaunch` (the game, Wine, FEX threads): cpu (% of ONE core, 100 = one core fully busy), user, sys, procs, threads;
         `sys` high next to `user` means the kernel is busy on the game's behalf (page faults, signals: the SIGILL fallback for LSE instructions shows up here)
  ui     Steam's own processes (steam*, not the game): cpu %
  top    the 6 busiest processes outside the idle: [name, pid, cpu %, user %, sys %]
  thr    the 8 busiest THREADS of the 3 busiest processes: [thread name, tid, process name, cpu %, pid] (Wine/FEX name their threads; a game main thread at 100% is CPU bound)
  target the game thread with the most CPU in this line: [pid, tid, thread name]; it is the one `wait` samples during the NEXT line
  wait   what that thread was doing, sampled 20 times a second: {"R": n} running, or {"S:futex": n, "S:poll": n, "D:ioctl": n ...} sleeping/blocked as state:system call (from the kernel stack,
         else the wait channel). Samples sum to ~20 per line; the running share is the thread's CPU-bound part, the rest says WHAT it waits for (a futex = another thread, ioctl = the GPU
         or a driver, poll/select = the display server or input, D = disk/swap)
Stdlib only; reads /proc and /sys; a few ms per line on this CPU. Never asks sway for anything (its IPC leaks).
"""
import glob, json, os, re, sys, time

HZ = os.sysconf('SC_CLK_TCK')
WAIT_HZ = 20                                        # wait-state samples per second of the busiest game thread
SYSCALL = re.compile(r'__arm64_(?:sys|compat_sys)_(\w+)')


def read(path):
    with open(path, errors='replace') as f: return f.read()


def parse_stat(text):
    """(pid, comm, ppid, utime, stime, threads) of /proc/PID/stat; fields are counted after the last ')' because the command name may contain spaces and parentheses."""
    head, _, tail = text.rpartition(')')
    pid, _, comm = head.partition(' (')
    f = tail.split()                                  # f[0] state, f[1] ppid, f[11] utime, f[12] stime, f[17] num_threads
    return int(pid), comm, int(f[1]), int(f[11]), int(f[12]), int(f[17])


def cpu_lines(text):
    """{cpuN: (user, system, busy, total)} jiffies from /proc/stat."""
    out = {}
    for line in text.splitlines():
        p = line.split()
        if p and p[0].startswith('cpu') and p[0] != 'cpu':
            v = [int(x) for x in p[1:9]]              # user nice system idle iowait irq softirq steal
            out[p[0]] = (v[0] + v[1], v[2] + v[5] + v[6], sum(v) - v[3] - v[4], sum(v))
    return out


def snapshot(proc='/proc', sys_root='/sys'):
    """Raw counters: per-core jiffies, per-process (comm, ppid, utime, stime, threads), per-thread utime+stime of every thread of the processes in `procs`, vmstat swap counters."""
    snap = {'t': time.time(), 'cpu': cpu_lines(read(proc + '/stat')), 'procs': {}}
    for d in glob.glob(proc + '/[0-9]*'):
        try:
            pid, comm, ppid, ut, st, nt = parse_stat(read(d + '/stat'))
        except (OSError, ValueError, IndexError): continue
        snap['procs'][pid] = (comm, ppid, ut, st, nt)
    vm = {}
    try:
        for line in read(proc + '/vmstat').splitlines():
            k, _, v = line.partition(' ')
            if k in ('pswpin', 'pswpout'): vm[k] = int(v)
    except OSError: pass
    snap['vm'] = vm
    return snap


def threads_of(proc, pid):
    out = {}
    for d in glob.glob('%s/%d/task/[0-9]*' % (proc, pid)):
        try: tid, comm, _, ut, st, _ = parse_stat(read(d + '/stat'))
        except (OSError, ValueError, IndexError): continue
        out[tid] = (comm, ut + st)
    return out


def children_map(procs):
    kids = {}
    for pid, (_, ppid, *_rest) in procs.items(): kids.setdefault(ppid, []).append(pid)
    return kids


def descendants(roots, kids):
    seen, stack = set(), list(roots)
    while stack:
        p = stack.pop()
        for c in kids.get(p, ()):
            if c not in seen: seen.add(c); stack.append(c)
    return seen


def launcher_pids(proc, procs):
    """pids of `reaper ... SteamLaunch` processes."""
    out = []
    for pid, (comm, *_r) in procs.items():
        if comm != 'reaper': continue
        try: argv = read('%s/%d/cmdline' % (proc, pid)).split('\0')
        except OSError: continue
        if 'SteamLaunch' in argv: out.append(pid)
    return out


def wait_key(proc, pid, tid):
    """What one thread is doing right now: 'R' (running), or '<state>:<system call>' for a sleeping one, the call taken from the kernel stack, else the wait channel, else '?'."""
    base = '%s/%d/task/%d' % (proc, pid, tid)
    try: state = read(base + '/stat').rpartition(')')[2].split()[0]
    except (OSError, IndexError): return None
    if state == 'R': return 'R'
    what = ''
    try:
        m = SYSCALL.search(read(base + '/stack'))
        if m: what = m.group(1)
    except OSError: pass
    if not what:
        try: what = read(base + '/wchan').strip()
        except OSError: pass
    return '%s:%s' % (state, what if what and what != '0' else '?')


def psi(proc, name):
    out = {}
    try:
        for line in read('%s/pressure/%s' % (proc, name)).splitlines():
            kind, *fields = line.split(); out[kind] = float(dict(f.split('=') for f in fields)['avg10'])
    except (OSError, KeyError, ValueError): pass
    return out.get('some'), out.get('full')


def mhz(path, per_mhz=1000):
    """A clock file as MHz: cpufreq counts kHz (per_mhz 1000), devfreq counts Hz (per_mhz 1000000)."""
    try: return int(read(path).strip()) // per_mhz
    except (OSError, ValueError): return None


def line(prev, cur, proc='/proc', sys_root='/sys'):
    dt = max(cur['t'] - prev['t'], 1e-3)
    tick = dt * HZ / 100.0                              # jiffies per 1 % of one core over dt
    cores = sorted(cur['cpu'], key=lambda n: int(n[3:]))
    cpu = []
    for n in cores:
        a, b = prev['cpu'].get(n, cur['cpu'][n]), cur['cpu'][n]
        total = max(b[3] - a[3], 1)
        cpu.append([round(100.0 * (b[2] - a[2]) / total), round(100.0 * (b[0] - a[0]) / total), round(100.0 * (b[1] - a[1]) / total)])
    freq = [mhz('%s/devices/system/cpu/%s/cpufreq/scaling_cur_freq' % (sys_root, n)) for n in cores]
    temps = []
    for z in glob.glob(sys_root + '/class/thermal/thermal_zone*/temp'):
        try: temps.append(int(read(z).strip()) / 1000.0)
        except (OSError, ValueError): pass
    gpu_files = glob.glob(sys_root + '/class/devfreq/*gpu*/cur_freq')
    gpu = mhz(gpu_files[0], 1000000) if gpu_files else None
    procs = cur['procs']
    deltas = {}
    for pid, (comm, ppid, ut, st, nt) in procs.items():
        if pid in prev['procs']:
            _, _, ut0, st0, _ = prev['procs'][pid]
            du, ds = ut - ut0, st - st0
            if du or ds: deltas[pid] = (comm, du / tick, ds / tick)
    kids = children_map(procs)
    game = descendants(launcher_pids(proc, procs), kids)
    ui = {pid for pid, (comm, *_r) in procs.items() if comm.startswith('steam') and pid not in game}
    g = [deltas[p] for p in game if p in deltas]
    top = sorted(deltas.items(), key=lambda kv: -(kv[1][1] + kv[1][2]))[:6]
    busiest = [pid for pid, _ in top[:3]]
    thr = []
    for pid in busiest:
        now, before = threads_of(proc, pid), prev.get('threads', {}).get(pid, {})
        for tid, (tcomm, j) in now.items():
            if tid in before: thr.append([tcomm, tid, procs[pid][0], round((j - before[tid][1]) / tick), pid])
    thr.sort(key=lambda t: -t[3])
    some, full = psi(proc, 'memory'); cpu_some, _ = psi(proc, 'cpu')
    mem = {}
    try:
        for l in read(proc + '/meminfo').splitlines():
            k, _, v = l.partition(':'); mem[k] = int(v.split()[0])
    except OSError: pass
    vm0, vm1 = prev['vm'], cur['vm']
    rec = {'t': round(cur['t'], 1), 'dt': round(dt, 2), 'cpu': cpu, 'freq': freq, 'gpu_mhz': gpu, 'temp_c': round(max(temps), 1) if temps else None,
           'load1': float(read(proc + '/loadavg').split()[0]) if os.path.exists(proc + '/loadavg') else None, 'psi_cpu': cpu_some, 'psi_mem_some': some, 'psi_mem_full': full,
           'avail_mb': mem.get('MemAvailable', 0) >> 10, 'swin': round((vm1.get('pswpin', 0) - vm0.get('pswpin', 0)) / dt), 'swout': round((vm1.get('pswpout', 0) - vm0.get('pswpout', 0)) / dt),
           'game': {'cpu': round(sum(x[1] + x[2] for x in g)), 'user': round(sum(x[1] for x in g)), 'sys': round(sum(x[2] for x in g)), 'procs': len(game), 'threads': sum(procs[p][4] for p in game)},
           'ui': {'cpu': round(sum(deltas[p][1] + deltas[p][2] for p in ui if p in deltas))},
           'top': [[d[0], pid, round(d[1] + d[2]), round(d[1]), round(d[2])] for pid, d in top], 'thr': thr[:8]}
    cur['threads'] = {pid: threads_of(proc, pid) for pid in busiest}
    in_game = [t for t in thr if t[4] in game and t[3] > 0]
    rec['target'] = [in_game[0][4], in_game[0][1], in_game[0][0]] if in_game else None
    return rec


def main(argv):
    period = float(argv[0]) if argv else 1.0
    end = time.time() + float(argv[1]) if len(argv) > 1 else None
    prev = snapshot(); prev['threads'] = {}
    target, tick = None, 1.0 / WAIT_HZ
    while end is None or time.time() < end:
        waits, until = {}, time.time() + period
        while time.time() < until:                         # the busiest game thread of the previous line, sampled between two lines
            if target:
                k = wait_key('/proc', target[0], target[1])
                if k: waits[k] = waits.get(k, 0) + 1
            time.sleep(min(tick, max(0.0, until - time.time())))
        cur = snapshot()
        rec = line(prev, cur)
        rec['wait'] = dict(sorted(waits.items(), key=lambda kv: -kv[1])[:6])
        target = rec['target']
        sys.stdout.write(json.dumps(rec, separators=(',', ':')) + '\n'); sys.stdout.flush()
        prev = cur


if __name__ == '__main__':
    try: main(sys.argv[1:])
    except (BrokenPipeError, KeyboardInterrupt): pass
