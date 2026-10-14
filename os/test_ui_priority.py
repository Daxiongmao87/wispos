"""wisp-ui-priority (os/rootfs/usr/bin/wisp-ui-priority): scheduling priority follows what is in front.

The program runs for real against a FAKE swaymsg (a script that prints scripted window events and answers `get_tree` from numbered files, one more each time it is asked) and a FAKE proc
tree (stat files and task directories); nice values are recorded in a file instead of being set (WISP_PRIO_RECORD), one `tid nice` line per CHANGE. What is proved: with Steam's UI in front
(or alone) the web helpers get -10 and a game's processes 0; with a game in front the helpers get 19 and the game -10; a game that is being started (its `reaper` exists, no game window has had
focus yet) counts as in front even while Steam still has focus; after a game window has had focus, Steam in front gives the UI -10 and the game 0 again, and a start that ends without a window
gives the UI -10 back; every THREAD of a managed process is set; the Steam client itself and unrelated processes are never touched; no sway reading means everything neutral (0), never a boost;
an event stream that ends exits non-zero having put everything back to 0; SIGTERM does the same and exits 0; only changes are written; the unit is tied to the Steam session and listed in the
rootfs check. Mutants (each rule changed in turn) must all be caught."""
import json, os, shutil, signal, subprocess, sys, tempfile, time, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROGRAM = Path(os.environ.get('WISP_UI_PRIORITY_PATH', HERE / 'rootfs/usr/bin/wisp-ui-priority'))
UNIT = HERE / 'rootfs/usr/lib/systemd/system/wisp-ui-priority.service'
WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))


def node(cls=None, focused=False):
    n = {'type': 'con', 'focused': focused, 'nodes': [], 'floating_nodes': []}
    if cls: n['window_properties'] = {'class': cls}
    return n


def tree(*windows):
    ws = {'type': 'workspace', 'focused': False, 'nodes': list(windows), 'floating_nodes': []}
    out = {'type': 'output', 'focused': False, 'nodes': [ws], 'floating_nodes': []}
    return {'type': 'root', 'focused': False, 'nodes': [out], 'floating_nodes': []}


STEAM, GAME, NONE = tree(node('steam', focused=True)), tree(node('steam_app_2200', focused=True)), tree()
# fake processes: pid -> (comm, ppid, thread ids)
UI = {100: ('steamwebhelper', 50, [100, 101, 102]), 101: ('steamwebhelper', 100, [103])}
CLIENT = {50: ('steam', 1, [50, 51])}
GAME_PROCS = {200: ('reaper', 50, [200]), 201: ('wineserver', 200, [201, 202]), 202: ('WA.exe', 201, [203])}
OTHER = {300: ('sshd', 1, [300])}


