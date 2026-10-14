#!/usr/bin/python3
"""Dev prototype (never shipped), runs ON the device: a "soft freeze" that follows focus (operator idea 2026-10-13: "we flip these values during ui interaction like qam and menu button").
It subscribes to sway's window events (the same signal wisp-ui-reclaim thaws on) and keeps two groups of threads at priorities that depend on who is in front:
  game in front (a focused window whose class is not Steam's)  UI = UI_BACK (19)   game = GAME_FRONT (default 0)
  Steam in front (or nothing focused)                          UI = UI_FRONT (0)   game = GAME_BACK (19)
A game that is still LAUNCHING (a `reaper` exists but no window other than Steam's has taken focus yet) counts as "game in front": the launch is the most memory-hungry moment, and a UI at
normal priority with the big ceiling thrashed the machine during it (2026-10-13, docs/WISPOS-NOTES.md).
The UI is every thread of every `steamwebhelper` process; the game is every thread of every process below a `reaper` (Steam starts one per game). nice is per THREAD on Linux and a child inherits
its parent's value, so threads are re-applied every PERIOD seconds (new threads, new helpers) and at once on a focus change. With --ceiling it also flips the UI cgroup's memory.high:
UI in front CEIL_FRONT (450 MiB), game in front CEIL_BACK (250 MiB). Direct syscalls, no forks except one `swaymsg` per focus event. It restores nothing when it stops: run it with the values you want.
usage: ui-prio.py [--game-front N=0] [--game-back N=19] [--ui-back N=19] [--ui-front N=0] [--ceiling] [--period S=2]"""
import json, os, subprocess, sys, time

args = sys.argv[1:]
def opt(name, default):
    return type(default)(args[args.index(name) + 1]) if name in args else default
GAME_FRONT, GAME_BACK = opt('--game-front', 0), opt('--game-back', 19)
UI_BACK, UI_FRONT = opt('--ui-back', 19), opt('--ui-front', 0)
PERIOD, CEILING = opt('--period', 2.0), '--ceiling' in args
CEIL_FRONT, CEIL_BACK = 471859200, 262144000
CG = '/sys/fs/cgroup/unified/wisp-ui/memory.high'
STEAM_UI = ('steam', 'steamwebhelper')
GAME_SEEN = [False]                                            # a window that is not Steam's has had focus since the current game (a reaper) appeared


def read(path):
    with open(path, errors='replace') as f: return f.read()


def focused_class(node):
    if not isinstance(node, dict): return None
    if node.get('focused') and node.get('type') in ('con', 'floating_con') and not node.get('nodes') and not node.get('floating_nodes'):
        return (node.get('window_properties') or {}).get('class') or node.get('app_id') or ''
    for c in (node.get('nodes') or []) + (node.get('floating_nodes') or []):
        f = focused_class(c)
        if f: return f
    return ''


def game_in_front():
    try: tree = json.loads(subprocess.run(['swaymsg', '-t', 'get_tree', '-r'], capture_output=True, text=True, timeout=10).stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired): return False
    cls = focused_class(tree)
    return bool(cls) and cls not in STEAM_UI


def processes():
    """{pid: (comm, ppid)}"""
    out = {}
    for pid in os.listdir('/proc'):
        if not pid.isdigit(): continue
        try:
            stat = read('/proc/%s/stat' % pid)
            out[int(pid)] = (stat[stat.index('(') + 1:stat.rindex(')')], int(stat[stat.rindex(')') + 2:].split()[1]))
        except (OSError, ValueError, IndexError): continue
    return out


def apply(pids, nice):
    n = 0
    for pid in pids:
        try:
            for tid in os.listdir('/proc/%d/task' % pid):
                try:
                    if os.getpriority(os.PRIO_PROCESS, int(tid)) != nice: os.setpriority(os.PRIO_PROCESS, int(tid), nice); n += 1
                except OSError: pass
        except OSError: continue
    return n


def sweep(front_is_game):
    procs = processes()
    ui = [p for p, (c, _) in procs.items() if c == 'steamwebhelper']
    reapers = {p for p, (c, _) in procs.items() if c == 'reaper'}
    if not reapers: GAME_SEEN[0] = False
    front_is_game = front_is_game or (bool(reapers) and not GAME_SEEN[0])      # launching counts as game in front
    game, frontier = set(), set(reapers)
    while frontier:                                            # everything below a reaper
        kids = {p for p, (_, pp) in procs.items() if pp in frontier and p not in game and p not in reapers}
        game |= kids; frontier = kids
    n = apply(ui, UI_BACK if front_is_game else UI_FRONT) + apply(game, GAME_FRONT if front_is_game else GAME_BACK)
    if CEILING and os.path.exists(CG):
        try:
            want = str(CEIL_BACK if front_is_game else CEIL_FRONT)
            if read(CG).strip() != want: open(CG, 'w').write(want + '\n'); n += 1
        except OSError: pass
    return n, len(ui), len(game)


def main():
    p = subprocess.Popen(['swaymsg', '-m', '-t', 'subscribe', '-r', '["window"]'], stdout=subprocess.PIPE, text=True)
    state = game_in_front(); last = 0.0
    print('ui-prio: start, %s in front' % ('game' if state else 'Steam'), flush=True)
    import select
    while True:
        r, _, _ = select.select([p.stdout], [], [], PERIOD)
        if r:
            line = p.stdout.readline()
            if not line: print('ui-prio: sway event stream ended', flush=True); return 1
            try: ev = json.loads(line)
            except ValueError: continue
            if isinstance(ev, dict) and ev.get('change') in ('focus', 'new', 'close', 'move'):
                now = game_in_front()
                if now: GAME_SEEN[0] = True
                if now != state: state = now; print('ui-prio: %s in front' % ('game' if state else 'Steam'), flush=True); last = 0.0
        if time.time() - last >= PERIOD:
            last = time.time(); n, u, g = sweep(state)
            if n: print('ui-prio: %d change(s) (%d UI process(es), %d game process(es))' % (n, u, g), flush=True)


if __name__ == '__main__':
    sys.exit(main())
