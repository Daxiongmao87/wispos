"""wisp-game-display (os/rootfs/usr/bin/wisp-game-display): the panel is at full scale while a game has focus and at the image's default otherwise.

The program runs for real against a FAKE swaymsg (a script that prints scripted window events and answers `get_tree` from a file the test rewrites) and a fake wisp-display that logs
its argument. What is proved: a game window gets `native` once; Steam's own windows (class steam / steamwebhelper) and no window at all get `default`; only CHANGES of the wanted state
call the display tool; a game already in front at start is seen; the tree is read after every event (a stale or reordered event cannot leave the wrong scale); garbage lines, a broken
tree and an event stream that ends are handled (non-zero exit so systemd restarts it); the unit is tied to the Steam session. A running `proton waitforexitandrun` launcher counts as a
game being started (full scale although Steam's UI still has focus, so the launcher's early switch is not undone), its disappearance without a window event (Steam's Stop game, a kill) is
noticed and answered with one fresh look, and while no launcher runs nothing is polled. Mutants (each rule changed in turn) must all be caught."""
import json, os, shutil, stat, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROGRAM = Path(os.environ.get('WISP_GAME_DISPLAY_PATH', HERE / 'rootfs/usr/bin/wisp-game-display'))
UNIT = HERE / 'rootfs/usr/lib/systemd/system/wisp-game-display.service'
WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))


def node(cls=None, focused=False, kind='con', app_id=None):
    n = {'type': kind, 'focused': focused, 'nodes': [], 'floating_nodes': []}
    if cls: n['window_properties'] = {'class': cls}
    if app_id: n['app_id'] = app_id
    return n


def tree(*windows):
    ws = {'type': 'workspace', 'focused': False, 'nodes': list(windows), 'floating_nodes': []}
    out = {'type': 'output', 'focused': False, 'nodes': [ws], 'floating_nodes': []}
    return {'type': 'root', 'focused': False, 'nodes': [out], 'floating_nodes': []}


