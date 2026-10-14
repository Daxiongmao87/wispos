"""Dev (never shipped), read-only, runs ON the device: what nice values do the Steam UI's web helpers and the game's processes have RIGHT NOW (every thread), and which window has focus?
Used to verify wisp-ui-priority against a real game (docs/WISPOS-NOTES.md, 2026-10-14). usage: flip-check.py LABEL"""
import collections, json, os, subprocess, sys

label = sys.argv[1] if len(sys.argv) > 1 else ''
procs = {}
for pid in filter(str.isdigit, os.listdir('/proc')):
    try:
        stat = open('/proc/%s/stat' % pid).read()
        procs[int(pid)] = (stat[stat.index('(') + 1:stat.rindex(')')], int(stat[stat.rindex(')') + 2:].split()[1]))
    except (OSError, ValueError, IndexError): pass
ui = [p for p, (c, _) in procs.items() if c == 'steamwebhelper']
reapers = {p for p, (c, _) in procs.items() if c == 'reaper'}
game, frontier = set(), set(reapers)
while frontier:
    kids = {p for p, (_, pp) in procs.items() if pp in frontier and p not in game and p not in reapers}
    game |= kids; frontier = kids


def nices(pids):
    c = collections.Counter()
    for p in pids:
        try:
            for t in os.listdir('/proc/%d/task' % p): c[os.getpriority(os.PRIO_PROCESS, int(t))] += 1
        except OSError: pass
    return dict(sorted(c.items()))


def focus(node):
    if node.get('focused') and not node.get('nodes') and not node.get('floating_nodes'):
        return (node.get('window_properties') or {}).get('class') or node.get('app_id') or '?'
    for ch in (node.get('nodes') or []) + (node.get('floating_nodes') or []):
        f = focus(ch)
        if f: return f
    return ''


try:
    env = {**os.environ, 'XDG_RUNTIME_DIR': '/var/run/0-runtime-dir', 'SWAYSOCK': '/var/run/0-runtime-dir/sway-ipc.0.sock'}
    f = focus(json.loads(subprocess.run(['swaymsg', '-t', 'get_tree', '-r'], capture_output=True, text=True, timeout=10, env=env).stdout)) or '(none)'
except Exception as e: f = 'unreadable (%s)' % e
steam = [p for p, (c, _) in procs.items() if c == 'steam']
print('%-28s focus=%-18s UI helpers %d proc nice-by-thread %s | game %d proc (reaper %s) nice-by-thread %s | steam client nice %s' % (
    label, f, len(ui), nices(ui), len(game), 'yes' if reapers else 'no', nices(game), nices(steam)))