class Rig(unittest.TestCase):
    def setUp(self):
        (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
        self.d = Path(tempfile.mkdtemp(prefix='wispos-uiprio-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, self.d, True)
        self.record, self.proc_root = self.d / 'record', self.d / 'proc'

    def proc(self, *groups):
        shutil.rmtree(self.proc_root, True); self.proc_root.mkdir()
        for g in groups:
            for pid, (comm, ppid, tids) in g.items():
                (self.proc_root / str(pid) / 'task').mkdir(parents=True)
                (self.proc_root / str(pid) / 'stat').write_text('%d (%s) S %d 0 0 0 0\n' % (pid, comm, ppid))
                for t in tids: (self.proc_root / str(pid) / 'task' / str(t)).mkdir()

    def sway(self, trees, events=None, between=0.5, hooks=None):
        """A fake swaymsg: the event stream prints one focus event per tree (waiting `between` seconds), and every get_tree answers the next tree from the list (the last one repeats)."""
        for i, t in enumerate(trees): (self.d / ('t%d.json' % i)).write_text(json.dumps(t))
        n = len(events if events is not None else trees) - 1
        evs = events if events is not None else [{'change': 'focus'}] * n
        body = ('#!/bin/bash\nif [ "$1" = -m ]; then\n' + ''.join('echo %s\nsleep %s\n%s' % (json.dumps(json.dumps(e)), between, (hooks or {}).get(k, '')) for k, e in enumerate(evs)) + 'exit 0\nfi\n'
                'if [ "$1 $2" = "-t get_tree" ]; then n=$(cat "%s/n" 2>/dev/null || echo 0); echo $((n+1)) > "%s/n"; f="%s/t$n.json"; [ -f "$f" ] || f="%s/t%d.json"; cat "$f"; exit 0; fi\nexit 1\n'
                % (self.d, self.d, self.d, self.d, len(trees) - 1))
        p = self.d / 'swaymsg'; p.write_text(body); p.chmod(0o755); return p

    def run_program(self, sway, timeout=30, **env):
        self.record.write_text('')
        e = {**os.environ, 'WISP_SWAYMSG': str(sway), 'WISP_PROC_ROOT': str(self.proc_root), 'WISP_PRIO_RECORD': str(self.record), 'WISP_PRIO_PERIOD': '0.2', **env}
        r = subprocess.run([sys.executable, '-I', str(PROGRAM)], capture_output=True, text=True, timeout=timeout, env=e)
        return r, [tuple(int(x) for x in l.split()) for l in self.record.read_text().splitlines()]

    @staticmethod
    def nices(lines, tid):
        return [n for t, n in lines if t == tid]


class Behaviour(Rig):
    def test_steam_in_front_gives_the_ui_minus_10_and_leaves_the_game_alone(self):
        self.proc(UI, CLIENT, GAME_PROCS, OTHER)
        # a game exists and was seen in front, then Steam took focus: UI -10, game 0
        r, lines = self.run_program(self.sway([GAME, STEAM, STEAM]))
        for tid in (100, 101, 102, 103): self.assertEqual(self.nices(lines, tid), [19, -10, 0], (tid, r.stderr))     # game in front, Steam in front, then the shutdown's neutral
        for tid in (201, 202, 203): self.assertEqual(self.nices(lines, tid), [-10, 0], (tid, r.stderr))                 # game -10 in front; at Steam in front it is back to 0 (no write for an unchanged 0 only at the end)

    def test_a_game_in_front_gives_the_ui_19_and_the_game_minus_10(self):
        self.proc(UI, GAME_PROCS)
        r, lines = self.run_program(self.sway([GAME, GAME]))
        self.assertEqual(self.nices(lines, 100)[0], 19, r.stderr); self.assertEqual(self.nices(lines, 201)[0], -10, r.stderr)
        self.assertEqual(r.stdout.count('a game is in front or starting'), 1, r.stdout)

    def test_a_game_that_is_starting_counts_as_in_front_while_steam_still_has_focus(self):
        self.proc(UI, GAME_PROCS)
        r, lines = self.run_program(self.sway([STEAM, STEAM]))                  # a reaper, Steam focused, no game window has ever had focus
        self.assertEqual(self.nices(lines, 100)[0], 19, (lines, r.stderr)); self.assertEqual(self.nices(lines, 201)[0], -10, (lines, r.stderr))

    def test_after_a_game_window_has_had_focus_steam_in_front_means_the_ui_is_back_on_top(self):
        self.proc(UI, GAME_PROCS)
        r, lines = self.run_program(self.sway([STEAM, GAME, STEAM, STEAM]))            # starting (Steam focused), the game window focused, Steam focused again
        self.assertEqual(self.nices(lines, 100), [19, -10, 0], (lines, r.stderr))        # 19 while starting and in front, -10 once Steam is back on top, 0 at the shutdown
        self.assertEqual(self.nices(lines, 201), [-10, 0], (lines, r.stderr))            # -10 while starting and in front, 0 once Steam is back on top

    def test_the_next_game_start_counts_again_after_the_last_one_ended(self):
        self.proc(UI, GAME_PROCS)
        readd = ''.join('mkdir -p "{r}/{p}/task/{p}"; echo "{p} ({c}) S {pp} 0 0 0 0" > "{r}/{p}/stat"\n'.format(r=self.proc_root, p=pid, c=c, pp=pp) for pid, (c, pp, _) in GAME_PROCS.items())
        gone = 'rm -rf "%s/200" "%s/201" "%s/202"\n' % (self.proc_root, self.proc_root, self.proc_root)
        r, lines = self.run_program(self.sway([GAME, STEAM, STEAM, STEAM], hooks={0: gone, 1: readd}))
        self.assertEqual(self.nices(lines, 100)[:3], [19, -10, 19], (lines, r.stderr))     # game in front, Steam on top after it, a NEW start: 19 again

    def test_a_start_that_ends_without_a_window_gives_the_ui_minus_10_back(self):
        self.proc(UI, GAME_PROCS)
        hook = 'rm -rf "%s/200" "%s/201" "%s/202"\n' % (self.proc_root, self.proc_root, self.proc_root)
        r, lines = self.run_program(self.sway([STEAM, STEAM, STEAM], hooks={0: hook}))
        self.assertEqual(self.nices(lines, 100)[:2], [19, -10], (lines, r.stderr))

    def test_the_steam_client_and_other_processes_are_never_touched(self):
        self.proc(UI, CLIENT, GAME_PROCS, OTHER)
        r, lines = self.run_program(self.sway([GAME, STEAM, GAME]))
        touched = {t for t, _ in lines}
        self.assertFalse(touched & {50, 51, 300, 200}, touched)                                                           # client, unrelated, the reaper itself
        self.assertTrue({100, 101, 102, 103, 201, 202, 203} <= touched, touched)

    def test_no_sway_reading_means_everything_neutral_never_a_boost(self):
        self.proc(UI, GAME_PROCS)
        broken = self.d / 'swaymsg'; broken.write_text('#!/bin/bash\nif [ "$1" = -m ]; then sleep 1; exit 0; fi\nexit 1\n'); broken.chmod(0o755)
        r, lines = self.run_program(broken)
        self.assertEqual([n for _, n in lines if n != 0], [], (lines, r.stderr))
        self.assertIn('unknown, neutral', r.stdout)

    def test_an_event_stream_that_ends_exits_non_zero_with_everything_back_at_zero(self):
        self.proc(UI, GAME_PROCS)
        r, lines = self.run_program(self.sway([GAME, GAME]))
        self.assertEqual(r.returncode, 1, r.stderr)
        final = {}
        for t, n in lines: final[t] = n
        self.assertEqual({t: n for t, n in final.items() if t in (100, 101, 102, 103, 201, 202, 203)}, {t: 0 for t in (100, 101, 102, 103, 201, 202, 203)}, lines)

    def test_sigterm_puts_everything_back_to_zero_and_exits_zero(self):
        self.proc(UI, GAME_PROCS)
        blocker = self.d / 'swaymsg'
        blocker.write_text('#!/bin/bash\nif [ "$1" = -m ]; then sleep 3; exit 0; fi\nif [ "$1 $2" = "-t get_tree" ]; then cat "%s/t0.json"; exit 0; fi\nexit 1\n' % self.d); blocker.chmod(0o755)
        (self.d / 't0.json').write_text(json.dumps(GAME)); self.record.write_text('')
        p = subprocess.Popen([sys.executable, '-I', str(PROGRAM)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             env={**os.environ, 'WISP_SWAYMSG': str(blocker), 'WISP_PROC_ROOT': str(self.proc_root), 'WISP_PRIO_RECORD': str(self.record), 'WISP_PRIO_PERIOD': '0.2'})
        for _ in range(50):
            if '19' in self.record.read_text().split(): break
            time.sleep(0.1)
        p.send_signal(signal.SIGTERM); out, err = p.communicate(timeout=10)
        self.assertEqual(p.returncode, 0, err)
        lines = [tuple(int(x) for x in l.split()) for l in self.record.read_text().splitlines()]
        self.assertEqual(self.nices(lines, 100), [19, 0], lines); self.assertEqual(self.nices(lines, 201), [-10, 0], lines)

    def test_only_changes_are_written(self):
        self.proc(UI)
        r, lines = self.run_program(self.sway([STEAM] * 6, between=0.3))
        self.assertEqual(self.nices(lines, 100), [-10, 0], (lines, r.stderr))                                             # one set, one reset at the end, however many sweeps ran

    def test_the_values_can_be_set_from_the_environment(self):
        self.proc(UI, GAME_PROCS)
        r, lines = self.run_program(self.sway([GAME, GAME]), WISP_UI_BACK='15', WISP_GAME_FRONT='-5')
        self.assertEqual(self.nices(lines, 100)[0], 15, r.stderr); self.assertEqual(self.nices(lines, 201)[0], -5, r.stderr)


class Wiring(unittest.TestCase):
    def test_the_unit_is_tied_to_the_steam_session_and_listed_in_the_rootfs_check(self):
        u = UNIT.read_text(); self.assertIn('PartOf=wisp-steam.service', u); self.assertIn('ExecStart=/usr/bin/wisp-ui-priority', u); self.assertIn('Restart=always', u)
        steam = (HERE / 'rootfs/usr/lib/systemd/system/wisp-steam.service').read_text(); self.assertIn('wisp-ui-priority.service', steam)
        check = (HERE / 'check_rootfs.py').read_text(); self.assertIn("'usr/bin/wisp-ui-priority'", check); self.assertIn("'usr/lib/systemd/system/wisp-ui-priority.service'", check)
        self.assertTrue(os.access(PROGRAM, os.X_OK))


class Mutants(unittest.TestCase):
    """Each rule of the program is changed in turn in a copy; the Behaviour tests must fail for every change."""
    MUTANTS = [
        ("UI_FRONT, UI_BACK = int(os.environ.get('WISP_UI_FRONT', '-10'))", "UI_FRONT, UI_BACK = int(os.environ.get('WISP_UI_FRONT', '0'))"),
        ("int(os.environ.get('WISP_UI_BACK', '19'))", "int(os.environ.get('WISP_UI_BACK', '10'))"),
        ("GAME_FRONT, GAME_BACK = int(os.environ.get('WISP_GAME_FRONT', '-10'))", "GAME_FRONT, GAME_BACK = int(os.environ.get('WISP_GAME_FRONT', '0'))"),
        ("int(os.environ.get('WISP_GAME_BACK', '0'))", "int(os.environ.get('WISP_GAME_BACK', '5'))"),
        ("return 'game' if (reaper_exists and not self.game_seen) else 'ui'", "return 'ui'"),
        ("if not reaper_exists: self.game_seen = False", "pass"),
        ("if self.focus is None: return None", "if self.focus is None: return 'ui'"),
        ("ui = [p for p, (comm, _) in procs.items() if comm == 'steamwebhelper']", "ui = [p for p, (comm, _) in procs.items() if comm in ('steamwebhelper', 'steam')]"),
        ("if ppid in frontier and", "if ppid in reapers and"),
        ("if tid.isdigit() and set_nice(int(tid), nice)", "if tid.isdigit() and int(tid) == pid and set_nice(int(tid), nice)"),
        ("sway event stream ended', file=sys.stderr, flush=True); neutral(); return 1", "sway event stream ended', file=sys.stderr, flush=True); return 1"),
        ("signal.signal(signal.SIGTERM, lambda *_: (neutral(), sys.exit(0)))", "signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))"),
        ("if _recorded.get(tid, 0) == nice: return False", "if False: return False"),
    ]

    def test_every_mutant_is_caught(self):
        src = PROGRAM.read_text(); survivors = []
        (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
        d = Path(tempfile.mkdtemp(prefix='wispos-uiprio-mut-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, d, True)
        for old, new in self.MUTANTS:
            self.assertEqual(src.count(old), 1, 'mutation anchor not found exactly once: %r' % old)
            m = d / 'mutant'; m.write_text(src.replace(old, new)); m.chmod(0o755)
            r = subprocess.run([sys.executable, '-m', 'unittest', '-q', 'test_ui_priority.Behaviour'], cwd=HERE, capture_output=True, text=True, timeout=600,
                               env={**os.environ, 'WISP_UI_PRIORITY_PATH': str(m), 'WISP_SKIP_MUTANTS': '1'})
            if r.returncode == 0: survivors.append(old)
        self.assertEqual(survivors, [], 'mutants that no test noticed')


if __name__ == '__main__':
    unittest.main()
