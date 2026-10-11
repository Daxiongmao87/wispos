"""Tests for os/steamos/wisp-steamos-manager.c (docs/WISPOS-STEAMOS-CONTRACT.md).

The daemon is compiled for the host against systemd's public headers (fetched, hash-pinned, by os/steamos/fetch-sdbus-headers.sh into the HDD work tree) and run on a PRIVATE
bus, with a scripted fake update helper and a fake cpufreq/os-release/device-tree tree, so nothing on this machine is touched. Valve's own interface definitions
(os/steamos/interfaces/*.xml) are the oracle: the daemon's introspection must match them member by member. It also cross-compiles (syntax only) for aarch64.
If the headers cannot be fetched (offline and not cached) the tests are SKIPPED with that reason, never passed.
"""
import base64, hashlib, json, os, shutil, signal, socket, subprocess, sys, tempfile, threading, time, unittest
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_wisp_update as tw          # its local update server and signing helpers are reused for the end-to-end tests
WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))
SRC = HERE / 'steamos/wisp-steamos-manager.c'
IFACES = HERE / 'steamos/interfaces'
ATOMUPD, MANAGER = 'com.steampowered.Atomupd1', 'com.steampowered.SteamOSManager1'
APATH, MPATH = '/com/steampowered/Atomupd1', '/com/steampowered/SteamOSManager1'
BUILD = {}                                        # filled by setUpModule: the host binary, or a reason to skip


