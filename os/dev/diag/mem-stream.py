#!/usr/bin/python3
"""Dev: memory and SD-write sampler that WRITES NOTHING on the device. It prints one JSON line per period to stdout; os/dev/log-stream.sh runs it over ssh (`python3 -u - PERIOD < mem-stream.py`,
so the script itself is not copied to the card either) and keeps the lines on the host. When the ssh connection ends the next write fails and the sampler exits; if the host just vanishes (no FIN),
sshd may keep the session for a while, so LIFETIME_S makes the sampler end by itself too: it cannot be left running.
Why (operator, 2026-10-11): the card should be written as little as possible, in dev too; the old mem-sample.py appended to /storage/wispos/logs. Stdlib only.
usage: mem-stream.py [PERIOD_S=60 [LIFETIME_S=no limit]]
One line: t (epoch), up (s), avail / shmem / swap_used / zram_orig / zram_used (MB), psi_some / psi_full (avg10, %), sd_wr_kib / sd2_wr_kib (KiB written to the boot card mmcblk0 and the TF2 card
mmcblk1 since the previous line), wr (the processes that wrote most since the previous line: [name, pid, KiB]), steam_mb (anon + swapped, all `steam*` processes), procs (Steam processes, sway
and Xwayland one by one: pid, ppid, name, kind, age_s, anon_mb, swap_mb) and others (the five biggest other names: [name, count, anon_mb, swap_mb]).
`kind` tells the Chromium roles apart where the command line does: browser (no --type), renderer/gpu-process/zygote/utility(sub-type). With --no-sandbox the renderers are forked from the zygote
and keep its command line, so they read as zygote children: use ppid to tell them from the zygote itself.
Never asks sway for anything: its IPC leaks (docs/WISPOS-NOTES.md, "MEMORY FLOOR").
"""
import json, os, sys, time

WANTED = ('sway', 'Xwayland')                       # listed one by one besides the steam* family
MIN_WRITER_KIB = 64                                 # a process must have written this much since the last line to be named
TOP_OTHERS, TOP_WRITERS = 5, 3


def read(path):
    with open(path, errors='replace') as f: return f.read()


def meminfo(proc):
    d = {}
    for line in read(proc + '/meminfo').splitlines():
        k, _, v = line.partition(':'); d[k] = int(v.split()[0])
    return d


def psi(proc):
    """(some avg10, full avg10) of /proc/pressure/memory, (None, None) when the kernel has no PSI."""
    out = {}
    try:
        for line in read(proc + '/pressure/memory').splitlines():
            kind, *fields = line.split(); out[kind] = float(dict(f.split('=') for f in fields)['avg10'])
    except (OSError, KeyError, ValueError): pass
    return out.get('some'), out.get('full')


def sectors_written(proc, dev):
    """Sectors written to block device `dev` since boot (/proc/diskstats column 10), 0 when it is not there (no TF2 card)."""
    try:
        for line in read(proc + '/diskstats').splitlines():
            p = line.split()
            if len(p) > 9 and p[2] == dev: return int(p[9])
    except (OSError, ValueError): pass
    return 0


def kind_of(name, args):
    """Role of a Chromium process from its argv; '' for anything that is not a steamwebhelper."""
    if name != 'steamwebhelper': return ''
    typ = next((a.split('=', 1)[1] for a in args if a.startswith('--type=')), '')
    if not typ: return 'browser'
    if typ == 'utility':
        sub = next((a.split('=', 1)[1] for a in args if a.startswith('--utility-sub-type=')), '')
        return 'utility:' + sub.rsplit('.', 1)[-1] if sub else 'utility'
    return typ


def start_ticks(stat_text):
    """Process start time in clock ticks since boot: field 22 of /proc/PID/stat, counted after the last ')' because the command name may contain spaces and parentheses."""
    return int(stat_text.rpartition(')')[2].split()[19])


