"""wisp-home (os/rootfs/usr/bin/wisp-home): a TAP of the pad's Steam button (BTN_MODE) takes the player from a running game to Steam's UI and back.

The program runs for real: its pad is a FIFO that receives genuine `struct input_event` records, its sway is a fake `swaymsg` that answers `get_tree` from a file and logs every other command.
What is proved: a tap with a game in front hides every game window in sway's scratchpad and focuses Steam's window; a tap with Steam in front and a game hidden shows the game again and makes it
fullscreen; a tap with no game does nothing (Steam's own handling of the button is left alone); a game is NEVER hidden when Steam has no window to show; a chord (another button pressed while
Steam is held), a press without release, autorepeat and other buttons do nothing; two taps inside the debounce time act once; a failing sway does not stop the daemon; Wayland app ids count as games;
the ioctl number for the pad's capability bits is the Linux one; when a game is hidden Steam is asked to open that game's library page (only when the window class names the game, only
on the way TO Steam); the unit is tied to the Steam session. Mutants (each rule changed in turn) must all be caught."""
import importlib.machinery, importlib.util, json, os, shutil, signal, struct, subprocess, sys, tempfile, time, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROGRAM = Path(os.environ.get('WISP_HOME_PATH', HERE / 'rootfs/usr/bin/wisp-home'))
UNIT = HERE / 'rootfs/usr/lib/systemd/system/wisp-home.service'
WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))
EVENT = struct.Struct('llHHi')
EV_SYN, EV_KEY, BTN_SOUTH, BTN_WEST, BTN_MODE = 0, 1, 304, 308, 316


def ev(code, value, etype=EV_KEY): return EVENT.pack(0, 0, etype, code, value)


def win(wid, cls=None, focused=False, kind='con', app_id=None):
    n = {'id': wid, 'type': kind, 'focused': focused, 'nodes': [], 'floating_nodes': []}
    if cls: n['window_properties'] = {'class': cls}
    if app_id: n['app_id'] = app_id
    return n


def tree(visible=(), hidden=()):
    ws = {'type': 'workspace', 'name': '1', 'focused': False, 'nodes': list(visible), 'floating_nodes': []}
    scratch = {'type': 'workspace', 'name': '__i3_scratch', 'focused': False, 'nodes': [], 'floating_nodes': list(hidden)}
    out = {'type': 'output', 'name': 'DSI-1', 'focused': False, 'nodes': [ws], 'floating_nodes': []}
    return {'type': 'root', 'focused': False, 'nodes': [out, {'type': 'output', 'name': '__i3', 'nodes': [scratch], 'floating_nodes': []}], 'floating_nodes': []}


STEAM_FOCUSED = win(10, 'steam', True)
STEAM = win(10, 'steam')
GAME_FOCUSED = win(11, 'steam_app_217200', True)
GAME = win(11, 'steam_app_217200')


def load():
    spec = importlib.util.spec_from_loader('wisp_home_mod', importlib.machinery.SourceFileLoader('wisp_home_mod', str(PROGRAM)))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


