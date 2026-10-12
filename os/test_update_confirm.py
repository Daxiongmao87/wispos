"""The part of the update scheme that runs in the NEW system: os/rootfs/usr/lib/wispos/wisp-update-confirm (and its unit) ends the trial of a freshly updated version once it has
started well, and /usr/bin/steamos-polkit-helpers/steamos-reboot is the restart Steam's UI asks for. The script runs for real with scripted stand-ins for systemctl and the helper.
What is proved: nothing is done when there is no trial; the helper's confirm is called only after the compositor AND a session program have been active together for the whole
hold (any gap restarts the count); a failing confirm is retried and then given up; a trial that ends by other means ends the script; the unit only starts when a trial exists."""
import os, shutil, stat, subprocess, tempfile, threading, time, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / 'rootfs/usr/lib/wispos/wisp-update-confirm'
UNIT = HERE / 'rootfs/usr/lib/systemd/system/wisp-update-confirm.service'
REBOOT = HERE / 'rootfs/usr/bin/steamos-polkit-helpers/steamos-reboot'
WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))


class ConfirmTests(unittest.TestCase):
    def setUp(self):
        (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
        self.d = Path(tempfile.mkdtemp(prefix='wispos-confirm-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, self.d, True)
        self.state = self.d / 'state'; self.state.mkdir(); self.calls = self.d / 'calls'; self.pending = self.d / 'PENDING'; self.pending.write_text('slot=b\n')
        self.restarts = self.d / 'restarts'
        self.systemctl = self.script('systemctl', 'if [ "$1" = is-active ] && [ "$2" = --quiet ] && [ "$(cat "%s/$3" 2>/dev/null)" = active ]; then exit 0; fi\nif [ "$1" = --force ] && [ "$2" = reboot ]; then echo "systemctl $*" >> "%s"; [ -z "$SYSTEMCTL_REBOOT_FAILS" ]; exit $?; fi\nexit 3\n' % (self.state, self.restarts))
        self.reboot_cmd = self.script('reboot', 'echo "reboot $*" >> "%s"\nexit 0\n' % self.restarts)
        self.helper = self.script('helper', 'echo "$@" >> "%s"\nn=$(wc -l < "%s")\n[ -n "$FAIL_FIRST" ] && [ "$n" -le "$FAIL_FIRST" ] && exit 1\n[ -n "$ALWAYS_FAIL" ] && exit 1\nrm -f "%s"; exit 0\n' % (self.calls, self.calls, self.pending))
        self.env = {**os.environ, 'WISP_CONFIRM_POLL': '0.05', 'WISP_CONFIRM_NEED': '4', 'WISP_CONFIRM_LIMIT_POLLS': '100000', 'WISP_SYSTEMCTL': str(self.systemctl), 'WISP_UPDATE_HELPER': str(self.helper), 'WISP_PENDING': str(self.pending), 'WISP_REBOOT': str(self.reboot_cmd)}

    def script(self, name, body):
        p = self.d / name; p.write_text('#!/bin/sh\n' + body); p.chmod(0o755); return p

    def up(self, *units):
        for u in units: (self.state / u).write_text('active')

    def down(self, *units):
        for u in units:
            try: (self.state / u).unlink()
            except FileNotFoundError: pass

    def run_script(self, timeout=20, **env):
        t0 = time.time(); r = subprocess.run(['sh', str(SCRIPT)], capture_output=True, text=True, env={**self.env, **env}, timeout=timeout); return r.returncode, time.time() - t0, r.stderr

    def helper_calls(self): return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_no_trial_means_nothing_is_done(self):
        self.pending.unlink(); self.up('sway.service', 'wisp-steam.service')
        rc, took, err = self.run_script(); self.assertEqual((rc, self.helper_calls()), (0, [])); self.assertLess(took, 2)

    def test_a_healthy_system_confirms_once_after_the_whole_hold(self):
        self.up('sway.service', 'wisp-steam.service')
        rc, took, err = self.run_script(); self.assertEqual((rc, self.helper_calls()), (0, ['confirm']), err)
        self.assertGreaterEqual(took, 0.15)                                                         # 4 polls in a row at 0.05 s: the first three do not confirm

    def test_any_one_session_program_will_do(self):
        for unit in ('wisp-steam.service', 'wisp-setup.service', 'wisp-desktop.service'):
            self.setUp(); self.up('sway.service', unit)
            rc, _, err = self.run_script(); self.assertEqual((rc, self.helper_calls()), (0, ['confirm']), (unit, err))

    def test_without_the_compositor_or_without_a_session_nothing_is_confirmed(self):
        for ups in ((), ('wisp-steam.service',), ('sway.service',), ('sway.service', 'wisp-dock.service'), ('wisp-steam.service', 'wisp-setup.service')):
            self.setUp(); self.up(*ups)
            with self.assertRaises(subprocess.TimeoutExpired): self.run_script(timeout=1)
            self.assertEqual(self.helper_calls(), [], ups)

    def test_a_gap_restarts_the_count(self):
        self.up('sway.service', 'wisp-steam.service'); log = []
        def flap():
            time.sleep(0.12); self.down('wisp-steam.service'); log.append(time.time()); time.sleep(0.12); self.up('wisp-steam.service')
        t = threading.Thread(target=flap); t0 = time.time(); t.start()
        rc, took, err = self.run_script(); t.join()
        self.assertEqual((rc, self.helper_calls()), (0, ['confirm'])); self.assertGreater(time.time() - log[0], 0.18)       # the confirm came after the gap plus a fresh hold, not before it

    def test_a_confirm_that_fails_a_few_times_is_retried_and_then_given_up(self):
        self.up('sway.service', 'wisp-steam.service')
        rc, _, err = self.run_script(FAIL_FIRST='3'); self.assertEqual((rc, len(self.helper_calls())), (0, 4), err)
        self.setUp(); self.up('sway.service', 'wisp-steam.service')
        rc, _, err = self.run_script(ALWAYS_FAIL='1'); self.assertEqual((rc, len(self.helper_calls())), (1, 5)); self.assertIn('could not end the trial', err)

    def test_a_trial_that_ends_by_other_means_ends_the_script_without_a_call(self):
        self.up('sway.service')                                                                      # not healthy yet: it is waiting
        threading.Timer(0.3, self.pending.unlink).start()
        rc, took, err = self.run_script(); self.assertEqual((rc, self.helper_calls()), (0, []))

    def restart_calls(self): return self.restarts.read_text().splitlines() if self.restarts.exists() else []

    def test_a_system_that_never_gets_healthy_is_restarted_after_the_limit_and_not_before(self):
        self.up('sway.service')                                                                      # the compositor runs, no session program ever does
        rc, took, err = self.run_script(WISP_CONFIRM_LIMIT_POLLS='10'); self.assertEqual((rc, self.helper_calls(), self.restart_calls()), (1, [], ['systemctl --force reboot']), err)
        self.assertGreaterEqual(took, 0.4); self.assertIn('not come up well', err)

    def test_a_healthy_system_is_never_restarted(self):
        self.up('sway.service', 'wisp-steam.service')
        rc, _, err = self.run_script(WISP_CONFIRM_LIMIT_POLLS='10'); self.assertEqual((rc, self.helper_calls(), self.restart_calls()), (0, ['confirm'], []))

    def test_the_restart_falls_back_to_reboot_f_when_systemctl_cannot(self):
        self.up('sway.service')
        rc, _, err = self.run_script(WISP_CONFIRM_LIMIT_POLLS='6', SYSTEMCTL_REBOOT_FAILS='1'); self.assertEqual((rc, self.restart_calls()), (1, ['systemctl --force reboot', 'reboot -f']))

    def test_a_trial_that_ends_before_the_limit_is_not_restarted(self):
        self.up('sway.service'); threading.Timer(0.2, self.pending.unlink).start()
        rc, _, err = self.run_script(WISP_CONFIRM_LIMIT_POLLS='20'); self.assertEqual((rc, self.restart_calls()), (0, []))

    def test_the_limit_is_ten_minutes_and_never_shorter_than_the_hold(self):
        text = SCRIPT.read_text()
        self.assertIn('LIMIT=${WISP_CONFIRM_LIMIT:-600}', text)
        self.up('sway.service')                                                                      # a limit of 1 poll must still allow the hold (need=4): the restart comes after more than 4 polls
        rc, took, err = self.run_script(WISP_CONFIRM_LIMIT_POLLS='1'); self.assertEqual(rc, 1); self.assertGreaterEqual(took, 0.2)

    def test_the_limit_in_seconds_becomes_polls_when_the_poll_is_whole_seconds(self):
        self.up('sway.service'); env = dict(self.env); env.pop('WISP_CONFIRM_LIMIT_POLLS')
        t0 = time.time(); r = subprocess.run(['sh', str(SCRIPT)], capture_output=True, text=True, timeout=30, env={**env, 'WISP_CONFIRM_POLL': '1', 'WISP_CONFIRM_NEED': '1', 'WISP_CONFIRM_LIMIT': '3'})
        self.assertEqual((r.returncode, self.restart_calls()), (1, ['systemctl --force reboot']), r.stderr); self.assertTrue(1.8 <= time.time() - t0 <= 6, time.time() - t0)         # the third check, after two one-second polls

    def test_the_hold_defaults_are_ninety_seconds_in_fives(self):
        text = SCRIPT.read_text()
        self.assertIn('HOLD=${WISP_CONFIRM_HOLD:-90}', text); self.assertIn('POLL=${WISP_CONFIRM_POLL:-5}', text)
        r = subprocess.run(['sh', '-c', 'HOLD=90; POLL=5; echo $(( (HOLD + POLL - 1) / POLL ))'], capture_output=True, text=True); self.assertEqual(r.stdout.strip(), '18')


class Wiring(unittest.TestCase):
    def test_the_script_is_valid_shell_and_executable(self):
        subprocess.run(['sh', '-n', str(SCRIPT)], check=True); subprocess.run(['sh', '-n', str(REBOOT)], check=True)
        for p in (SCRIPT, REBOOT): self.assertTrue(p.stat().st_mode & stat.S_IXUSR, p)

    def test_the_unit_starts_only_when_a_trial_exists_and_is_wanted_by_the_target(self):
        text = UNIT.read_text()
        self.assertIn('ConditionPathExists=/flash/wispos/update/PENDING', text); self.assertIn('ExecStart=/usr/lib/wispos/wisp-update-confirm', text); self.assertIn('WantedBy=wispos.target', text)
        link = HERE / 'rootfs/usr/lib/systemd/system/wispos.target.wants/wisp-update-confirm.service'
        self.assertTrue(link.is_symlink()); self.assertEqual(os.readlink(link), '../wisp-update-confirm.service')

    def test_the_unit_is_a_long_running_service_not_a_blocker_of_the_boot(self):
        text = UNIT.read_text(); self.assertIn('Type=simple', text); self.assertNotIn('Before=wispos.target', text)       # it waits minutes; nothing may wait for it

    def test_steamos_reboot_restarts_the_machine_through_systemd(self):
        d = Path(tempfile.mkdtemp(prefix='wispos-reboot-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, d, True)
        (d / 'systemctl').write_text('#!/bin/sh\necho "$@" > "%s/called"\n' % d); (d / 'systemctl').chmod(0o755)
        r = subprocess.run(['sh', str(REBOOT)], env={**os.environ, 'PATH': '%s:%s' % (d, os.environ['PATH'])}, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0); self.assertEqual((d / 'called').read_text().strip(), 'reboot')


if __name__ == '__main__':
    unittest.main()