def setUpModule():
    if not str(WORK).startswith('/mnt/hdd/'): BUILD['skip'] = 'the work directory must be on the HDD (WISP_WORK=%s)' % WORK; return
    tmp = WORK / 'tmp'; tmp.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(['bash', str(HERE / 'steamos/fetch-sdbus-headers.sh'), str(WORK)], capture_output=True, text=True)
    if r.returncode != 0: BUILD['skip'] = "systemd's public headers are not available (offline and not cached?): " + r.stderr.strip(); return
    inc = r.stdout.strip(); out = tmp / 'test-wisp-steamos-manager'
    c = subprocess.run(['gcc', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror', '-I', inc, '-o', str(out), str(SRC), '/usr/lib/x86_64-linux-gnu/libsystemd.so.0'], capture_output=True, text=True)
    if c.returncode != 0: raise RuntimeError('wisp-steamos-manager.c does not compile:\n' + c.stderr)      # a real error fails the run, it is never a skip
    BUILD.update(binary=out, inc=inc, tmp=tmp)


def members(xml_text, only=None):
    """{interface: set of ('method', name, ((direction, type), ...)) / ('property', name, type, access)} from introspection XML."""
    out = {}
    for iface in ET.fromstring(xml_text.strip()).iter('interface'):
        name = iface.get('name')
        if only and name not in only: continue
        s = set()
        for e in iface:
            if e.tag == 'method': s.add(('method', e.get('name'), tuple((a.get('direction', 'in'), a.get('type')) for a in e.findall('arg'))))
            elif e.tag == 'property': s.add(('property', e.get('name'), e.get('type'), e.get('access')))
        out[name] = s
    return out


class Fixture(unittest.TestCase):
    """A private bus, the daemon on it (both roles), a scripted helper and fake sysfs/os-release/device-tree files."""
    ROLES = ['--atomupd', 'system', '--manager', 'system']

    def setUp(self):
        if 'skip' in BUILD: self.skipTest(BUILD['skip'])
        self.d = Path(tempfile.mkdtemp(prefix='wispos-sm-', dir=BUILD['tmp']))
        self.addCleanup(shutil.rmtree, self.d, True)
        (self.d / 'bus.conf').write_text('<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN" "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">'
            '<busconfig><type>session</type><listen>unix:path=%s/bus</listen><auth>EXTERNAL</auth><policy context="default"><allow send_destination="*" eavesdrop="true"/>'
            '<allow eavesdrop="true"/><allow own="*"/></policy></busconfig>' % self.d)
        self.bus = subprocess.Popen(['dbus-daemon', '--config-file=%s/bus.conf' % self.d, '--nofork', '--print-address=1'], stdout=subprocess.PIPE, text=True)
        self.addCleanup(self.stop, self.bus)
        self.addr = self.bus.stdout.readline().strip(); self.assertTrue(self.addr.startswith('unix:'), self.addr)
        (self.d / 'os-release').write_text('OS_NAME="WispOS"\nWISPOS_VERSION="0.1.0"\nBUILD_ID="20261011.1"\n')
        (self.d / 'model').write_bytes(b'Anbernic RG34XXSP\0')
        for pol in ('policy0', 'policy4'):
            (self.d / 'cpufreq' / pol).mkdir(parents=True)
            (self.d / 'cpufreq' / pol / 'scaling_available_governors').write_text('performance schedutil powersave\n')
            (self.d / 'cpufreq' / pol / 'scaling_governor').write_text('schedutil\n')
        (self.d / 'helper.sh').write_text('''#!/bin/sh
D="$FAKE_DIR"; echo $$ > "$D/$1.pid"
case "$1" in
 check) cat "$D/check.out" 2>/dev/null
        if [ -f "$D/check.fail" ]; then echo "error NETWORK_DOWN the update server cannot be reached"; exit 1; fi; exit 0;;
 apply) echo "$2" > "$D/apply.id"; trap 'echo terminated >> "$D/events"; exit 143' TERM
        n=$(cat "$D/apply.steps" 2>/dev/null || echo 20); i=0
        while [ $i -lt $n ]; do i=$((i + 1)); echo "progress $((i * 100 / n)) 1800000000"
            if [ -f "$D/apply.fail" ] && [ $i -eq 3 ]; then echo "error DISK_FULL not enough free space on the card"; exit 1; fi
            sleep 0.1; done
        echo done; exit 0;;
esac
''')
        (self.d / 'helper.sh').chmod(0o755)
        (self.d / 'check.out').write_text('update 20261012.1 0.1.1 stable 1048576 0\nlater 20261020.1 0.2.0 stable 2097152 1 0.1.1\n')
        self.extra_env = {}
        self.prepare()
        self.start_daemon()

    def prepare(self):
        """A subclass may set self.extra_env (extra environment for the daemon and so for its helper) before the daemon starts."""

    def start_daemon(self, roles=None):
        notify = self.d / 'notify'
        if notify.exists(): notify.unlink()
        ns = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM); ns.bind(str(notify)); ns.settimeout(10)
        env = {**os.environ, 'DBUS_SYSTEM_BUS_ADDRESS': self.addr, 'DBUS_SESSION_BUS_ADDRESS': self.addr, 'NOTIFY_SOCKET': str(notify), 'FAKE_DIR': str(self.d),
               'WISP_UPDATE_HELPER': str(self.d / 'helper.sh'), 'WISP_OS_RELEASE': str(self.d / 'os-release'), 'WISP_CPUFREQ_DIR': str(self.d / 'cpufreq'), 'WISP_DT_MODEL': str(self.d / 'model'),
               **self.extra_env}
        self.daemon = subprocess.Popen([str(BUILD['binary']), *(roles or self.ROLES)], env=env, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop, self.daemon)
        try: self.assertIn(b'READY=1', ns.recv(256))              # sd_notify, the way the systemd unit is told the daemon is up
        finally: ns.close()

    @staticmethod
    def stop(p):
        if p.poll() is None:
            p.terminate()
            try: p.wait(10)
            except subprocess.TimeoutExpired: p.kill(); p.wait()
        for f in (p.stdout, p.stderr):
            if f: f.close()

    def busctl(self, *args, check=True):
        r = subprocess.run(['busctl', '--address=' + self.addr, *args], capture_output=True, text=True, timeout=30)
        if check: self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def prop(self, name, iface=ATOMUPD, dest=ATOMUPD, path=APATH):
        return json.loads(self.busctl('--json=short', 'get-property', dest, path, iface, name).stdout)['data']

    def call(self, method, *sig, iface=ATOMUPD, check=True):
        return self.busctl('--json=short', 'call', ATOMUPD, APATH, iface, method, *sig, check=check)

    def wait_for(self, fn, what, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            v = fn()
            if v: return v
            time.sleep(0.05)
        self.fail('timed out waiting for ' + what)

    def status(self): return self.prop('UpdateStatus')


class InterfaceTests(Fixture):
    def introspect(self, dest, path):
        return members(self.busctl('--xml-interface', 'introspect', dest, path).stdout)

    def test_atomupd_matches_valves_interface_plus_the_one_member_steam_calls_beyond_it(self):
        want = members((IFACES / 'com.steampowered.Atomupd1.xml').read_text())[ATOMUPD]
        got = self.introspect(ATOMUPD, APATH)[ATOMUPD]
        self.assertEqual(want - got, set(), 'members of Valve\'s interface that WispOS lacks')
        self.assertEqual(got - want, {('method', 'DisableHttpProxy', ())}, 'extra members must be exactly the one Steam is seen calling (captured 2026-10-11)')

    def test_manager_interfaces_we_claim_match_valves_exactly(self):
        valve = members((IFACES / 'com.steampowered.SteamOSManager1.xml').read_text())
        got = self.introspect(MANAGER, MPATH)
        claimed = {k for k in got if k.startswith(MANAGER + '.')}
        self.assertEqual(claimed, {MANAGER + '.Manager2', MANAGER + '.CpuScaling1'})
        for name in claimed: self.assertEqual(got[name], valve[name], name)

    def test_the_object_manager_at_the_root_lists_the_manager_object_with_the_interfaces_we_claim(self):
        # Steam calls this first (measured on the device); without it it logs "failed to connect to OS service" and never uses the manager
        r = json.loads(self.busctl('--json=short', 'call', MANAGER, '/', 'org.freedesktop.DBus.ObjectManager', 'GetManagedObjects').stdout)
        objects = r['data'][0]
        self.assertIn(MPATH, objects)
        ifaces = set(objects[MPATH])
        self.assertTrue({MANAGER + '.Manager2', MANAGER + '.CpuScaling1'} <= ifaces, ifaces)
        self.assertNotIn(MANAGER + '.Storage1', ifaces)                                           # and nothing we do not implement
        cpu = objects[MPATH][MANAGER + '.CpuScaling1']
        self.assertEqual(sorted(cpu), ['AvailableCpuScalingGovernors', 'CpuScalingGovernor'])     # properties come with their values, as Valve's daemon lists them

    def test_interfaces_we_do_not_implement_are_absent_not_faked(self):
        got = self.introspect(MANAGER, MPATH)
        for name in ('Storage1', 'SessionManagement1', 'GpuPerformanceLevel1', 'PerformanceProfile1', 'TdpLimit1', 'FanControl1', 'FactoryReset1', 'UpdateBios1'):
            self.assertNotIn(MANAGER + '.' + name, got)

    def test_usage_errors_exit_2(self):
        for args in ([], ['--atomupd'], ['--atomupd', 'bogus'], ['--manager', 'system', '--extra']):
            r = subprocess.run([str(BUILD['binary']), *args], capture_output=True, text=True, env={**os.environ, 'DBUS_SYSTEM_BUS_ADDRESS': self.addr})
            self.assertEqual(r.returncode, 2, args)

    def test_roles_can_be_split_across_two_processes_on_one_bus(self):
        # on the device each role runs in its own process on its own bus; on one bus they must not collide
        self.stop(self.daemon)
        self.start_daemon(['--atomupd', 'system'])
        self.assertEqual(self.prop('CurrentBuildID'), '20261011.1')
        second = subprocess.Popen([str(BUILD['binary']), '--manager', 'system'], env={**os.environ, 'DBUS_SYSTEM_BUS_ADDRESS': self.addr, 'WISP_DT_MODEL': str(self.d / 'model'),
                                  'WISP_CPUFREQ_DIR': str(self.d / 'cpufreq')}, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop, second)
        self.wait_for(lambda: self.busctl('--json=short', 'get-property', MANAGER, MPATH, MANAGER + '.Manager2', 'DeviceModel', check=False).returncode == 0, 'the manager role')


class ManagerTests(Fixture):
    def test_device_model_is_the_device_trees_name(self):
        self.assertEqual(self.prop('DeviceModel', MANAGER + '.Manager2', MANAGER, MPATH), ['Anbernic RG34XXSP', 'unknown'])
        (self.d / 'model').unlink()
        self.assertEqual(self.prop('DeviceModel', MANAGER + '.Manager2', MANAGER, MPATH), ['unknown', 'unknown'])

    def test_reload_config_succeeds(self):
        self.busctl('call', MANAGER, MPATH, MANAGER + '.Manager2', 'ReloadConfig')

    def test_governors_are_read_live_and_written_to_every_policy(self):
        cs = MANAGER + '.CpuScaling1'
        self.assertEqual(self.prop('AvailableCpuScalingGovernors', cs, MANAGER, MPATH), ['performance', 'schedutil', 'powersave'])
        self.assertEqual(self.prop('CpuScalingGovernor', cs, MANAGER, MPATH), 'schedutil')
        self.busctl('set-property', MANAGER, MPATH, cs, 'CpuScalingGovernor', 's', 'performance')
        for pol in ('policy0', 'policy4'): self.assertEqual((self.d / 'cpufreq' / pol / 'scaling_governor').read_text().strip(), 'performance')
        (self.d / 'cpufreq/policy0/scaling_governor').write_text('powersave\n')                 # changed behind the daemon's back: the next read shows the truth
        self.assertEqual(self.prop('CpuScalingGovernor', cs, MANAGER, MPATH), 'powersave')

    def test_an_unknown_governor_is_refused_and_nothing_is_written(self):
        r = self.busctl('set-property', MANAGER, MPATH, MANAGER + '.CpuScaling1', 'CpuScalingGovernor', 's', 'turbo-ludicrous', check=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn('AvailableCpuScalingGovernors', r.stderr)
        for pol in ('policy0', 'policy4'): self.assertEqual((self.d / 'cpufreq' / pol / 'scaling_governor').read_text().strip(), 'schedutil')

    def test_no_cpufreq_means_an_empty_list_not_an_error(self):
        shutil.rmtree(self.d / 'cpufreq')
        self.assertEqual(self.prop('AvailableCpuScalingGovernors', MANAGER + '.CpuScaling1', MANAGER, MPATH), [])


class UpdateTests(Fixture):
    def test_identity_comes_from_os_release_and_there_is_one_variant(self):
        self.assertEqual(self.prop('Version'), 1)
        self.assertEqual(self.prop('CurrentVersion'), '0.1.0'); self.assertEqual(self.prop('CurrentBuildID'), '20261011.1')
        self.assertEqual(self.prop('Variant'), 'stable'); self.assertEqual(self.prop('KnownVariants'), ['stable'])
        self.assertEqual(self.status(), 0); self.assertEqual(self.prop('ProgressPercentage'), 0); self.assertEqual(self.prop('UpdateBuildID'), '')
        self.assertEqual(self.prop('UpdatesAvailable'), {}); self.assertEqual(self.prop('UpdatesAvailableLater'), {})      # busctl shows an empty dictionary as {}

    def test_identity_falls_back_to_the_standard_keys_and_never_uses_a_missing_one(self):
        (self.d / 'os-release').write_text('VERSION_ID=0.2.0\nBUILD_ID=20270101.3\n')                          # no WISPOS_VERSION: the standard key answers
        self.stop(self.daemon); self.start_daemon()
        self.assertEqual((self.prop('CurrentVersion'), self.prop('CurrentBuildID')), ('0.2.0', '20270101.3'))
        (self.d / 'os-release').write_text('')
        self.stop(self.daemon); self.start_daemon()
        self.assertEqual((self.prop('CurrentVersion'), self.prop('CurrentBuildID')), ('unknown', 'unknown'))

    def test_check_returns_both_maps_with_valves_keys_and_publishes_them_as_properties(self):
        r = json.loads(self.call('CheckForUpdates', 'a{sv}', '0').stdout)
        self.assertEqual(r['type'], 'a{sa{sv}}a{sa{sv}}')
        avail, later = r['data']
        self.assertEqual(set(avail), {'20261012.1'})
        u = {k: v['data'] for k, v in avail['20261012.1'].items()}
        self.assertEqual(u, {'variant': 'stable', 'version': '0.1.1', 'estimated_size': 1048576, 'update_type': 0})
        l = {k: v['data'] for k, v in later['20261020.1'].items()}
        self.assertEqual(l, {'variant': 'stable', 'version': '0.2.0', 'estimated_size': 2097152, 'update_type': 1, 'requires': '0.1.1'})
        self.assertEqual(self.prop('UpdatesAvailable'), avail); self.assertEqual(self.prop('UpdatesAvailableLater'), later)

    def test_check_with_nothing_new_returns_two_empty_maps(self):
        (self.d / 'check.out').write_text('')
        self.assertEqual(json.loads(self.call('CheckForUpdates', 'a{sv}', '0').stdout)['data'], [{}, {}])

    def test_a_failed_check_is_a_dbus_error_with_the_helpers_words(self):
        (self.d / 'check.fail').write_text('')
        r = self.call('CheckForUpdates', 'a{sv}', '0', check=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn('the update server cannot be reached', r.stderr)
        self.assertEqual(self.status(), 0)                                                    # a failed check is not a failed update

    def test_a_missing_helper_fails_the_check_loudly_not_silently(self):
        (self.d / 'helper.sh').unlink()
        r = self.call('CheckForUpdates', 'a{sv}', '0', check=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn('update helper could not be run', r.stderr)
        self.assertEqual(self.status(), 0)

    def test_an_update_whose_helper_cannot_run_fails_with_that_reason(self):
        self.call('CheckForUpdates', 'a{sv}', '0')
        (self.d / 'helper.sh').unlink()
        self.call('StartUpdate', 's', '20261012.1')
        self.wait_for(lambda: self.status() == 4, 'the update to fail')
        self.assertEqual(self.prop('FailureMessage'), 'the update helper could not be run')

    def test_start_refuses_an_id_the_check_did_not_offer(self):
        r = self.call('StartUpdate', 's', '20261012.1', check=False)                         # no check yet
        self.assertNotEqual(r.returncode, 0); self.assertIn('CheckForUpdates', r.stderr)
        self.call('CheckForUpdates', 'a{sv}', '0')
        self.assertNotEqual(self.call('StartUpdate', 's', '20261020.1', check=False).returncode, 0)   # offered only for later: needs a newer base first
        self.assertEqual(self.status(), 0)

    def test_an_update_runs_to_success_with_progress_and_the_chosen_id(self):
        self.call('CheckForUpdates', 'a{sv}', '0'); self.call('StartUpdate', 's', '20261012.1')
        self.assertEqual(self.prop('UpdateBuildID'), '20261012.1')
        self.wait_for(lambda: self.status() == 1 or self.status() == 3, 'the update to start')
        self.wait_for(lambda: self.status() == 3, 'the update to succeed')
        self.assertEqual(self.prop('ProgressPercentage'), 100); self.assertEqual(self.prop('FailureCode'), ''); self.assertEqual((self.d / 'apply.id').read_text().strip(), '20261012.1')

    def test_progress_and_eta_are_published_while_it_runs(self):
        (self.d / 'apply.steps').write_text('40')
        self.call('CheckForUpdates', 'a{sv}', '0'); self.call('StartUpdate', 's', '20261012.1')
        self.wait_for(lambda: 0 < self.prop('ProgressPercentage') < 100, 'progress between 0 and 100')
        self.assertEqual(self.prop('EstimatedCompletionTime'), 1800000000)
        self.assertEqual(self.status(), 1)

    def test_a_second_start_while_running_is_refused(self):
        (self.d / 'apply.steps').write_text('50')
        self.call('CheckForUpdates', 'a{sv}', '0'); self.call('StartUpdate', 's', '20261012.1')
        r = self.call('StartUpdate', 's', '20261012.1', check=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn('already', r.stderr)
        # asking again while it runs answers from the last check and does not disturb the helper
        self.assertEqual(json.loads(self.call('CheckForUpdates', 'a{sv}', '0').stdout)['type'], 'a{sa{sv}}a{sa{sv}}')
        self.assertEqual(self.status(), 1)
        self.call('CancelUpdate'); self.wait_for(lambda: self.status() == 5, 'cancel')

    def test_a_failing_update_reports_code_and_message_and_can_be_retried(self):
        (self.d / 'apply.fail').write_text('')
        self.call('CheckForUpdates', 'a{sv}', '0'); self.call('StartUpdate', 's', '20261012.1')
        self.wait_for(lambda: self.status() == 4, 'the update to fail')
        self.assertEqual(self.prop('FailureCode'), 'org.wispos.UpdateError.DISK_FULL'); self.assertEqual(self.prop('FailureMessage'), 'not enough free space on the card')
        (self.d / 'apply.fail').unlink()
        self.call('StartUpdate', 's', '20261012.1')                                           # a failed update may be started again; the failure fields clear
        self.assertEqual(self.prop('FailureCode'), '')
        self.wait_for(lambda: self.status() == 3, 'the retry to succeed')

    def test_pause_stops_the_helper_and_its_children_and_resume_continues(self):
        (self.d / 'apply.steps').write_text('100')
        self.call('CheckForUpdates', 'a{sv}', '0'); self.call('StartUpdate', 's', '20261012.1')
        pid = int(self.wait_for(lambda: (self.d / 'apply.pid').exists() and (self.d / 'apply.pid').read_text().strip(), 'the helper'))
        def state(): return Path('/proc/%d/stat' % pid).read_text().rsplit(')', 1)[1].split()[0]
        self.call('PauseUpdate'); self.assertEqual(self.status(), 2)
        self.wait_for(lambda: state() == 'T', 'the helper to stop')
        before = self.prop('ProgressPercentage'); time.sleep(0.5); self.assertEqual(self.prop('ProgressPercentage'), before)
        self.call('ResumeUpdate'); self.assertEqual(self.status(), 1)
        self.wait_for(lambda: self.prop('ProgressPercentage') > before, 'progress after resume')
        self.call('CancelUpdate'); self.wait_for(lambda: self.status() == 5, 'cancel')

    def test_resume_without_a_pause_is_an_error(self):
        self.assertNotEqual(self.call('ResumeUpdate', check=False).returncode, 0)

    def test_cancel_terminates_the_helper_so_it_can_clean_up_even_when_paused(self):
        (self.d / 'apply.steps').write_text('100')
        self.call('CheckForUpdates', 'a{sv}', '0'); self.call('StartUpdate', 's', '20261012.1')
        self.wait_for(lambda: self.prop('ProgressPercentage') > 0, 'the update to run')
        self.call('PauseUpdate'); self.call('CancelUpdate')
        self.wait_for(lambda: self.status() == 5, 'the status to become CANCELLED')
        self.wait_for(lambda: (self.d / 'events').exists() and 'terminated' in (self.d / 'events').read_text(), 'the helper to see SIGTERM')
        self.assertEqual(self.prop('FailureCode'), '')                                        # a cancel is not a failure

    def test_pause_and_cancel_with_nothing_running_are_harmless(self):
        self.call('PauseUpdate'); self.call('CancelUpdate'); self.assertEqual(self.status(), 0)

    def test_switching_to_another_variant_is_refused(self):
        self.call('SwitchToVariant', 's', 'stable')
        r = self.call('SwitchToVariant', 's', 'steamdeck-beta', check=False)
        self.assertNotEqual(r.returncode, 0)

    def test_steams_start_up_proxy_call_succeeds(self):
        self.call('DisableHttpProxy')

    def test_stopping_the_daemon_stops_a_running_helper(self):
        (self.d / 'apply.steps').write_text('100')
        self.call('CheckForUpdates', 'a{sv}', '0'); self.call('StartUpdate', 's', '20261012.1')
        pid = int(self.wait_for(lambda: (self.d / 'apply.pid').exists() and (self.d / 'apply.pid').read_text().strip(), 'the helper'))
        self.daemon.send_signal(signal.SIGTERM); self.assertEqual(self.daemon.wait(10), 0)
        self.wait_for(lambda: not Path('/proc/%d' % pid).exists() or Path('/proc/%d/stat' % pid).read_text().rsplit(')', 1)[1].split()[0] == 'Z', 'the helper to end')


class EndToEndTests(Fixture):
    """The daemon driving the REAL helper (os/steamos/wisp-update) against a local, signed update server: what Steam's UI would get."""
    def prepare(self):
        self.srv = tw.Server(('127.0.0.1', 0), tw.Handler); self.srv.files, self.srv.log, self.srv.drop, self.srv.throttle, self.srv.honor_range = {}, [], {}, 0, True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start(); self.addCleanup(self.srv.server_close); self.addCleanup(self.srv.shutdown)
        self.base = 'http://127.0.0.1:%d' % self.srv.server_address[1]; self.flash = self.d / 'flash'; self.flash.mkdir()
        for args in (('genpkey', '-algorithm', 'ED25519', '-out', str(self.d / 'key.pem')),): subprocess.run(['openssl', *args], check=True, capture_output=True)
        subprocess.run(['openssl', 'pkey', '-in', str(self.d / 'key.pem'), '-pubout', '-out', str(self.d / 'pub.pem')], check=True, capture_output=True)
        (self.d / 'os-release').write_text('BUILD_ID="20261011.1"\nWISPOS_VERSION="0.1.0"\nHW_DEVICE="H700"\n')
        (self.d / 'update.conf').write_text(json.dumps({'manifest_url': self.base + '/manifest.json'}))
        self.publish()
        self.extra_env = {'WISP_UPDATE_HELPER': str(tw.HELPER), 'WISP_UPDATE_CONF': str(self.d / 'update.conf'), 'WISP_UPDATE_PUBKEY': str(self.d / 'pub.pem'),
                          'WISP_FLASH_DIR': str(self.flash), 'WISP_UPDATE_NO_REMOUNT': '1', 'WISP_UPDATE_ALLOW_HTTP': '1', 'WISP_UPDATE_NET_TIMEOUT': '5', 'WISP_UPDATE_RETRIES': '1',
                          'WISP_UPDATE_NO_BACKOFF': '1', 'WISP_UPDATE_FREE_MARGIN': '0', 'TMPDIR': str(BUILD['tmp'])}

    def publish(self, tamper=False, corrupt=False):
        files = {n: {'url': self.base + '/' + n, 'size': len(b), 'sha256': hashlib.sha256(b).hexdigest()} for n, b in (('KERNEL', tw.KERNEL), ('SYSTEM', tw.SYSTEM))}
        raw = json.dumps({'format': 1, 'variant': 'stable', 'build_id': '20261012.1', 'version': '0.1.1', 'requires': '', 'update_type': 0, 'hw_device': 'H700', 'files': files}).encode()
        (self.d / 'm.bin').write_bytes(raw)
        sig = subprocess.run(['openssl', 'pkeyutl', '-sign', '-rawin', '-in', str(self.d / 'm.bin'), '-inkey', str(self.d / 'key.pem')], check=True, capture_output=True).stdout
        self.srv.files.update({'/manifest.json': raw.replace(b'0.1.1', b'9.9.9') if tamper else raw, '/manifest.json.sig': base64.b64encode(sig), '/KERNEL': tw.KERNEL,
                               '/SYSTEM': (bytes([tw.SYSTEM[0] ^ 1]) + tw.SYSTEM[1:]) if corrupt else tw.SYSTEM})

    def test_check_start_and_success_with_the_real_helper(self):
        r = json.loads(self.call('CheckForUpdates', 'a{sv}', '0').stdout)['data'][0]
        self.assertEqual(r['20261012.1']['version']['data'], '0.1.1'); self.assertEqual(r['20261012.1']['estimated_size']['data'], len(tw.KERNEL) + len(tw.SYSTEM))
        self.call('StartUpdate', 's', '20261012.1')
        self.wait_for(lambda: self.status() == 3, 'the update to be staged', 60)
        self.assertEqual(self.prop('ProgressPercentage'), 100)
        self.assertEqual(sorted(p.name for p in (self.flash / 'wispos/update').iterdir()), ['KERNEL.new', 'READY', 'SYSTEM.new'])

    def test_without_a_configured_source_the_check_says_so_and_nothing_is_guessed(self):
        (self.d / 'update.conf').write_text('{}')
        r = self.call('CheckForUpdates', 'a{sv}', '0', check=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn('no update source is configured', r.stderr)
        self.assertEqual(self.srv.log, [])                                                    # it did not even try the network

    def test_a_tampered_manifest_is_a_failed_check_with_the_reason(self):
        self.publish(tamper=True)
        r = self.call('CheckForUpdates', 'a{sv}', '0', check=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn('not signed by the key in this image', r.stderr)
        self.assertEqual(self.prop('UpdatesAvailable'), {})

    def test_corrupt_download_fails_the_update_with_a_dbus_error_name(self):
        self.call('CheckForUpdates', 'a{sv}', '0'); self.publish(corrupt=True)
        self.call('StartUpdate', 's', '20261012.1')
        self.wait_for(lambda: self.status() == 4, 'the update to fail', 60)
        self.assertEqual(self.prop('FailureCode'), 'org.wispos.UpdateError.HashMismatch'); self.assertIn('does not match the signed manifest', self.prop('FailureMessage'))
        self.assertFalse((self.flash / 'wispos/update/READY').exists())

    def test_cancelling_mid_download_leaves_no_partial_files_on_the_card(self):
        self.srv.throttle = 0.15
        self.call('CheckForUpdates', 'a{sv}', '0'); self.call('StartUpdate', 's', '20261012.1')
        self.wait_for(lambda: list((self.flash / 'wispos/update').glob('*.part')), 'the download to start', 30)
        self.call('PauseUpdate'); self.assertEqual(self.status(), 2)
        self.call('CancelUpdate'); self.wait_for(lambda: self.status() == 5, 'the cancel', 30)
        self.wait_for(lambda: not list((self.flash / 'wispos/update').iterdir()), 'the helper to clean up', 30)


class CrossCompileTests(unittest.TestCase):
    def test_it_compiles_cleanly_for_the_device(self):
        if 'skip' in BUILD: self.skipTest(BUILD['skip'])
        r = subprocess.run(['aarch64-linux-gnu-gcc', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror', '-fsyntax-only', '-I', BUILD['inc'], str(SRC)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == '__main__':
    unittest.main()
