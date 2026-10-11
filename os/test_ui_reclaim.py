"""Tests for os/reclaim/wisp-ui-reclaim.c (the Steam UI's memory ceiling, docs/WISPOS-NOTES.md "Steam UI memory: decisions").

The program is compiled for the host and run against FAKE /proc and cgroup directories (files in a temp tree on the HDD), so no real cgroup, process or memory is touched. What is
proved: only steamwebhelper processes are put in the UI cgroup (a game never is); memory.high is the idle ceiling without a game and the small one while a `reaper` exists, and goes
back afterwards; it writes only when the value changes; new helpers (a recycled UI) are picked up; an unusable cgroup root is a loud exit, not a silent no-op.
The real kernel behaviour (memory.high actually pushing pages to zram) is measured on the device, not here.
"""
import os, shutil, signal, subprocess, tempfile, time, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / 'reclaim/wisp-ui-reclaim.c'
WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))
MIB = 1 << 20
BINARY = []


def setUpModule():
    if not str(WORK).startswith('/mnt/hdd/'): raise unittest.SkipTest('the work directory must be on the HDD')
    tmp = WORK / 'tmp'; tmp.mkdir(parents=True, exist_ok=True)
    out = tmp / 'test-wisp-ui-reclaim'
    r = subprocess.run(['gcc', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror', '-o', str(out), str(SRC)], capture_output=True, text=True)
    if r.returncode != 0: raise RuntimeError('wisp-ui-reclaim.c does not compile:\n' + r.stderr)
    BINARY.append(out)


class Base(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix='wispos-reclaim-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, self.d, True)
        self.proc, self.root = self.d / 'proc', self.d / 'cgroup'
        self.proc.mkdir(); self.root.mkdir()
        self.process(1, 'systemd'); self.process(50, 'steam')

    def process(self, pid, comm):
        (self.proc / str(pid)).mkdir(exist_ok=True); (self.proc / str(pid) / 'comm').write_text(comm + '\n')

    def gone(self, pid): shutil.rmtree(self.proc / str(pid))

    def args(self, *extra): return [str(BINARY[0]), '--root', str(self.root), '--proc', str(self.proc), *extra]

    def once(self, *extra):
        return subprocess.run(self.args('--once', *extra), capture_output=True, text=True, timeout=20)

    @property
    def group(self): return self.root / 'wisp-ui'
    def members(self): return [int(x) for x in (self.group / 'cgroup.procs').read_text().split()] if (self.group / 'cgroup.procs').exists() else []
    def high(self):
        try: return int((self.group / 'memory.high').read_text())
        except (OSError, ValueError): return None                  # absent, or caught between the daemon's truncate and its write (a fake file; a real cgroup file has no such instant)

    def wait_for(self, fn, what, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            if fn(): return
            time.sleep(0.05)
        self.fail('timed out waiting for ' + what)


class OnePassTests(Base):
    def test_web_helpers_go_into_the_ui_cgroup_and_nothing_else_does(self):
        for pid in (101, 102, 103): self.process(pid, 'steamwebhelper')
        self.process(200, 'steamwebhelper2'); self.process(201, 'Steamwebhelper'); self.process(202, 'quake3.exe')         # near misses by name
        r = self.once()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(self.members()), [101, 102, 103])
        self.assertIn('+memory', (self.root / 'cgroup.subtree_control').read_text())

    def test_without_a_game_the_ceiling_is_the_idle_one(self):
        self.process(101, 'steamwebhelper'); self.once()
        self.assertEqual(self.high(), 300 * MIB)

    def test_with_a_game_the_ceiling_is_the_small_one_and_the_game_is_never_in_the_group(self):
        self.process(101, 'steamwebhelper'); self.process(300, 'reaper'); self.process(301, 'quake3.exe')
        self.once()
        self.assertEqual(self.high(), 96 * MIB); self.assertEqual(self.members(), [101])

    def test_the_ceilings_are_arguments(self):
        self.process(300, 'reaper'); self.once('--high-idle', str(512 * MIB), '--high-game', str(64 * MIB))
        self.assertEqual(self.high(), 64 * MIB)

    def test_a_missing_cgroup_root_is_a_loud_failure(self):
        shutil.rmtree(self.root)
        r = self.once(); self.assertEqual(r.returncode, 1); self.assertIn('no cgroup v2 hierarchy at', r.stderr); self.assertIn(str(self.root), r.stderr)

    def test_bad_arguments_exit_2(self):
        for extra in (['--bogus'], ['--high-game', str(500 * MIB)], ['--high-idle', '0'], ['--poll', '5']):
            r = subprocess.run(self.args('--once', *extra), capture_output=True, text=True); self.assertEqual(r.returncode, 2, extra)


class FreezeTests(Base):
    """--freeze with the visibility rule: frozen only while a game runs AND the focused window is not the Steam UI (class exactly `steam`), fed by sway window events."""
    @staticmethod
    def focus(cls, name='x'):
        wp = '"window_properties": { "class": "%s", "instance": "%s" }' % (cls, cls) if cls is not None else '"shell": "xdg_shell"'            # a Wayland window has no window_properties
        return '{ "change": "focus", "container": { "id": 1, "type": "con", "name": "%s", "focused": true, "nodes": [ ], %s } }\n' % (name, wp)

    def setUp(self):
        super().setUp()
        self.events = self.d / 'events'; self.events.write_text('')
        self.fake = self.d / 'swaymsg'; self.fake.write_text('#!/bin/sh\necho "$@" > %s/swaymsg.args\necho $$ > %s/swaymsg.pid\nexec tail -n 0 -F %s\n' % (self.d, self.d, self.events)); self.fake.chmod(0o755)

    def say(self, line):
        with open(self.events, 'a') as f: f.write(line)

    def freeze_state(self):
        try: return (self.group / 'cgroup.freeze').read_text().strip()
        except OSError: return None

    def run_loop(self, *extra, swaymsg=True):
        args = ['--poll', '100', '--freeze', *(['--swaymsg', str(self.fake)] if swaymsg else []), *extra]
        p = subprocess.Popen(self.args(*args), stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (p.poll() is None and p.terminate(), p.wait(10), p.stderr.close()))
        return p

    def test_without_the_flag_the_freezer_is_never_touched(self):
        self.process(101, 'steamwebhelper'); self.process(300, 'reaper'); self.once()
        self.assertIsNone(self.freeze_state())

    def test_the_subscription_is_to_sway_window_events(self):
        self.process(101, 'steamwebhelper'); self.run_loop(); self.wait_for(lambda: (self.d / 'swaymsg.args').exists(), 'the subscription')
        self.assertEqual((self.d / 'swaymsg.args').read_text().strip(), '-t subscribe -m ["window"]')

    def test_a_game_in_front_freezes_the_ui_after_the_ceiling_is_down_and_the_ui_coming_back_thaws_it_before_the_ceiling_goes_up(self):
        self.process(101, 'steamwebhelper'); p = self.run_loop()
        self.wait_for(lambda: self.freeze_state() == '0', 'the start-up thaw')
        self.process(300, 'reaper'); time.sleep(0.5)
        self.assertEqual(self.freeze_state(), '0'); self.assertEqual(self.high(), 300 * MIB)                 # a game, but the UI has not been told to hide: visible until a focus event says otherwise
        self.say(self.focus('steam_app_2200', 'Quake 3: Arena')); self.wait_for(lambda: self.freeze_state() == '1', 'the freeze')
        self.assertEqual(self.high(), 96 * MIB)
        self.say(self.focus('steam', 'Steam Big Picture Mode')); self.wait_for(lambda: self.freeze_state() == '0' and self.high() == 300 * MIB, 'the thaw and the idle ceiling')
        self.say(self.focus('steam_app_2200')); self.wait_for(lambda: self.freeze_state() == '1', 'the second freeze')
        self.gone(300); self.wait_for(lambda: self.freeze_state() == '0' and self.high() == 300 * MIB, 'the thaw after the game ends')
        p.terminate(); err = p.communicate(timeout=10)[1]
        order = [l.split('wisp-ui-reclaim: wisp-ui: ')[1] for l in err.splitlines() if 'wisp-ui-reclaim: wisp-ui: ' in l and 'web helper' not in l and 'stopped' not in l]
        want = ['thawed', 'memory.high 300 MiB (no game)', 'memory.high 96 MiB (a game is running)', 'frozen (a game is running)', 'thawed', 'memory.high 300 MiB (a game is running, the Steam UI is in front)',
                'memory.high 96 MiB (a game is running)', 'frozen (a game is running)', 'thawed', 'memory.high 300 MiB (no game)']
        self.assertEqual(order[:len(want)], want, err)            # down first, then frozen; back: thawed first, then the ceiling up

    def test_only_the_exact_class_steam_is_the_ui(self):
        self.process(101, 'steamwebhelper'); self.process(300, 'reaper'); self.run_loop(); self.wait_for(lambda: self.freeze_state() == '0', 'start')
        for cls in ('steamwebhelper', 'steam_app_480', 'Steam', None):                                           # near misses and a Wayland window: none of them is the UI
            self.say(self.focus('steam')); self.wait_for(lambda: self.freeze_state() == '0', 'thaw')
            self.say(self.focus(cls)); self.wait_for(lambda: self.freeze_state() == '1', 'freeze for class %r' % cls)

    def test_a_lost_subscription_means_visible_never_frozen_blind(self):
        self.process(101, 'steamwebhelper'); self.process(300, 'reaper')
        broken = self.d / 'swaymsg-broken'; broken.write_text('#!/bin/sh\nexit 1\n'); broken.chmod(0o755)
        p = subprocess.Popen(self.args('--poll', '100', '--freeze', '--swaymsg', str(broken)), stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (p.poll() is None and p.terminate(), p.wait(10), p.stderr.close()))
        self.wait_for(lambda: self.freeze_state() == '0', 'start'); time.sleep(1.0)
        self.assertEqual(self.freeze_state(), '0'); self.assertEqual(self.high(), 300 * MIB)
        p.terminate(); self.assertIn('treating the UI as visible', p.communicate(timeout=10)[1])

    def test_sway_going_away_while_frozen_thaws_the_ui(self):
        self.process(101, 'steamwebhelper'); self.process(300, 'reaper'); p = self.run_loop()
        self.wait_for(lambda: self.freeze_state() == '0', 'start'); self.say(self.focus('steam_app_1')); self.wait_for(lambda: self.freeze_state() == '1', 'freeze')
        subprocess.run(['sh', '-c', "for q in $(ps -eo pid,args | grep 'tail -n 0 -F %s' | grep -v grep | awk '{print $1}'); do kill $q; done" % self.events])      # the stream ends
        self.wait_for(lambda: self.freeze_state() == '0', 'the thaw when the stream ended')

    def test_a_freeze_left_by_a_killed_predecessor_is_cleared_at_start(self):
        self.process(101, 'steamwebhelper'); self.group.mkdir(); (self.group / 'cgroup.freeze').write_text('1\n')
        r = self.once('--freeze'); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.freeze_state(), '0')

    def test_stopping_while_frozen_thaws_and_lifts_the_ceiling(self):
        self.process(101, 'steamwebhelper'); self.process(300, 'reaper'); p = self.run_loop()
        self.wait_for(lambda: self.freeze_state() == '0', 'start'); self.say(self.focus('steam_app_2200')); self.wait_for(lambda: self.freeze_state() == '1', 'the freeze')
        p.send_signal(signal.SIGTERM); err = p.communicate(timeout=10)[1]
        self.assertEqual(p.returncode, 0, err); self.assertEqual(self.freeze_state(), '0'); self.assertEqual((self.group / 'memory.high').read_text().strip(), 'max')
        pid = int((self.d / 'swaymsg.pid').read_text())
        self.wait_for(lambda: not Path('/proc/%d' % pid).exists() or Path('/proc/%d/stat' % pid).read_text().rsplit(')', 1)[1].split()[0] == 'Z', 'the event subscriber to be ended with the daemon')


class LoopTests(Base):
    def run_loop(self):
        p = subprocess.Popen(self.args('--poll', '100'), stderr=subprocess.PIPE, text=True)
        self.addCleanup(lambda: (p.poll() is None and p.terminate(), p.wait(10), p.stderr.close()))
        return p

    def test_a_game_starting_and_ending_moves_the_ceiling_down_and_back_and_writes_only_on_change(self):
        self.process(101, 'steamwebhelper'); p = self.run_loop()
        self.wait_for(lambda: self.high() == 300 * MIB, 'the idle ceiling')
        self.process(300, 'reaper'); self.wait_for(lambda: self.high() == 96 * MIB, 'the game ceiling')
        self.gone(300); self.wait_for(lambda: self.high() == 300 * MIB, 'the idle ceiling again')
        time.sleep(0.5); p.terminate(); err = p.communicate(timeout=10)[1]
        lines = [l for l in err.splitlines() if 'memory.high' in l and 'stopped' not in l]
        self.assertEqual(len(lines), 3, err)                                                  # idle, game, idle: nothing is written on the polls in between
        self.assertIn('a game is running', lines[1]); self.assertIn('stopped', err)

    def test_stopping_the_daemon_lifts_the_ceiling_so_the_ui_is_never_left_throttled(self):
        self.process(101, 'steamwebhelper'); self.process(300, 'reaper'); p = self.run_loop()
        self.wait_for(lambda: self.high() == 96 * MIB, 'the game ceiling')
        p.send_signal(signal.SIGTERM); err = p.communicate(timeout=10)[1]
        self.assertEqual(p.returncode, 0, err); self.assertEqual((self.group / 'memory.high').read_text().strip(), 'max'); self.assertIn('stopped', err)

    def test_a_recycled_ui_new_helpers_are_picked_up_once_and_gone_ones_are_not_chased(self):
        for pid in (101, 102): self.process(pid, 'steamwebhelper')
        p = self.run_loop(); self.wait_for(lambda: sorted(self.members()) == [101, 102], 'the first helpers')
        self.gone(101); self.gone(102)
        for pid in (401, 402): self.process(pid, 'steamwebhelper')                            # the UI was recycled: new processes
        self.wait_for(lambda: {401, 402} <= set(self.members()), 'the new helpers')
        time.sleep(0.5)
        self.assertEqual(sorted(m for m in self.members() if m in (401, 402)), [401, 402])     # each exactly once, however many polls ran
        p.terminate(); p.communicate(timeout=10)


if __name__ == '__main__':
    unittest.main()
