# Dev: does half resolution (sway output scale 2 = 360x240 scaled up) reduce GPU/graphics load? A/B/A on the idle Steam UI, no input.
# Per phase (scale 1, 2, 1): set the scale, let the UI re-layout (12 s), then 30 s of measurement:
#   - the GPU clock (devfreq cur_freq sampled every 100 ms; the ondemand governor raises it when the GPU is busy): share of samples at each clock and the mean,
#   - CPU ticks of the graphics path: Steam's GPU process (the steamwebhelper holding /dev/dri/renderD128), the other steamwebhelpers, Xwayland, sway, steam,
#   - whole-system CPU busy share.
# `--nav`: a virtual pad (wisp-dev-pad) presses down/up once a second during the measurement, so the UI animates. The scale always returns to 1 (finally). Not a frame-rate test: no game runs.
import collections, glob, os, subprocess, sys, threading, time
ENV = dict(os.environ, XDG_RUNTIME_DIR='/var/run/0-runtime-dir', SWAYSOCK='/var/run/0-runtime-dir/sway-ipc.0.sock', WAYLAND_DISPLAY='wayland-1')
FREQ = '/sys/class/devfreq/1800000.gpu/cur_freq'

def sh(*a): return subprocess.run(a, env=ENV, capture_output=True, text=True).stdout.strip()
def ticks(pid):
    try: f = open('/proc/%d/stat' % pid).read().rsplit(')', 1)[1].split(); return int(f[11]) + int(f[12])
    except Exception: return 0
def pids(name): return [int(p) for p in sh('pidof', name).split()]
def gpu_process():
    for pid in pids('steamwebhelper'):
        try:
            if any(os.readlink(f).endswith('renderD128') for f in glob.glob('/proc/%d/fd/*' % pid)): return pid
        except OSError: pass
def cpu_total():
    f = [int(x) for x in open('/proc/stat').readline().split()[1:8]]; return sum(f), f[3]
PAD = '/storage/wispos/dev/wisp-dev-pad'
def start_pad():
    for p in glob.glob('/proc/[0-9]*/cmdline'):
        try:
            if 'wisp-dev-pad' in open(p).read() and 'start' in open(p).read(): return
        except OSError: pass
    subprocess.Popen([PAD, 'start', '--clone', 'H700 Gamepad'], env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True); time.sleep(4)
def navigate(stop):
    for btn in __import__('itertools').cycle(('down', 'up')):
        if stop.is_set(): return
        subprocess.run([PAD, 'press', btn], env=ENV, capture_output=True); stop.wait(1.0)
def measure(seconds=30, nav=False):
    stop = threading.Event()
    if nav: threading.Thread(target=navigate, args=(stop,), daemon=True).start()
    gp = gpu_process(); helpers = [p for p in pids('steamwebhelper') if p != gp]
    groups = {'gpu process': [gp] if gp else [], 'other steamwebhelper': helpers, 'Xwayland': pids('Xwayland'), 'sway': pids('sway'), 'steam': pids('steam')}
    t0 = {k: sum(ticks(p) for p in v) for k, v in groups.items()}; tot0, idle0 = cpu_total(); freqs = collections.Counter(); start = time.time(); n = 0
    while time.time() - start < seconds:
        freqs[int(open(FREQ).read()) // 1000000] += 1; n += 1; time.sleep(0.1)
    stop.set(); el = time.time() - start; tot1, idle1 = cpu_total()
    cpu = {k: (sum(ticks(p) for p in v) - t0[k]) / el for k, v in groups.items()}      # ticks/s = % of one core
    mean = sum(f * c for f, c in freqs.items()) / n
    return dict(freqs=freqs, n=n, mean=mean, cpu=cpu, busy=100 * (1 - (idle1 - idle0) / (tot1 - tot0)))

def main():
    for _ in range(36):                                   # wait for the UI to settle (load average below 2)
        if float(open('/proc/loadavg').read().split()[0]) < 2.0: break
        time.sleep(5)
    nav = '--nav' in sys.argv
    if nav: start_pad()
    print('%s start (%s), load %s' % (time.strftime('%H:%M:%S'), 'navigating with down/up presses' if nav else 'idle', open('/proc/loadavg').read().split()[0]), flush=True)
    try:
        for scale in ('1', '2', '1'):
            sh('swaymsg', 'output', 'DSI-1', 'scale', scale); time.sleep(12)
            sh('grim', '-l', '1', '/storage/wispos/shots/gpuab-scale%s-%s.png' % (scale, time.strftime('%H%M%S')))
            r = measure(nav=nav)
            share = ', '.join('%d MHz %d%%' % (f, 100 * c / r['n']) for f, c in sorted(r['freqs'].items()))
            print('%s scale %s: GPU clock mean %.0f MHz (%s) | CPU %% of a core: %s | system busy %.0f%%' % (
                time.strftime('%H:%M:%S'), scale, r['mean'], share, ', '.join('%s %.0f' % kv for kv in r['cpu'].items()), r['busy']), flush=True)
    finally:
        sh('swaymsg', 'output', 'DSI-1', 'scale', '1'); print('%s scale restored to 1' % time.strftime('%H:%M:%S'), flush=True)
    print('GPUABDONE', flush=True)

if __name__ == '__main__': main()
