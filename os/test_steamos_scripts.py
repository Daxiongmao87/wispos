"""Tests for the scripts that connect Steam's System page to WispOS (measured contract, docs/WISPOS-NOTES.md 2026-10-11):
  os/rootfs/usr/bin/steamos-polkit-helpers/steamos-update   the call forms and exit codes Steam uses (0 = update available / done, 7 = none)
  os/rootfs/usr/bin/wisp-steam-launch                       starts the SteamOS manager before Steam, never blocks Steam
Both are run for real with scripted stand-ins for the update helper, the daemon and busctl."""
import os, shutil, subprocess, tempfile, time, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
UPDATE = HERE / 'rootfs/usr/bin/steamos-polkit-helpers/steamos-update'
LAUNCH = HERE / 'rootfs/usr/bin/wisp-steam-launch'
WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))


class Base(unittest.TestCase):
    def setUp(self):
        (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
        self.d = Path(tempfile.mkdtemp(prefix='wispos-scripts-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, self.d, True)

    def script(self, name, body):
        p = self.d / name; p.write_text('#!/bin/sh\n' + body); p.chmod(0o755); return p


class SteamosUpdateTests(Base):
    def setUp(self):
        super().setUp()
        self.calls = self.d / 'calls'
        # a stand-in for wisp-update: `check` offers 20261012.1 unless NO_UPDATE; `apply ID` prints progress and ends as FAIL says
        self.helper = self.script('helper', f'''echo "$@" >> {self.calls}
case "$1" in
 check) [ -n "$NO_UPDATE" ] && exit 0; [ -n "$CHECK_FAILS" ] && {{ echo "error NetworkError down"; exit 1; }}; echo "update 20261012.1 0.1.1 stable 1000 0"; echo "later 20261020.1 0.2.0 stable 1 1 0.1.1";;
 apply) echo "progress 0.0 0"; echo "progress 10.4 99"; echo "progress 10.9 99"; echo "progress 55.0 99"; [ -n "$FAIL" ] && {{ echo "error DiskFull not enough room"; exit 1; }}; echo "progress 100.0 0"; echo done;;
esac
''')
        self.staged = self.d / 'staged'
        self.env = {**os.environ, 'WISP_UPDATE_HELPER': str(self.helper), 'WISP_UPDATE_STAGED_MARK': str(self.staged), 'TMPDIR': str(self.d)}

    def run_update(self, *args, **env):
        r = subprocess.run(['sh', str(UPDATE), *args], capture_output=True, text=True, env={**self.env, **env}, timeout=30)
        return r.returncode, r.stdout, r.stderr

    def helper_calls(self): return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_supports_duplicate_detection_exits_0_and_does_nothing(self):
        self.assertEqual(self.run_update('--supports-duplicate-detection')[0], 0); self.assertEqual(self.helper_calls(), [])

    def test_check_exits_0_when_an_update_is_offered_and_7_when_not_or_when_the_check_fails(self):
        for args in (('check',), ('--enable-duplicate-detection', 'check')):
            rc, out, err = self.run_update(*args); self.assertEqual((rc, out), (0, ''), args)         # nothing on stdout: Steam only reads the exit code
        self.assertEqual(self.run_update('check', NO_UPDATE='1')[0], 7)
        self.assertEqual(self.run_update('check', CHECK_FAILS='1')[0], 7)
        self.assertNotIn('apply', ' '.join(self.helper_calls()))                                      # a check never installs

    def test_installing_prints_each_whole_percent_once_exits_0_and_marks_the_update_staged(self):
        for args in ((), ('--enable-duplicate-detection',)):
            if self.staged.exists(): self.staged.unlink()
            rc, out, err = self.run_update(*args)
            self.assertEqual(rc, 0, err); self.assertEqual(out.split(), ['0%', '10%', '55%', '100%'], args)     # 10.4 and 10.9 are one step: no repeated lines
            self.assertTrue(self.staged.exists())
        self.assertIn('apply 20261012.1', self.helper_calls())                                       # the id comes from the helper's own check

    def test_once_staged_both_a_check_and_a_second_install_say_7_so_steam_asks_for_a_restart(self):
        self.assertEqual(self.run_update()[0], 0)
        self.assertEqual(self.run_update('check')[0], 7); self.assertEqual(self.run_update()[0], 7)

    def test_a_failed_install_exits_1_with_the_reason_and_is_not_marked_staged(self):
        rc, out, err = self.run_update(FAIL='1')
        self.assertEqual(rc, 1); self.assertIn('DiskFull not enough room', err); self.assertFalse(self.staged.exists())
        self.assertEqual(out.split(), ['0%', '10%', '55%'])                                           # the progress that did happen is still reported
        self.assertEqual(self.run_update()[0], 0)                                                    # and the next try can succeed

    def test_installing_with_nothing_to_install_exits_7_without_touching_the_card(self):
        self.assertEqual(self.run_update(NO_UPDATE='1')[0], 7); self.assertNotIn('apply', ' '.join(self.helper_calls()))

    def test_no_temporary_files_are_left_behind(self):
        self.run_update(); self.run_update(FAIL='1', **{})
        self.assertEqual([p.name for p in self.d.glob('steamos-update.*')], [])


class LaunchTests(Base):
    def setUp(self):
        super().setUp()
        self.ready = self.d / 'name-up'; self.log = self.d / 'log'
        # the daemon: records its start, and "takes the name" (creates the file busctl looks at) after DELAY seconds
        self.manager = self.script('manager', f'echo "manager $*" >> {self.log}; exec >/dev/null 2>&1; [ -n "$NEVER" ] && sleep 20 && exit 0; sleep "${{DELAY:-0}}"; : > {self.ready}; sleep 20\n')
        self.busctl = self.script('busctl', f'[ -e {self.ready} ] && echo "com.steampowered.SteamOSManager1 123 manager root :1.6"; echo ":1.0 1 steam root :1.0"\n')
        self.cmd = self.script('command', f'echo "command $* name_up=$([ -e {self.ready} ] && echo yes || echo no)" >> {self.log}; exit "${{CMD_RC:-0}}"\n')
        self.env = {**os.environ, 'WISP_MANAGER': str(self.manager), 'WISP_BUSCTL': str(self.busctl)}

    def run_launch(self, *args, **env):
        t = time.time(); r = subprocess.run(['sh', str(LAUNCH), str(self.cmd), *args], capture_output=True, text=True, env={**self.env, **env}, timeout=30)
        return r.returncode, time.time() - t

    def tearDown(self):                     # the daemon outlives the launcher on purpose (it ends with the session bus); the tests end their stand-ins
        subprocess.run(['sh', '-c', f"for p in $(ps -eo pid,args | grep '{self.d}/manager' | grep -v grep | awk '{{print $1}}'); do kill $p; done"], capture_output=True)

    def test_the_manager_starts_first_and_the_command_runs_only_once_its_name_is_up(self):
        rc, took = self.run_launch('-gamepadui', 'x y', DELAY='1')
        self.assertEqual(rc, 0)
        lines = self.log.read_text().splitlines()
        self.assertEqual(lines[0], 'manager --manager session')                                       # started with the session role
        self.assertEqual(lines[1], 'command -gamepadui x y name_up=yes')                             # arguments intact, and the name was up when it ran
        self.assertGreaterEqual(took, 0.9)

    def test_a_manager_that_never_appears_does_not_block_the_command_for_more_than_a_few_seconds(self):
        rc, took = self.run_launch('go', NEVER='1')
        self.assertEqual(rc, 0); self.assertLess(took, 8); self.assertGreaterEqual(took, 2.5)
        self.assertEqual(self.log.read_text().splitlines()[1], 'command go name_up=no')

    def test_a_missing_manager_binary_is_not_an_error(self):
        rc, took = self.run_launch('go', WISP_MANAGER=str(self.d / 'does-not-exist'))
        self.assertEqual(rc, 0); self.assertIn('command go', self.log.read_text())

    def test_the_commands_exit_status_is_the_launchers(self):
        self.assertEqual(self.run_launch('x', DELAY='0', CMD_RC='42')[0], 42)             # the session script relaunches on 0 and 42, exactly as before

    def test_no_command_is_a_usage_error(self):
        self.assertEqual(subprocess.run(['sh', str(LAUNCH)], capture_output=True).returncode, 2)

    def test_the_session_script_runs_steam_through_it_inside_the_session_bus(self):
        text = (HERE / 'rootfs/usr/bin/wisp-steam-session').read_text()
        line = [l for l in text.splitlines() if 'dbus-run-session' in l and 'wisp-steam-launch' in l]
        self.assertEqual(len(line), 1); self.assertIn('dbus-run-session -- /usr/bin/wisp-steam-launch /usr/bin/env', line[0])


if __name__ == '__main__':
    unittest.main()
