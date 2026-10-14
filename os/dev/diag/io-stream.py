#!/usr/bin/python3
"""Dev (never shipped), WRITES NOTHING on the device (run as `python3 -u - PERIOD LIFETIME < io-stream.py` over ssh): is the Steam UI waiting on the SD card while it is navigated?
(operator 2026-10-14: "wondering if it might be pulling assets from disk when I navigate").
One JSON line per period: the card (mmcblk0): reads/s, read MB/s, mean read latency in ms (time spent reading / reads completed), busy % (io_ticks), writes/s and write MB/s;
the kernel's view of file-cache misses: file refaults/s (pages read back that were evicted: the cost of too little memory), page faults needing disk (major) /s;
io pressure (some, full avg10); and the three processes that read the most bytes from storage in the period (name, MB). Stdlib only."""
import json, os, sys, time

PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0
LIFETIME = float(sys.argv[2]) if len(sys.argv) > 2 else float('inf')
DEV = 'mmcblk0'


def diskstats():
    for line in open('/proc/diskstats'):
        f = line.split()
        if f[2] == DEV:
            return dict(rd=int(f[3]), rd_sec=int(f[5]), rd_ms=int(f[6]), wr=int(f[7]), wr_sec=int(f[9]), busy_ms=int(f[12]))
    raise SystemExit('no %s in /proc/diskstats' % DEV)


def vm():
    out = {}
    for line in open('/proc/vmstat'):
        k, v = line.split()
        if k in ('workingset_refault_file', 'pgmajfault', 'pgpgin'): out[k] = int(v)
    return out


def procreads():
    out = {}
    for pid in os.listdir('/proc'):
        if not pid.isdigit(): continue
        try:
            rb = 0
            for line in open('/proc/%s/io' % pid):
                if line.startswith('read_bytes:'): rb = int(line.split()[1])
            name = open('/proc/%s/comm' % pid).read().strip()
            out[pid] = (name, rb)
        except OSError: continue
    return out


def psi():
    some = full = None
    for line in open('/proc/pressure/io'):
        f = dict(x.split('=') for x in line.split()[1:])
        if line.startswith('some'): some = float(f['avg10'])
        else: full = float(f['avg10'])
    return some, full


t0 = time.time()
d0, v0, p0, t_prev = diskstats(), vm(), procreads(), time.time()
while time.time() - t0 < LIFETIME:
    time.sleep(PERIOD)
    d1, v1, p1, now = diskstats(), vm(), procreads(), time.time()
    dt = now - t_prev
    rd = d1['rd'] - d0['rd']
    top = sorted(((p1[p][1] - p0.get(p, (None, p1[p][1]))[1], p1[p][0]) for p in p1), reverse=True)[:3]
    some, full = psi()
    print(json.dumps({
        't': int(now), 'reads_s': round(rd / dt), 'read_MBs': round((d1['rd_sec'] - d0['rd_sec']) * 512 / 1048576 / dt, 2),
        'read_ms': round((d1['rd_ms'] - d0['rd_ms']) / rd, 1) if rd else 0, 'busy_pct': round(100.0 * (d1['busy_ms'] - d0['busy_ms']) / (dt * 1000), 1),
        'writes_s': round((d1['wr'] - d0['wr']) / dt), 'write_MBs': round((d1['wr_sec'] - d0['wr_sec']) * 512 / 1048576 / dt, 2),
        'file_refault_s': round((v1['workingset_refault_file'] - v0['workingset_refault_file']) / dt), 'majfault_s': round((v1['pgmajfault'] - v0['pgmajfault']) / dt),
        'io_psi': [some, full], 'top_readers': [[n, round(b / 1048576, 2)] for b, n in top if b > 0]}), flush=True)
    d0, v0, p0, t_prev = d1, v1, p1, now
