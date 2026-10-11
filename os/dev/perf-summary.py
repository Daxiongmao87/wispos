#!/usr/bin/python3
"""Dev: read the files of one os/dev/perf-run.sh run and print what limits the game. usage: perf-summary.py RUN_DIR
Window = the steady play phase (demo_window: the longest stretch of high game CPU), so averages are about the game playing, not about loading or Steam sitting idle around it. Prints: the game's CPU (one core = 100%), per-core busy and clock, the GPU clock distribution (a GPU clock that never leaves the floor
means the GPU is not the limit; the governor raises it only above ~90% load), the busiest threads by name, how much the Steam UI takes meanwhile, memory pressure, and (stat.csv) instructions
per cycle, branch and cache misses per 1000 instructions. Pure functions are tested in os/test_dev.py."""
import collections, json, os, sys

ACTIVE_CPU = 40


def load_stream(path):
    rows = []
    try:
        with open(path, errors='replace') as f:
            for line in f:
                if line.startswith('{'):
                    try: rows.append(json.loads(line))
                    except ValueError: pass
    except OSError: pass
    return rows


def demo_window(rows):
    """(t_start, t_end) epoch seconds of the steady play phase: the longest stretch where the game's CPU (3 s moving average) stays above 60% of its 90th percentile, trimmed by a quarter
    of a second each side. The loading phases before it are busy too but different; averaging them in hid the real picture. None when the game never ran."""
    cpu = [r.get('game', {}).get('cpu', 0) for r in rows]
    if not cpu or max(cpu) < ACTIVE_CPU: return None
    sm = [sum(cpu[max(0, i - 1):i + 2]) / len(cpu[max(0, i - 1):i + 2]) for i in range(len(cpu))]
    p90 = sorted(sm)[int(len(sm) * 0.9)]
    thr = max(ACTIVE_CPU, 0.6 * p90)
    best, start = (0, 0, 0), None
    for i, c in enumerate(sm + [0]):
        if c >= thr and start is None: start = i
        if c < thr and start is not None:
            if i - start > best[0]: best = (i - start, start, i)
            start = None
    n, a, b = best
    if n < 4: return None
    trim = max(1, n // 8)
    return rows[a + trim]['t'], rows[b - 1 - trim]['t']


def window(rows):
    w = demo_window(rows)
    if not w: return [r for r in rows if r.get('game', {}).get('cpu', 0) >= ACTIVE_CPU]
    return [r for r in rows if w[0] <= r['t'] <= w[1]]


def avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else 0.0


def summarize(rows, w):
    out = {'seconds': len(w)}
    if not w: return out
    n = len(w[0]['cpu'])
    out['game_cpu'] = avg(r['game']['cpu'] for r in w); out['game_user'] = avg(r['game']['user'] for r in w); out['game_sys'] = avg(r['game']['sys'] for r in w)
    out['ui_cpu'] = avg(r['ui']['cpu'] for r in w)
    out['core_busy'] = [avg(r['cpu'][i][0] for r in w) for i in range(n)]
    out['core_mhz'] = [avg((r['freq'][i] if i < len(r['freq']) else None) for r in w) for i in range(n)]
    gpu = collections.Counter(r['gpu_mhz'] for r in w if r.get('gpu_mhz') is not None)
    out['gpu_mhz'] = {k: round(100.0 * v / len(w)) for k, v in sorted(gpu.items())}
    out['temp_max'] = max((r['temp_c'] for r in w if r.get('temp_c') is not None), default=None)
    out['psi_mem_full'] = max((r['psi_mem_full'] or 0 for r in w), default=0); out['psi_cpu'] = avg(r.get('psi_cpu') for r in w)
    out['avail_min'] = min(r['avail_mb'] for r in w); out['swout'] = avg(r['swout'] for r in w); out['swin'] = avg(r['swin'] for r in w)
    thr = collections.defaultdict(float)
    for r in w:
        for t in r['thr']: thr['%s (%s)' % (t[0], t[2])] += t[3]
    wait = collections.Counter()
    for r in w:
        for k, v in (r.get('wait') or {}).items(): wait[k] += v
    tot = sum(wait.values())
    out['wait'] = [(k, 100.0 * v / tot) for k, v in wait.most_common(6)] if tot else []
    out['threads'] = sorted(((k, v / len(w)) for k, v in thr.items()), key=lambda kv: -kv[1])[:8]
    return out


def stat_ipc(path, t_from, t_to):
    """Sums of the hardware counters in [t_from, t_to] seconds of perf stat -I -x, output: {event: sum}."""
    sums = collections.Counter()
    try:
        with open(path, errors='replace') as f:
            for line in f:
                p = line.strip().split(',')
                if len(p) < 4: continue
                try: t, v = float(p[0]), float(p[1])
                except ValueError: continue
                if t_from <= t <= t_to: sums[p[3].split(':')[0]] += v
    except OSError: pass
    return sums


def main(run):
    if run == '--window':                               # print "T0 T1" of the demo window of a stream.jsonl (used by perf-run.sh)
        w = demo_window(load_stream(sys.argv[2])); print('%.1f %.1f' % w if w else ''); return 0
    rows = load_stream(os.path.join(run, 'stream.jsonl'))
    if not rows: print('no stream.jsonl lines in', run); return 1
    w = window(rows); s = summarize(rows, w)
    print('--- %s: %d s of trace, steady play window %d s (%s)' % (os.path.basename(run.rstrip('/')), len(rows), s['seconds'], ('%.0f..%.0f s into the trace' % (w[0]['t'] - rows[0]['t'], w[-1]['t'] - rows[0]['t'])) if w else 'none found'))
    if not w: return 0
    print('game CPU %.0f%% of one core (user %.0f, system %.0f); Steam UI %.0f%% meanwhile' % (s['game_cpu'], s['game_user'], s['game_sys'], s['ui_cpu']))
    print('cores busy %%: %s   clocks MHz: %s' % (' '.join('%3.0f' % x for x in s['core_busy']), ' '.join('%4.0f' % x for x in s['core_mhz'])))
    print('GPU clock share of time (MHz: %%): %s   hottest zone %.1f C' % (s['gpu_mhz'], s['temp_max'] or 0))
    print('memory: min available %d MB, swap out/in %.0f/%.0f pages/s, PSI mem full max %.1f%%, PSI cpu some %.1f%%' % (s['avail_min'], s['swout'], s['swin'], s['psi_mem_full'], s['psi_cpu']))
    print('busiest threads (average %% of one core): ' + ', '.join('%s %.0f' % kv for kv in s['threads']))
    if s['wait']: print('busiest game thread, what it does (20 Hz): ' + ', '.join('%s %.0f%%' % kv for kv in s['wait']))
    t0 = rows[0]['t']; a, b = w[0]['t'] - t0, w[-1]['t'] - t0
    c = stat_ipc(os.path.join(run, 'stat.csv'), a, b)
    if c.get('cycles') and c.get('instructions'):
        ki = c['instructions'] / 1000.0
        print('hardware counters over the same window (whole system): IPC %.2f, branch misses %.1f / 1000 instr, L1d load misses %.1f / 1000 instr, cache misses %.1f / 1000 instr' % (
            c['instructions'] / c['cycles'], c.get('branch-misses', 0) / ki, c.get('l1-dcache-load-misses', 0) / ki, c.get('cache-misses', 0) / ki))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]) if len(sys.argv) > 1 else 'usage: perf-summary.py RUN_DIR')