class Rig(unittest.TestCase):
    def setUp(self):
        (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
        self.d = Path(tempfile.mkdtemp(prefix='wispos-gamedisp-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, self.d, True)
        self.tree, self.events, self.calls = self.d / 'tree.json', self.d / 'events', self.d / 'calls'
        self.tree.write_text(json.dumps(tree())); self.events.write_text('')
        self.sway = self.script('swaymsg', '#!/bin/bash\nif [ "$1" = -m ]; then cat "%s"; exit 0; fi\nif [ "$1 $2" = "-t get_tree" ]; then [ -f "%s.broken" ] && { echo "oops"; exit 1; }; cat "%s"; exit 0; fi\nexit 2\n' % (self.events, self.tree, self.tree))
        self.display = self.script('wisp-display', '#!/bin/bash\necho "$1" >> "%s"\n' % self.calls)

    def script(self, name, body):
        p = self.d / name; p.write_text(body); p.chmod(0o755); return p

    def proc(self, *cmdlines):
        """A fake /proc with one process per command line; returns its path."""
        root = self.d / 'proc'; shutil.rmtree(root, True); root.mkdir()
        for i, args in enumerate(cmdlines):
            (root / str(100 + i)).mkdir(); (root / str(100 + i) / 'cmdline').write_bytes(b'\0'.join(a.encode() for a in args) + b'\0')
        return root

    def run_program(self, events, tree_after=None, timeout=20, extra=None):
        """Run until sway's (fake) event stream ends. `events` are printed in order; the tree file holds `tree_after` for the whole run unless a callable swaps it."""
        self.events.write_text(''.join((e if isinstance(e, str) else json.dumps(e)) + '\n' for e in events))
        if tree_after is not None: self.tree.write_text(json.dumps(tree_after))
        r = subprocess.run([sys.executable, '-I', str(PROGRAM)], capture_output=True, text=True, timeout=timeout, env={**os.environ, 'WISP_SWAYMSG': str(self.sway), 'WISP_DISPLAY_TOOL': str(self.display), 'WISP_PROC_ROOT': str(self.d / 'no-proc'), **(extra or {})})
        return r, (self.calls.read_text().split() if self.calls.exists() else [])

    focus = {'change': 'focus'}


class Behaviour(Rig):
    def test_a_game_in_front_gets_the_full_scale_once(self):
        r, calls = self.run_program([self.focus, {'change': 'focus'}, {'change': 'new'}], tree(node('steam_app_2200', focused=True)))
        self.assertEqual(calls, ['native'], r.stderr)

    def test_steams_own_windows_and_no_window_get_the_default(self):
        for t in (tree(node('steam', focused=True)), tree(node('steamwebhelper', focused=True)), tree(), tree(node('steam_app_2200', focused=False))):
            self.calls.unlink(missing_ok=True)
            r, calls = self.run_program([self.focus], t); self.assertEqual(calls, ['default'], (t, r.stderr))

    def test_only_a_change_of_the_wanted_state_calls_the_display_tool(self):
        # the tree is read after every event; here it is swapped between events by a script that edits the file as the fake swaymsg prints them
        game, ui, nothing = tree(node('steam_app_2200', focused=True)), tree(node('steam', focused=True)), tree()
        seq = [ui, game, game, ui, ui, game, nothing, nothing]
        self.script('swaymsg', '#!/bin/bash\nif [ "$1" = -m ]; then for i in 1 2 3 4 5 6 7; do echo \'{"change":"focus"}\'; sleep 0.3; done; exit 0; fi\n'
                    'n=$(cat "%s.n" 2>/dev/null || echo 0); echo $((n+1)) > "%s.n"; cat "%s/t$n.json" 2>/dev/null || cat "%s/t7.json"\n' % (self.d, self.d, self.d, self.d))
        for i, t in enumerate(seq): (self.d / ('t%d.json' % i)).write_text(json.dumps(t))
        r = subprocess.run([sys.executable, '-I', str(PROGRAM)], capture_output=True, text=True, timeout=30, env={**os.environ, 'WISP_SWAYMSG': str(self.d / 'swaymsg'), 'WISP_DISPLAY_TOOL': str(self.display), 'WISP_PROC_ROOT': str(self.d / 'no-proc')})
        self.assertEqual(self.calls.read_text().split(), ['default', 'native', 'default', 'native', 'default'], r.stderr)

    def test_a_game_already_in_front_when_it_starts_is_seen_without_any_event(self):
        r, calls = self.run_program([], tree(node('steam_app_2200', focused=True))); self.assertEqual(calls, ['native'])

    def test_a_wayland_game_is_found_by_its_app_id(self):
        r, calls = self.run_program([self.focus], tree(node(None, focused=True, app_id='some.game'))); self.assertEqual(calls, ['native'])

    def test_a_floating_game_window_counts_too(self):
        win = node('steam_app_2200', focused=True, kind='floating_con'); t = tree(); t['nodes'][0]['nodes'][0]['floating_nodes'] = [win]
        r, calls = self.run_program([self.focus], t); self.assertEqual(calls, ['native'])

    def test_garbage_event_lines_are_ignored_and_irrelevant_changes_do_not_read_the_tree(self):
        r, calls = self.run_program(['not json', '[1,2]', {'change': 'title'}, {'change': 'urgent'}, self.focus], tree(node('steam_app_2200', focused=True)))
        self.assertEqual(calls, ['native'], r.stderr)

    def test_a_tree_that_cannot_be_read_means_the_default(self):
        (self.d / 'tree.json.broken').write_text('x')
        r, calls = self.run_program([self.focus]); self.assertEqual(calls, ['default'])

    def test_when_sways_event_stream_ends_it_exits_non_zero_so_systemd_restarts_it(self):
        r, _ = self.run_program([self.focus], tree(node('steam', focused=True))); self.assertEqual(r.returncode, 1); self.assertIn('event stream ended', r.stderr)

    LAUNCHER = ['/storage/steam/reaper', 'SteamLaunch', 'AppId=2200', '--', '/storage/tool/proton', 'waitforexitandrun', '/x/quake3.exe']

    def test_a_game_being_started_gets_the_full_scale_while_steams_ui_still_has_focus(self):
        # the patched launcher switched the panel before the game exists; this program must not undo that on the Steam window's next focus event
        r, calls = self.run_program([self.focus], tree(node('steam', focused=True)), extra={'WISP_PROC_ROOT': str(self.proc(self.LAUNCHER))})
        self.assertEqual(calls, ['native'], r.stderr)

    def test_only_the_game_start_verb_of_the_launcher_counts(self):
        for args in (['/x/tool/proton', 'run', '/x/iscriptevaluator.exe'], ['/x/tool/proton', 'runinprefix', 'winecfg'], ['/bin/sh', '-c', 'waitforexitandrun'],
                     ['python3', 'waitforexitandrun'], ['/x/tool/proton']):
            self.calls.unlink(missing_ok=True)
            r, calls = self.run_program([self.focus], tree(node('steam', focused=True)), extra={'WISP_PROC_ROOT': str(self.proc(args))})
            self.assertEqual(calls, ['default'], (args, r.stderr))

    def _swaymsg_that_outlives_the_launcher(self, launcher_ends_after):
        proc = self.proc(self.LAUNCHER)
        self.script('swaymsg', '#!/bin/bash\nif [ "$1" = -m ]; then echo \'{"change":"focus"}\'; sleep %s; rm -rf "%s/100"; sleep 2; exit 0; fi\n'
                    'echo tree >> "%s/swaymsg.reads"; cat "%s"\n' % (launcher_ends_after, proc, self.d, self.tree))
        self.tree.write_text(json.dumps(tree(node('steam', focused=True))))
        r = subprocess.run([sys.executable, '-I', str(PROGRAM)], capture_output=True, text=True, timeout=30,
                           env={**os.environ, 'WISP_SWAYMSG': str(self.d / 'swaymsg'), 'WISP_DISPLAY_TOOL': str(self.display), 'WISP_PROC_ROOT': str(proc), 'WISP_GAME_DISPLAY_POLL': '0.1'})
        reads = len((self.d / 'swaymsg.reads').read_text().split()) if (self.d / 'swaymsg.reads').exists() else 0
        return r, self.calls.read_text().split(), reads

    def test_a_launcher_that_disappears_without_a_window_event_gets_the_default_back(self):
        # Steam's "Stop game" or a kill: the launcher never runs its own restore and the game window may close before it ends
        r, calls, _ = self._swaymsg_that_outlives_the_launcher(1)
        self.assertEqual(calls, ['native', 'default'], r.stderr)

    def test_nothing_is_polled_while_no_launcher_runs_and_the_tree_is_not_reread_while_one_does(self):
        # sway keeps about eight times the size of every reply it gives (docs/WISPOS-NOTES.md): the tree is read at start and after each event, never on a timer
        proc_none = self.d / 'no-proc'
        self.script('swaymsg', '#!/bin/bash\nif [ "$1" = -m ]; then echo \'{"change":"focus"}\'; sleep 2; exit 0; fi\necho tree >> "%s/swaymsg.reads"; cat "%s"\n' % (self.d, self.tree))
        self.tree.write_text(json.dumps(tree(node('steam', focused=True))))
        for proc in (proc_none, self.proc(self.LAUNCHER)):
            (self.d / 'swaymsg.reads').unlink(missing_ok=True)
            subprocess.run([sys.executable, '-I', str(PROGRAM)], capture_output=True, text=True, timeout=30,
                           env={**os.environ, 'WISP_SWAYMSG': str(self.d / 'swaymsg'), 'WISP_DISPLAY_TOOL': str(self.display), 'WISP_PROC_ROOT': str(proc), 'WISP_GAME_DISPLAY_POLL': '0.1'})
            self.assertEqual(len((self.d / 'swaymsg.reads').read_text().split()), 2, proc)     # once at start, once for the one event: 2 seconds at a 0.1 s poll add none

    def test_the_unit_is_tied_to_the_steam_session_and_restarted(self):
        u = UNIT.read_text(); self.assertIn('PartOf=wisp-steam.service', u); self.assertIn('ExecStart=/usr/bin/wisp-game-display', u); self.assertIn('Restart=always', u)
        steam = (HERE / 'rootfs/usr/lib/systemd/system/wisp-steam.service').read_text(); self.assertIn('wisp-game-display.service', steam)
        self.assertTrue(os.access(PROGRAM, os.X_OK))


MUTANTS = [
    ('every window is a game', "return 'native' if cls and cls not in STEAM_UI else 'default'", "return 'native' if cls else 'default'"),
    ('Steam web helper windows count as games', "STEAM_UI = ('steam', 'steamwebhelper')", "STEAM_UI = ('steam',)"),
    ('no window means a game', "return 'native' if cls and cls not in STEAM_UI else 'default'", "return 'native' if cls not in STEAM_UI else 'default'"),
    ('the display tool is called on every event', "        if want != state:\n", "        if True:\n"),
    ('the tree is not read at start', "    settle()\n    fd, buf = p.stdout.fileno(), b''\n", "    fd, buf = p.stdout.fileno(), b''\n"),
    ('app ids are ignored', "or node.get('app_id') or ''", "or ''"),
    ('floating windows are ignored', "(node.get('nodes') or []) + (node.get('floating_nodes') or [])", "(node.get('nodes') or [])"),
    ('focus changes are ignored', "('focus', 'new', 'close', 'move')", "('new', 'close', 'move')"),
    ('the end of the stream is a success', "    return 1\n", "    return 0\n"),
    ('a running launcher is ignored', "    if launching: return 'native'\n", ""),
    ('any command line with the verb counts', "if b'waitforexitandrun' in args and any(a.endswith(b'/proton') for a in args): return True", "if b'waitforexitandrun' in args: return True"),
    ('any proton command line counts', "if b'waitforexitandrun' in args and any(a.endswith(b'/proton') for a in args): return True", "if any(a.endswith(b'/proton') for a in args): return True"),
    ('the launcher ending is not noticed', "            if not launcher_running(): settle()\n", "            pass\n"),
    ('the tree is reread on every poll', "            if not launcher_running(): settle()\n", "            settle()\n"),
    ('polling all the time', "POLL if watch else None", "POLL"),
    ('a launcher seen at the last look is forgotten', "        watch = launcher_running()\n", "        watch = False\n"),
    ('a broken tree is a game', "return json.loads(r.stdout) if r.returncode == 0 else None", "return json.loads(r.stdout) if r.returncode == 0 else {'type': 'con', 'focused': True, 'window_properties': {'class': 'x'}}"),
]


class Mutants(unittest.TestCase):
    def test_every_mutant_is_caught(self):
        if 'WISP_GAME_DISPLAY_PATH' in os.environ: self.skipTest('running a mutant already')
        import concurrent.futures
        (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix='wispos-gdmut-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, tmp, True)
        text = PROGRAM.read_text(); jobs = []
        for i, (what, old, new) in enumerate(MUTANTS):
            self.assertEqual(text.count(old), 1, 'mutant %r: the pattern must occur exactly once' % what)
            m = tmp / ('m%d' % i); m.write_text(text.replace(old, new)); m.chmod(0o755); jobs.append((what, m))
        def go(job):
            what, m = job
            r = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), 'Behaviour'], env={**os.environ, 'WISP_GAME_DISPLAY_PATH': str(m)}, capture_output=True, text=True, timeout=900)
            return what, r.returncode
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex: results = list(ex.map(go, jobs))
        self.assertEqual([w for w, rc in results if rc == 0], [], 'mutants that no test noticed')


if __name__ == '__main__':
    unittest.main()
