#!/usr/bin/python3
"""Dev: UI-instability sampler that WRITES NOTHING on the device (like mem-stream.py: os/dev/log-stream.sh pipes it over ssh, `python3 -u - PERIOD LIFETIME < ui-soak-stream.py`).
One JSON line per period. Why (2026-10-13, docs/WISPOS-NOTES.md "UI instability investigation"): mem-stream.py has no swap traffic, per-core CPU, CPU/IO pressure or Steam client state, which
are what the starvation asserts ("BMainLoop stalled > 15 seconds") depend on. Fields: t, up (s), load1, cpu (busy % per core since the previous line), psi (memory/cpu/io some+full avg10),
swapin / swapout (pages/s since the previous line), majflt (/s), zram (orig, compr, used in MB), kswapd (cpu it ran on, its CPU % of one core), steam (state letter of the client, '-' none),
helpers (live steamwebhelper processes, zombies excluded), zombies, dumps (number of /tmp/dumps/assert_*.dmp), newest (name of the newest). Never asks sway. Stdlib only."""
import json, os, sys, time

PERIOD = float(sys.argv[1]) if len(sys.argv) > 1 else 30.0
LIFETIME = float(sys.argv[2]) if len(sys.argv) > 2 else float('inf')
HZ = os.sysconf('SC_CLK_TCK')


def read(path):
    with open(path, errors='replace') as f: return f.read()


def cpu_times():
    out = {}
    for line in read('/proc/stat').splitlines():
        if line.startswith('cpu') and line[3:4].isdigit():
            v = [int(x) for x in line.split()[1:]]; out[line.split()[0]] = (sum(v), v[3] + v[4])    # total, idle + iowait
    return out


def vmstat():
    d = {}
    for line in read('/proc/vmstat').splitlines():
        k, _, v = line.partition(' ')
        if k in ('pswpin', 'pswpout', 'pgmajfault'): d[k] = int(v)
    return d


def psi(kind):
    out = {}
    try:
        for line in read('/proc/pressure/' + kind).splitlines():
            k, *f = line.split(); out[k] = float(dict(x.split('=') for x in f)['avg10'])
    except (OSError, KeyError, ValueError): pass
    return [out.get('some'), out.get('full')]


def comm_state(pid):
    stat = read('/proc/%s/stat' % pid)
    return stat[stat.index('(') + 1:stat.rindex(')')], stat[stat.rindex(')') + 2:].split()


def procs(name):
    """(state letters of live processes whose name is `name`)."""
    states = []
    for pid in os.listdir('/proc'):
        if not pid.isdigit(): continue
        try:
            comm, f = comm_state(pid)
            if comm == name: states.append(f[0])
        except (OSError, ValueError, IndexError): continue
    return states


def kswapd():
    for pid in os.listdir('/proc'):
        if pid.isdigit():
            try:
                comm, f = comm_state(pid)
                if comm == 'kswapd0': return int(f[36]), int(f[11]) + int(f[12])      # processor (field 39), utime + stime ticks
            except (OSError, ValueError, IndexError): continue
    return None, 0


def main():
    start = time.time(); prev = None
    while time.time() - start < LIFETIME:
        now = time.time(); cpu = cpu_times(); vm = vmstat(); ks_cpu, ks_ticks = kswapd()
        if prev:
            dt = now - prev['t']
            busy = [round(100.0 * (1 - (cpu[c][1] - prev['cpu'][c][1]) / max(1, cpu[c][0] - prev['cpu'][c][0])), 1) for c in sorted(cpu)]
            try: mm = [int(x) for x in read('/sys/block/zram0/mm_stat').split()[:3]]
            except (OSError, ValueError): mm = [0, 0, 0]                           # no zram on this machine (the host smoke test)
            steam = procs('steam'); helpers = procs('steamwebhelper')
            try: dumps = sorted(n for n in os.listdir('/tmp/dumps') if n.startswith('assert_') and n.endswith('.dmp'))
            except OSError: dumps = []
            print(json.dumps({'t': int(now), 'up': int(float(read('/proc/uptime').split()[0])), 'load1': float(read('/proc/loadavg').split()[0]), 'cpu': busy,
                              'psi': {k: psi(k) for k in ('memory', 'cpu', 'io')},
                              'swapin': round((vm['pswpin'] - prev['vm']['pswpin']) / dt), 'swapout': round((vm['pswpout'] - prev['vm']['pswpout']) / dt),
                              'majflt': round((vm['pgmajfault'] - prev['vm']['pgmajfault']) / dt),
                              'zram': [round(x / 1048576) for x in mm], 'kswapd': [ks_cpu, round(100.0 * (ks_ticks - prev['ks']) / HZ / dt, 1)],
                              'steam': ''.join(steam) or '-', 'helpers': sum(1 for s in helpers if s != 'Z'), 'zombies': sum(1 for s in helpers if s == 'Z'),
                              'dumps': len(dumps), 'newest': dumps[-1] if dumps else ''}), flush=True)
        prev = {'t': now, 'cpu': cpu, 'vm': vm, 'ks': ks_ticks}
        time.sleep(max(0.5, PERIOD - (time.time() - now)))


if __name__ == '__main__':
    try: main()
    except (BrokenPipeError, KeyboardInterrupt): pass