class Rig(unittest.TestCase):
    def setUp(self):
        (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
        self.d = Path(tempfile.mkdtemp(prefix='wispos-home-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, self.d, True)
        self.tree, self.calls, self.fifo = self.d / 'tree.json', self.d / 'calls', self.d / 'pad'
        os.mkfifo(self.fifo)
        self.set_tree(tree())
        self.sway = self.d / 'swaymsg'
        self.sway.write_text('#!/bin/bash\nif [ "$1 $2" = "-t get_tree" ]; then [ -f "%s.broken" ] && { echo oops; exit 1; }; cat "%s"; exit 0; fi\necho "$*" >> "%s"\n' % (self.tree, self.tree, self.calls))
        self.sway.chmod(0o755)
        self.opened = self.d / 'opened'
        self.opener = self.d / 'wisp-steam-open'
        self.opener.write_text('#!/bin/bash\necho "$1" >> "%s"\n' % self.opened); self.opener.chmod(0o755)

    def opens(self): return self.opened.read_text().split() if self.opened.exists() else []

    def set_tree(self, t): self.tree.write_text(json.dumps(t))

    def run_daemon(self, records, wait=1.0, expect=None, debounce='0', extra=None):
        """Start the daemon on the FIFO, feed it `records` (bytes of input_events), wait until `expect` commands were logged (or `wait` seconds), stop it; return the commands and the process."""
        env = {**os.environ, 'WISP_SWAYMSG': str(self.sway), 'WISP_HOME_DEVICES': str(self.fifo), 'WISP_HOME_RESCAN': '0.2', 'WISP_HOME_DEBOUNCE': debounce,
               'WISP_STEAM_OPEN': str(self.opener), **(extra or {})}
        p = subprocess.Popen([sys.executable, '-I', str(PROGRAM)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            fd = None; t0 = time.time()
            while fd is None and time.time() - t0 < 5:
                try: fd = os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK)
                except OSError: time.sleep(0.05)
            self.assertIsNotNone(fd, 'the daemon never opened the pad')
            time.sleep(0.4)                                                    # let it register the descriptor
            for r in records: os.write(fd, r); time.sleep(0.05)
            end = time.time() + wait
            while time.time() < end:
                time.sleep(0.05)
                if expect is not None and self.logged() and len(self.logged()) >= expect: time.sleep(0.2); break
            alive = p.poll() is None
            os.close(fd)
        finally:
            if p.poll() is None: p.send_signal(signal.SIGTERM);
            try: p.wait(timeout=5)
            except subprocess.TimeoutExpired: p.kill()
            self.last_err = p.stderr.read(); p.stdout.close(); p.stderr.close()
        return self.logged(), alive

    def logged(self): return self.calls.read_text().splitlines() if self.calls.exists() else []


TAP = [ev(BTN_MODE, 1), ev(BTN_MODE, 0)]


class Behaviour(Rig):
    def test_a_tap_with_a_game_in_front_hides_the_game_and_shows_steam(self):
        self.set_tree(tree([STEAM, GAME_FOCUSED]))
        calls, _ = self.run_daemon(TAP, expect=2)
        self.assertEqual(calls, ['[con_id=11] move scratchpad', '[con_id=10] focus'])

    def test_steam_is_asked_for_the_games_page_only_on_the_way_to_steam_and_only_when_the_class_names_the_game(self):
        self.set_tree(tree([STEAM, GAME_FOCUSED]))
        self.run_daemon(TAP, expect=2); time.sleep(0.3)
        self.assertEqual(self.opens(), ['steam://nav/games/details/217200'])
        for name, t in (('a game of unknown id', tree([STEAM, win(11, 'hl_linux', True)])), ('a Wayland game', tree([STEAM, win(11, None, True, app_id='some.game')])),
                        ('the way back', tree([STEAM_FOCUSED], hidden=[GAME])), ('nothing to do', tree([STEAM_FOCUSED])), ('no Steam window', tree([GAME_FOCUSED]))):
            self.opened.unlink(missing_ok=True); self.calls.unlink(missing_ok=True); self.set_tree(t)
            self.run_daemon(TAP, wait=0.8); time.sleep(0.3)
            self.assertEqual(self.opens(), [], name)
        self.opened.unlink(missing_ok=True); self.calls.unlink(missing_ok=True); self.set_tree(tree([STEAM, win(11, 'steam_app_12abc', True)]))   # a class that only looks like one
        self.run_daemon(TAP, wait=0.8); time.sleep(0.3); self.assertEqual(self.opens(), [])

    def test_a_missing_opener_does_not_stop_the_tap_from_working(self):
        self.set_tree(tree([STEAM, GAME_FOCUSED]))
        calls, alive = self.run_daemon(TAP, expect=2, extra={'WISP_STEAM_OPEN': str(self.d / 'nowhere')})
        self.assertEqual(calls, ['[con_id=11] move scratchpad', '[con_id=10] focus']); self.assertTrue(alive)

    def test_a_tap_with_steam_in_front_and_a_game_hidden_shows_the_game_fullscreen(self):
        self.set_tree(tree([STEAM_FOCUSED], hidden=[GAME]))
        calls, _ = self.run_daemon(TAP, expect=2)
        self.assertEqual(calls, ['[con_id=11] scratchpad show', '[con_id=11] fullscreen enable'])

    def test_a_tap_with_no_game_does_nothing(self):
        self.set_tree(tree([STEAM_FOCUSED]))
        calls, alive = self.run_daemon(TAP, wait=1.0); self.assertEqual(calls, []); self.assertTrue(alive)

    def test_a_game_is_never_hidden_when_steam_has_no_window_to_show(self):
        self.set_tree(tree([GAME_FOCUSED]))
        calls, alive = self.run_daemon(TAP, wait=1.0); self.assertEqual(calls, []); self.assertTrue(alive)
        self.assertEqual(self.last_err, '', 'declining must be a decision, not a swallowed error')

    def test_a_chord_a_lone_press_other_buttons_and_autorepeat_are_not_taps(self):
        self.set_tree(tree([STEAM, GAME_FOCUSED]))
        for name, recs in (('chord', [ev(BTN_MODE, 1), ev(BTN_WEST, 1), ev(BTN_WEST, 0), ev(BTN_MODE, 0)]),
                           ('press only', [ev(BTN_MODE, 1)]),
                           ('other buttons', [ev(BTN_SOUTH, 1), ev(BTN_SOUTH, 0), ev(BTN_WEST, 1), ev(BTN_WEST, 0)]),
                           ('release without press', [ev(BTN_MODE, 0)]),
                           ('syn only', [ev(0, 0, EV_SYN)])):
            self.calls.unlink(missing_ok=True)
            calls, _ = self.run_daemon(recs, wait=0.8); self.assertEqual(calls, [], name)
        self.calls.unlink(missing_ok=True)                                      # autorepeat (value 2) of the held button does not make a second tap, nor stop the first
        calls, _ = self.run_daemon([ev(BTN_MODE, 1), ev(BTN_MODE, 2), ev(BTN_MODE, 2), ev(BTN_MODE, 0)], expect=2)
        self.assertEqual(calls, ['[con_id=11] move scratchpad', '[con_id=10] focus'])

    def test_two_taps_inside_the_debounce_time_act_once_and_two_far_apart_act_twice(self):
        self.set_tree(tree([STEAM, GAME_FOCUSED]))
        calls, _ = self.run_daemon(TAP + TAP, wait=1.2, debounce='5'); self.assertEqual(len(calls), 2, calls)      # one tap = two commands
        self.calls.unlink(missing_ok=True)
        calls, _ = self.run_daemon(TAP + TAP, wait=1.2, debounce='0'); self.assertEqual(len(calls), 4, calls)

    def test_every_game_window_is_hidden_and_a_wayland_game_counts(self):
        self.set_tree(tree([STEAM, win(11, 'steam_app_1', True), win(12, None, False, app_id='some.game.app'), win(13, 'steamwebhelper')]))
        calls, _ = self.run_daemon(TAP, expect=3)
        self.assertEqual(calls, ['[con_id=11] move scratchpad', '[con_id=12] move scratchpad', '[con_id=10] focus'])      # the web helper's window is Steam's, not a game

    def test_a_game_already_hidden_is_not_hidden_again(self):
        self.set_tree(tree([STEAM, GAME_FOCUSED], hidden=[win(12, 'steam_app_9')]))
        calls, _ = self.run_daemon(TAP, expect=2)
        self.assertEqual(calls, ['[con_id=11] move scratchpad', '[con_id=10] focus'])

    def test_a_failing_or_garbled_sway_does_not_stop_the_daemon(self):
        (self.d / 'tree.json.broken').write_text('x')
        calls, alive = self.run_daemon(TAP, wait=0.8); self.assertEqual(calls, []); self.assertTrue(alive)
        (self.d / 'tree.json.broken').unlink(); self.tree.write_text('not json')
        calls, alive = self.run_daemon(TAP, wait=0.8); self.assertEqual(calls, []); self.assertTrue(alive)

    def test_the_capability_ioctl_number_is_the_linux_one(self):
        m = load()
        self.assertEqual(m.eviocgbit(1, 96), 0x80604521)                      # EVIOCGBIT(EV_KEY, 96): the value evtest and libevdev use
        self.assertEqual(m.eviocgbit(0, 4), 0x80044520)

    def test_the_unit_is_tied_to_the_steam_session_and_restarted(self):
        u = UNIT.read_text(); self.assertIn('PartOf=wisp-steam.service', u); self.assertIn('ExecStart=/usr/bin/wisp-home', u); self.assertIn('Restart=always', u)
        steam = (HERE / 'rootfs/usr/lib/systemd/system/wisp-steam.service').read_text(); self.assertIn('wisp-home.service', steam)
        self.assertTrue(os.access(PROGRAM, os.X_OK))
        self.assertIn("'usr/bin/wisp-home'", (HERE / 'check_rootfs.py').read_text())


MUTANTS = [
    ('a game is hidden although Steam has no window', "        if not steam: return []\n", ""),
    ('Steam web helper windows count as games', "STEAM_UI = ('steam', 'steamwebhelper')", "STEAM_UI = ('steam',)"),
    ('nothing is shown again', "    if hidden: return ['[con_id=%s] scratchpad show' % hidden[0][0], '[con_id=%s] fullscreen enable' % hidden[0][0]]\n", ""),
    ('the game is not made fullscreen', ", '[con_id=%s] fullscreen enable' % hidden[0][0]]", "]"),
    ('hidden games are hidden again', "for g in games if g[3] != SCRATCH]", "for g in games]"),
    ('a chord counts as a tap', "elif fd in pressed: pressed[fd] = True", "elif fd in pressed: pass"),
    ('the press acts, not the release', "                if code == BTN_MODE and value == 1: pressed[fd] = False\n", "                if code == BTN_MODE and value == 1: pressed[fd] = False; tap()\n"),
    ('autorepeat counts', "if etype != EV_KEY or value == 2: continue", "if etype != EV_KEY: continue"),
    ('no debounce', "time.time() - last_tap >= DEBOUNCE", "True"),
    ('app ids are ignored', "or node.get('app_id') or ''", "or ''"),
    ('floating windows are ignored', "(node.get('nodes') or []) + (node.get('floating_nodes') or [])", "(node.get('nodes') or [])"),
    ('a broken sway stops the daemon', "    try: tree = json.loads(r.stdout)\n    except ValueError: return\n", "    tree = json.loads(r.stdout)\n"),
    ('the wrong button', "EV_KEY, BTN_SOUTH, BTN_MODE = 1, 304, 316", "EV_KEY, BTN_SOUTH, BTN_MODE = 1, 304, 315"),
    ('Steam is hidden instead of focused', "['[con_id=%s] focus' % steam[0][0]]", "['[con_id=%s] move scratchpad' % steam[0][0]]"),
    ('any class gives a page', "m = re.fullmatch(r'steam_app_([0-9]+)', cls)", "m = re.fullmatch(r'.*?([0-9]*)', cls)"),
    ('a class that only starts like a game gives a page', "re.fullmatch(r'steam_app_([0-9]+)', cls)", "re.match(r'steam_app_([0-9]+)', cls)"),
    ('the page is the wrong one', "'steam://nav/games/details/%s'", "'steam://open/games/details/%s'"),
    ('a missing opener stops the daemon', "        except OSError: pass\n\n\ndef main", "        except OSError: raise\n\n\ndef main"),
    ('the ioctl number is wrong', "(2 << 30) | (length << 16)", "(1 << 30) | (length << 16)"),
]


class Mutants(unittest.TestCase):
    def test_every_mutant_is_caught(self):
        if 'WISP_HOME_PATH' in os.environ: self.skipTest('running a mutant already')
        import concurrent.futures
        (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix='wispos-homemut-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, tmp, True)
        text = PROGRAM.read_text(); jobs = []
        for i, (what, old, new) in enumerate(MUTANTS):
            self.assertEqual(text.count(old), 1, 'mutant %r: the pattern must occur exactly once (%d)' % (what, text.count(old)))
            m = tmp / ('m%d' % i); m.write_text(text.replace(old, new)); m.chmod(0o755); jobs.append((what, m))
        def go(job):
            what, m = job
            r = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), 'Behaviour'], env={**os.environ, 'WISP_HOME_PATH': str(m)}, capture_output=True, text=True, timeout=900)
            return what, r.returncode
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex: results = list(ex.map(go, jobs))
        self.assertEqual([w for w, rc in results if rc == 0], [], 'mutants that no test noticed')


if __name__ == '__main__':
    unittest.main()
