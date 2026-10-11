#!/usr/bin/python3
"""Dev: memory sampler for game runs. Runs ON the device (copy to /storage/wispos/dev/). usage: mem-sample.py [OUT_FILE=/storage/wispos/logs/mem-sample.log] [PERIOD_S=3]
Why: the 2026-10-09 Steam-launched Worms match ran out of memory (zram hit its mem_limit, 170 "Write-error on swap-device" lines, the guard stopped Steam) while the earlier
hand-launched run on the same device kept 230-290 MB available. Who holds the memory at that moment decides the fix (docs/WISPOS-NOTES.md, "memory floor").
Every PERIOD_S seconds one block: time, MemAvailable, MemFree, page cache, Shmem, Slab, PageTables, SwapFree, memory pressure (PSI some/full avg10), zram mm_stat (orig / compressed / used /
used-max, MB; mem_limit is not readable by this user on the device) and the top 10 process NAMES by anonymous memory (resident RssAnon / swapped-out VmSwap, MB, summed over
processes of that name; count in brackets, e.g. steamwebhelper[8]=120/410). Not a benchmark: it reads /proc, which costs a few ms per process, once per period.
Never ask sway for anything from here: its IPC leaks (docs/WISPOS-NOTES.md, "MEMORY FLOOR").
"""
import os, sys, time

OUT = sys.argv[1] if len(sys.argv) > 1 else '/storage/wispos/logs/mem-sample.log'
PERIOD = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0


def meminfo():
    d = {}
    for l in open('/proc/meminfo'):
        k, v = l.split(':', 1); d[k] = int(v.split()[0])
    return d


def status(pid):
    d = {}
    try:
        for l in open('/proc/%s/status' % pid):
            if l.startswith(('Name:', 'RssAnon:', 'VmSwap:')):
                k, v = l.split(':', 1); d[k] = v.strip()
    except OSError:
        return None
    return d


def mb(kb): return kb // 1024


def psi():
    try:
        d = {}
        for l in open('/proc/pressure/memory'):
            kind, *fields = l.split(); d[kind] = dict(f.split('=') for f in fields)
        return '%s/%s' % (d['some']['avg10'], d['full']['avg10'])
    except (OSError, KeyError, ValueError):
        return 'n/a'


def sample():
    m = meminfo()
    try: mm = [int(x) for x in open('/sys/block/zram0/mm_stat').read().split()]
    except (OSError, ValueError): mm = [0] * 5
    groups = {}
    for pid in filter(str.isdigit, os.listdir('/proc')):
        s = status(pid)
        if not s or 'RssAnon' not in s: continue
        g = groups.setdefault(s['Name'], [0, 0, 0])
        g[0] += int(s['RssAnon'].split()[0]); g[1] += int(s.get('VmSwap', '0 kB').split()[0]); g[2] += 1
    top = sorted(groups.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))[:10]
    head = '%s avail=%dMB free=%dMB cache=%dMB shmem=%dMB slab=%dMB pt=%dMB swapfree=%dMB psi=%s zram orig=%d compr=%d used=%d max=%dMB' % (
        time.strftime('%H:%M:%S'), mb(m['MemAvailable']), mb(m['MemFree']), mb(m['Cached']), mb(m['Shmem']), mb(m['Slab']), mb(m.get('PageTables', 0)), mb(m['SwapFree']),
        psi(), mm[0] >> 20, mm[1] >> 20, mm[2] >> 20, mm[4] >> 20)
    return head + '\n  ' + '  '.join('%s[%d]=%d/%d' % (n, c, mb(a), mb(w)) for n, (a, w, c) in top) + '\n'


if __name__ == '__main__':
    with open(OUT, 'a', buffering=1) as f:
        f.write('--- sampler start %s period %.1fs\n' % (time.strftime('%H:%M:%S'), PERIOD))
        while True:
            f.write(sample()); time.sleep(PERIOD)