def process(proc, pid, uptime_s, hz):
    """One process as a dict, None when it is gone or unreadable."""
    base = '%s/%s' % (proc, pid)
    try:
        status = {}
        for line in read(base + '/status').splitlines():
            k, _, v = line.partition(':')
            if k in ('Name', 'PPid', 'RssAnon', 'VmSwap'): status[k] = v.strip()
        if 'RssAnon' not in status: return None                     # kernel thread
        args = [a for a in read(base + '/cmdline').split('\0') if a]
        age = max(0, int(uptime_s - start_ticks(read(base + '/stat')) / hz))
    except (OSError, ValueError, IndexError): return None
    try: wr = int(next(l.split()[1] for l in read(base + '/io').splitlines() if l.startswith('write_bytes'))) // 1024
    except (OSError, StopIteration, ValueError): wr = 0
    kb = lambda s: int(s.split()[0]) if s else 0
    return {'pid': int(pid), 'ppid': int(status.get('PPid', 0)), 'name': status['Name'], 'kind': kind_of(status['Name'], args), 'age_s': age,
            'anon': kb(status.get('RssAnon')), 'swap': kb(status.get('VmSwap')), 'wr_kib': wr}


def sample(proc='/proc', sys_root='/sys', prev=None):
    """(record, state): `state` is what the next call needs to compute the since-last-line fields."""
    prev = prev or {'sd': None, 'sd2': None, 'wr': {}}
    m = meminfo(proc)
    try: mm = [int(x) for x in read(sys_root + '/block/zram0/mm_stat').split()]
    except (OSError, ValueError): mm = [0] * 5
    uptime = float(read(proc + '/uptime').split()[0])
    hz = os.sysconf('SC_CLK_TCK')
    procs = [p for p in (process(proc, pid, uptime, hz) for pid in os.listdir(proc) if pid.isdigit()) if p]
    sd, sd2 = sectors_written(proc, 'mmcblk0'), sectors_written(proc, 'mmcblk1')
    writers = []
    for p in procs:
        d = p['wr_kib'] - prev['wr'].get(p['pid'], p['wr_kib'])     # a process first seen now has no delta yet
        if d >= MIN_WRITER_KIB: writers.append([p['name'], p['pid'], d])
    writers.sort(key=lambda w: -w[2])
    named = [p for p in procs if p['name'].startswith('steam') or p['name'] in WANTED]
    groups = {}
    for p in procs:
        if p in named: continue
        g = groups.setdefault(p['name'], [0, 0, 0]); g[0] += 1; g[1] += p['anon']; g[2] += p['swap']
    others = sorted(([n, c, a >> 10, s >> 10] for n, (c, a, s) in groups.items()), key=lambda o: -(o[2] + o[3]))[:TOP_OTHERS]
    some, full = psi(proc)
    mb = lambda kb: kb >> 10
    rec = {'t': int(time.time()), 'up': int(uptime), 'avail': mb(m['MemAvailable']), 'shmem': mb(m.get('Shmem', 0)), 'swap_used': mb(m.get('SwapTotal', 0) - m.get('SwapFree', 0)),
           'zram_orig': mm[0] >> 20, 'zram_used': mm[2] >> 20, 'psi_some': some, 'psi_full': full,
           'sd_wr_kib': 0 if prev['sd'] is None else (sd - prev['sd']) // 2, 'sd2_wr_kib': 0 if prev['sd2'] is None else (sd2 - prev['sd2']) // 2,       # 512-byte sectors -> KiB
           'wr': writers[:TOP_WRITERS], 'steam_mb': mb(sum(p['anon'] + p['swap'] for p in procs if p['name'].startswith('steam'))),
           'procs': [{'pid': p['pid'], 'ppid': p['ppid'], 'name': p['name'], 'kind': p['kind'], 'age_s': p['age_s'], 'anon_mb': mb(p['anon']), 'swap_mb': mb(p['swap'])}
                     for p in sorted(named, key=lambda p: -(p['anon'] + p['swap']))],
           'others': others}
    return rec, {'sd': sd, 'sd2': sd2, 'wr': {p['pid']: p['wr_kib'] for p in procs}}


def main(argv):
    period = float(argv[0]) if argv else 60.0
    end = time.time() + float(argv[1]) if len(argv) > 1 else None
    state = None
    while end is None or time.time() < end:
        rec, state = sample(prev=state)
        sys.stdout.write(json.dumps(rec, separators=(',', ':')) + '\n'); sys.stdout.flush()      # a closed ssh channel raises here and ends the sampler
        time.sleep(period)


if __name__ == '__main__':
    try: main(sys.argv[1:])
    except (BrokenPipeError, KeyboardInterrupt): pass
