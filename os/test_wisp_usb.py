"""Tests for os/rootfs/usr/bin/wisp-usb: the USB-C port's three states (off, computer, host) and the rules that keep host mode safe.

Everything runs against a FAKE /sys and /run under a temporary directory (WISP_USB_ROOT), with the commands it would run (systemctl, ip, udhcpd) recorded instead of executed, so nothing on
this machine is touched. What is proved: the decision rules as a table (refuse host mode with external power, below the battery floor, when the supply cannot be read, when the kernel has no gate);
that `host` writes the gate and starts the watcher only when allowed, and takes the gadget down first; that `computer` builds the CDC-NCM gadget the way configfs requires and tears it down
completely; that the watcher ends host mode on a power-supply uevent (and on the slow battery look) and ignores other uevents. The kernel gate itself is not here: it needs the hardware
(docs/WISPOS-OS-DESIGN.md, bench tests).
"""
import contextlib, errno, importlib.machinery, io, importlib.util, itertools, os, shutil, socket, subprocess, sys, tempfile, threading, time, unittest, unittest.mock
from pathlib import Path

sys.dont_write_bytecode = True                          # importing the script must not leave a __pycache__ next to it
HERE = Path(__file__).resolve().parent
SCRIPT = HERE / 'rootfs/usr/bin/wisp-usb'
WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))
COUNTER = itertools.count()


def load(root, **env):
    """A fresh copy of the module with its constants read from this fake root (they are read at import)."""
    env = {'WISP_USB_ROOT': str(root), 'WISP_SYSTEMCTL': 'systemctl-test', 'WISP_USB_IP': 'ip-test', 'WISP_USB_UDHCPD': 'udhcpd-test', 'WISP_USB_RECHECK': '60', **env}
    with unittest.mock.patch.dict(os.environ, env):
        loader = importlib.machinery.SourceFileLoader('wisp_usb_%d' % next(COUNTER), str(SCRIPT))
        mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader)); loader.exec_module(mod)
    return mod


def configfs_rmdir(path):
    """What configfs does: a directory goes with its attribute files and its default groups, but not while a user item (a string set, a configuration, a function) or a link is inside."""
    path = Path(path)
    for p in path.rglob('*'):
        if p.is_symlink(): raise OSError(errno.ENOTEMPTY, 'a link is still inside', str(p))
        if p.is_dir() and p.parent.name in ('strings', 'configs', 'functions'): raise OSError(errno.ENOTEMPTY, 'an item is still inside', str(p))
    shutil.rmtree(path)


class Runner:
    """Stands in for subprocess.run: records the command, fails the ones named."""
    def __init__(self, fail=()): self.calls, self.fail = [], set(fail)

    def __call__(self, cmd):
        self.calls.append(list(cmd))
        bad = (cmd[0], cmd[1]) in self.fail
        return subprocess.CompletedProcess(cmd, 1 if bad else 0, '', 'boom' if bad else '')


class Base(unittest.TestCase):
    def fake(self, gate=True, udc=True, battery='80', usb_online='0', usb_present='0', machine_id='0123456789abcdef0123456789abcdef', **env):
        root = Path(tempfile.mkdtemp(prefix='wisp-usb-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, root, True)
        def put(rel, text='', mode='w'):
            p = root / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text)
        if gate: put('sys/bus/platform/drivers/sun4i-usb-phy/5100400.phy/host_enable', '0\n')
        if udc: (root / 'sys/class/udc/musb-hdrc.5.auto').mkdir(parents=True)
        if battery is not None:
            put('sys/class/power_supply/axp20x-battery/type', 'Battery\n'); put('sys/class/power_supply/axp20x-battery/capacity', battery + '\n')
        if usb_online is not None:
            put('sys/class/power_supply/axp20x-usb/type', 'USB\n'); put('sys/class/power_supply/axp20x-usb/online', usb_online + '\n')
            if usb_present is not None: put('sys/class/power_supply/axp20x-usb/present', usb_present + '\n')
        put('sys/class/regulator/regulator.5/name', 'usb0-vbus\n'); put('sys/class/regulator/regulator.5/state', 'disabled\n')
        (root / 'sys/class/net/usb0').mkdir(parents=True); (root / 'sys/kernel/config/usb_gadget').mkdir(parents=True)
        put('etc/machine-id', machine_id + '\n')
        self.root = root
        self.m = load(root, **env)
        self.runner = Runner(); self.m.run = self.runner; self.m.rmdir = configfs_rmdir
        self.m.time = unittest.mock.Mock(sleep=lambda s: None, monotonic=time.monotonic)           # the interface wait must not slow the tests
        return self.m

    def gate(self): return (self.root / 'sys/bus/platform/drivers/sun4i-usb-phy/5100400.phy/host_enable').read_text().strip()
    def set_supply(self, name, attr, value): (self.root / 'sys/class/power_supply' / name / attr).write_text(value + '\n')
    def commands(self): return [' '.join(c) for c in self.runner.calls]


class DecisionTable(Base):
    """The operator's rules as a pure function: no files, no commands."""
    def setUp(self): self.m = load('/nonexistent')

    def facts(self, **kw): return self.m.Facts(**{'gate': 'g', 'udc': 'u', 'external': False, 'battery': 80, **kw})

    def test_host_is_allowed_only_without_external_power_and_above_the_floor(self):
        d = self.m.decide
        self.assertEqual(d('host', self.facts()), ('do', None))
        self.assertEqual(d('host', self.facts(external=True)), ('refuse', 'external'))
        self.assertEqual(d('host', self.facts(battery=14)), ('refuse', 'battery'))
        self.assertEqual(d('host', self.facts(battery=15)), ('do', None))             # the floor itself is allowed
        self.assertEqual(d('host', self.facts(external=True, battery=3)), ('refuse', 'external'))        # external power is the first thing said

    def test_what_cannot_be_read_is_never_assumed_safe(self):
        d = self.m.decide
        self.assertEqual(d('host', self.facts(external=None)), ('refuse', 'unknown-external'))
        self.assertEqual(d('host', self.facts(battery=None)), ('refuse', 'unknown-battery'))

    def test_without_the_kernel_gate_host_mode_is_not_offered_at_all(self):
        self.assertEqual(self.m.decide('host', self.facts(gate=None)), ('unavailable', 'no-gate'))
        self.assertEqual(self.m.decide('host', self.facts(gate=None, external=True)), ('unavailable', 'no-gate'))      # not even the power question matters

    def test_a_release_without_a_bench_result_does_not_offer_host_mode_whatever_else_is_true(self):
        d = self.m.decide
        self.assertEqual(d('host', self.facts(released=False)), ('unavailable', 'not-released'))
        self.assertEqual(d('host', self.facts(released=False, gate=None)), ('unavailable', 'not-released'))           # before the other questions
        self.assertEqual(d('computer', self.facts(released=False)), ('do', None)); self.assertEqual(d('off', self.facts(released=False)), ('do', None))      # only host mode is switched off
        self.assertEqual(d('host', self.facts()), ('do', None))                                                          # the default Facts are released: nothing changes without the marker

    def test_computer_needs_a_controller_and_off_needs_nothing(self):
        d = self.m.decide
        self.assertEqual(d('computer', self.facts()), ('do', None))
        self.assertEqual(d('computer', self.facts(udc=None)), ('unavailable', 'no-udc'))
        self.assertEqual(d('computer', self.facts(external=True, battery=1)), ('do', None))           # a computer's port is a source, not a load
        self.assertEqual(d('off', self.facts(gate=None, udc=None, external=None, battery=None)), ('do', None))

    def test_every_reason_has_words_for_the_menu_and_the_ones_that_can_end_host_mode_have_words_for_that(self):
        for key, (refusal, ended) in self.m.REASONS.items():
            self.assertTrue(refusal)
            if key in ('external', 'battery', 'unknown-external', 'unknown-battery'): self.assertTrue(ended, key)
        f = self.facts(battery=9)
        self.assertEqual(self.m.text('battery', f), 'The battery is at 9%: host mode needs at least 15%.')
        self.assertEqual(self.m.text('battery', f, ended=True), 'Host mode ended: the battery fell to 9% (the floor is 15%).')


class ReadingFacts(Base):
    def test_a_complete_system_reads_as_it_is(self):
        m = self.fake(battery='64', usb_online='1')
        f = m.read_facts()
        self.assertTrue(f.gate.endswith('5100400.phy/host_enable')); self.assertEqual(f.udc, 'musb-hdrc.5.auto')
        self.assertEqual((f.host_on, f.computer_on, f.external, f.battery), (False, False, True, 64))

    def test_external_power_is_online_or_present_on_any_supply_that_is_not_the_battery(self):
        m = self.fake(usb_online='0', usb_present='1'); self.assertTrue(m.read_facts().external)
        m = self.fake(usb_online='0', usb_present='0'); self.assertFalse(m.read_facts().external)
        m = self.fake(usb_online='1', usb_present=None); self.assertTrue(m.read_facts().external)

    def test_no_readable_supply_means_unknown_not_none(self):
        self.assertIsNone(self.fake(usb_online=None).read_facts().external)
        m = self.fake(); (self.root / 'sys/class/power_supply/axp20x-usb/online').write_text('garbage\n'); (self.root / 'sys/class/power_supply/axp20x-usb/present').unlink()
        self.assertIsNone(m.read_facts().external)

    def test_a_missing_battery_reads_as_none(self):
        self.assertIsNone(self.fake(battery=None).read_facts().battery)

    def test_a_kernel_without_the_gate_or_a_controller_reads_as_none(self):
        f = self.fake(gate=False, udc=False).read_facts(); self.assertIsNone(f.gate); self.assertIsNone(f.udc)


class Commands(Base):
    def run_cmd(self, m, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = m.main(['wisp-usb', *args])
        return code, out.getvalue(), err.getvalue()

    # ------------------------------------------------------------------------------------------------------------------------------- host
    def test_host_writes_the_gate_and_starts_the_watcher(self):
        m = self.fake(); code, out, err = self.run_cmd(m, 'host')
        self.assertEqual(code, 0, err); self.assertEqual(self.gate(), '1')
        self.assertIn('systemctl-test start wisp-usb-watch.service', self.commands())
        self.assertIn('usb: host', out)

    def test_host_in_an_image_with_the_disabled_marker_is_unavailable_and_never_touches_the_gate(self):
        m = self.fake(); (self.root / 'usr/share/wispos').mkdir(parents=True); (self.root / 'usr/share/wispos/usb-host-disabled').write_text('off\n')
        code, out, err = self.run_cmd(m, 'host'); self.assertEqual(code, 4); self.assertIn('Host mode is not part of this release.', err)
        self.assertEqual(self.gate(), '0'); self.assertEqual(self.commands(), [])
        code, out, err = self.run_cmd(m, 'status'); self.assertIn('host: unavailable: Host mode is not part of this release.', out); self.assertIn('usb: off', out)
        self.assertEqual(self.run_cmd(m, 'computer')[0], 0)                                                              # computer mode is unaffected

    def test_host_with_external_power_is_refused_and_changes_nothing(self):
        m = self.fake(usb_online='1'); code, out, err = self.run_cmd(m, 'host')
        self.assertEqual(code, 3); self.assertIn('Unplug the charger first', err)
        self.assertEqual(self.gate(), '0'); self.assertEqual(self.commands(), [])

    def test_host_below_the_floor_is_refused_and_the_floor_can_be_moved(self):
        m = self.fake(battery='14'); code, _, err = self.run_cmd(m, 'host')
        self.assertEqual(code, 3); self.assertIn('14%', err); self.assertEqual(self.gate(), '0')
        m = self.fake(battery='14', WISP_USB_BATTERY_FLOOR='10'); self.assertEqual(self.run_cmd(m, 'host')[0], 0)

    def test_host_without_the_gate_is_unavailable_and_exits_4(self):
        m = self.fake(gate=False); code, _, err = self.run_cmd(m, 'host')
        self.assertEqual(code, 4); self.assertIn('cannot keep the host role off', err); self.assertEqual(self.commands(), [])

    def test_host_when_the_supply_cannot_be_read_is_refused(self):
        m = self.fake(usb_online=None); code, _, err = self.run_cmd(m, 'host')
        self.assertEqual(code, 3); self.assertIn('Cannot tell whether external power', err); self.assertEqual(self.gate(), '0')

    def test_host_without_a_working_watcher_does_not_stay_on(self):
        m = self.fake(); self.runner.fail.add(('systemctl-test', 'start')); code, _, err = self.run_cmd(m, 'host')
        self.assertEqual(code, 1); self.assertIn('watcher did not start', err); self.assertEqual(self.gate(), '0')

    def test_host_takes_a_running_gadget_down_first(self):
        m = self.fake(); self.assertEqual(self.run_cmd(m, 'computer')[0], 0)
        order = []
        real_down, real_write = m.gadget_down, m.write
        m.gadget_down = lambda: (order.append('gadget-down'), real_down())[1]
        m.write = lambda p, v: (order.append('gate=' + str(v).strip()) if str(p).endswith('host_enable') else None, real_write(p, v))[1]
        self.assertEqual(self.run_cmd(m, 'host')[0], 0)
        self.assertEqual(order, ['gadget-down', 'gate=1']); self.assertFalse((self.root / 'sys/kernel/config/usb_gadget/wisp').exists())

    # ------------------------------------------------------------------------------------------------------------------------------- off
    def test_off_from_host_closes_the_gate_and_stops_the_watcher(self):
        m = self.fake(); self.run_cmd(m, 'host'); self.runner.calls.clear()
        code, out, _ = self.run_cmd(m, 'off')
        self.assertEqual(code, 0); self.assertEqual(self.gate(), '0'); self.assertIn('systemctl-test stop wisp-usb-watch.service', self.commands()); self.assertIn('usb: off', out)

    def test_off_with_nothing_on_is_quiet_and_needs_no_commands(self):
        m = self.fake(gate=False, udc=False); code, out, _ = self.run_cmd(m, 'off')
        self.assertEqual(code, 0); self.assertEqual(self.commands(), []); self.assertIn('usb: off', out)

    # ------------------------------------------------------------------------------------------------------------------------------- computer
    def test_computer_builds_the_ncm_gadget_binds_it_and_gives_the_pc_one_address(self):
        m = self.fake(); code, out, err = self.run_cmd(m, 'computer')
        self.assertEqual(code, 0, err); g = self.root / 'sys/kernel/config/usb_gadget/wisp'
        rd = lambda rel: (g / rel).read_text().strip()
        self.assertEqual((rd('idVendor'), rd('idProduct'), rd('bDeviceClass')), ('0x1d6b', '0x0104', '0x02'))
        self.assertEqual((rd('strings/0x409/manufacturer'), rd('strings/0x409/serialnumber')), ('WispOS', '0123456789ab'))
        self.assertEqual(rd('UDC'), 'musb-hdrc.5.auto')
        self.assertEqual(rd('functions/ncm.usb0/dev_addr'), '82:57:49:53:50:01'); self.assertEqual(rd('functions/ncm.usb0/host_addr'), '12:23:45:67:89:ab')
        self.assertEqual(os.readlink(g / 'configs/c.1/ncm.usb0'), str(g / 'functions/ncm.usb0'))
        self.assertEqual(rd('functions/ncm.usb0/os_desc/interface.ncm/compatible_id'), 'WINNCM'); self.assertTrue((g / 'os_desc/c.1').is_symlink())
        self.assertEqual(self.commands()[:2], ['ip-test address add 169.254.170.2/16 dev usb0', 'ip-test link set usb0 up'])
        conf = (self.root / 'run/wisp-usb/udhcpd.conf').read_text()
        for line in ('interface usb0', 'start 169.254.170.1', 'end 169.254.170.1', 'max_leases 1', 'opt subnet 255.255.0.0'): self.assertIn(line, conf.splitlines())
        self.assertIn('udhcpd-test -S %s/run/wisp-usb/udhcpd.conf' % self.root, self.commands())
        self.assertIn('usb: computer', out); self.assertIn('address: 169.254.170.2', out)

    def test_computer_finds_the_interface_by_its_mac_when_udev_renamed_it(self):
        m = self.fake(); net = self.root / 'sys/class/net'; shutil.rmtree(net / 'usb0')
        (net / 'enx825749535001').mkdir(); (net / 'enx825749535001/address').write_text(m.DEV_MAC.upper() + '\n')          # the MAC is compared without regard to case
        (net / 'eth0').mkdir(); (net / 'eth0/address').write_text('52:54:00:12:34:56\n')
        code, _, err = self.run_cmd(m, 'computer')
        self.assertEqual(code, 0, err); self.assertEqual(self.commands()[:2], ['ip-test address add 169.254.170.2/16 dev enx825749535001', 'ip-test link set enx825749535001 up'])
        self.assertIn('interface enx825749535001', (self.root / 'run/wisp-usb/udhcpd.conf').read_text().splitlines())

    def test_computer_says_when_ssh_is_still_off(self):
        flag = self.root_flag = Path(tempfile.mkdtemp(prefix='wisp-usb-flag-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, flag, True)
        m = self.fake(WISP_SSH_FLAG=str(flag / 'ssh-enabled')); out = self.run_cmd(m, 'computer')[1]
        self.assertIn('wisp-ssh on', out)
        (flag / 'ssh-enabled').write_text(''); m2 = load(self.root, WISP_SSH_FLAG=str(flag / 'ssh-enabled'))
        self.assertNotIn('wisp-ssh on', self.run_cmd(m2, 'status')[1])

    def test_computer_twice_does_not_build_it_twice(self):
        m = self.fake(); self.run_cmd(m, 'computer'); self.runner.calls.clear()
        self.assertEqual(self.run_cmd(m, 'computer')[0], 0); self.assertEqual(self.commands(), [])

    def test_computer_leaves_host_mode_first(self):
        m = self.fake(); self.run_cmd(m, 'host'); self.runner.calls.clear()
        self.assertEqual(self.run_cmd(m, 'computer')[0], 0)
        self.assertEqual(self.gate(), '0'); self.assertIn('systemctl-test stop wisp-usb-watch.service', self.commands())

    def test_computer_without_a_controller_is_unavailable(self):
        m = self.fake(udc=False); code, _, err = self.run_cmd(m, 'computer')
        self.assertEqual(code, 4); self.assertIn('no USB device controller', err)

    def test_off_removes_the_whole_gadget_and_the_lease_state(self):
        m = self.fake(); self.run_cmd(m, 'computer')
        self.assertTrue((self.root / 'run/wisp-usb/udhcpd.conf').exists())
        self.assertEqual(self.run_cmd(m, 'off')[0], 0)
        self.assertFalse((self.root / 'sys/kernel/config/usb_gadget/wisp').exists()); self.assertFalse((self.root / 'run/wisp-usb/udhcpd.conf').exists())

    def test_the_dhcp_server_is_stopped_only_when_the_pid_really_is_udhcpd(self):
        m = self.fake(); self.run_cmd(m, 'computer')
        ours = subprocess.Popen(['udhcpd', '60'], executable='/bin/sleep')                    # argv[0] says udhcpd, so /proc/PID/cmdline does too
        other = subprocess.Popen(['sleep', '60'])
        for p in (ours, other): self.addCleanup(p.wait); self.addCleanup(p.kill)
        for p, expect_stopped in ((other, False), (ours, True)):
            (self.root / 'run/wisp-usb/udhcpd.pid').write_text('%d\n' % p.pid)
            self.assertEqual(self.run_cmd(m, 'off')[0], 0)
            time.sleep(0.2)
            self.assertEqual(p.poll() is not None, expect_stopped, 'pid of %s' % p.args)
            self.run_cmd(m, 'computer')

    def test_a_failure_while_building_takes_back_everything_it_did(self):
        for failing in (('ip-test', 'address'), ('ip-test', 'link'), ('udhcpd-test', '-S')):
            with self.subTest(failing=failing):
                m = self.fake(); self.runner.fail.add(failing); code, _, err = self.run_cmd(m, 'computer')
                self.assertEqual(code, 1); self.assertIn('failed', err)
                self.assertFalse((self.root / 'sys/kernel/config/usb_gadget/wisp').exists())

    def test_a_controller_that_does_not_take_the_gadget_is_a_failure_with_nothing_left(self):
        m = self.fake(); real_write = m.write
        m.write = lambda p, v: real_write(p, '' if str(p).endswith('/UDC') else v)               # the bind silently does nothing
        code, _, err = self.run_cmd(m, 'computer')
        self.assertEqual(code, 1); self.assertIn('did not accept the gadget', err); self.assertFalse((self.root / 'sys/kernel/config/usb_gadget/wisp').exists())

    # ------------------------------------------------------------------------------------------------------------------------------- status / usage
    def test_status_names_the_state_and_what_the_others_need(self):
        m = self.fake(usb_online='1'); out = self.run_cmd(m, 'status')[1]
        self.assertEqual(out.splitlines()[:3], ['usb: off', 'host: unavailable: Unplug the charger first: host mode runs on the battery and never charges.', 'computer: available'])

    def test_status_in_host_mode_shows_the_5_v_output_and_the_battery(self):
        m = self.fake(); self.run_cmd(m, 'host'); (self.root / 'sys/class/regulator/regulator.5/state').write_text('enabled\n')
        out = self.run_cmd(m, 'status')[1]
        self.assertIn('usb: host', out); self.assertIn('host: on', out); self.assertIn('5v-output: enabled', out); self.assertIn('battery: 80%', out)

    def test_status_shows_the_note_that_ended_a_mode_until_the_next_change_clears_it(self):
        m = self.fake(); m.note('Host mode ended: external power appeared.')
        self.assertIn('note: Host mode ended: external power appeared.', self.run_cmd(m, 'status')[1])
        self.run_cmd(m, 'off'); self.assertNotIn('note:', self.run_cmd(m, 'status')[1])

    def test_usage_errors_exit_2(self):
        m = self.fake()
        for args in ((), ('bogus',), ('host', 'now'), ('status', 'x')): self.assertEqual(self.run_cmd(m, *args)[0], 2, args)

    def test_it_runs_as_a_program_with_the_fake_root(self):
        self.fake(); r = subprocess.run([sys.executable, '-B', str(SCRIPT), 'status'], capture_output=True, text=True, env={**os.environ, 'WISP_USB_ROOT': str(self.root)})
        self.assertEqual(r.returncode, 0, r.stderr); self.assertTrue(r.stdout.startswith('usb: off\n'))


class Watcher(Base):
    """host mode ends by itself, on events, without polling for the charger."""
    def start(self, m):
        a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM); self.addCleanup(a.close); self.addCleanup(b.close)
        result = []; t = threading.Thread(target=lambda: result.append(m.watch(a)), daemon=True); t.start()
        return b, t, result

    def host_on(self, m):
        with contextlib.redirect_stdout(io.StringIO()): self.assertEqual(m.main(['wisp-usb', 'host']), 0)

    UEVENT = b'change@/devices/platform/soc/axp20x-usb\0ACTION=change\0SUBSYSTEM=power_supply\0POWER_SUPPLY_NAME=axp20x-usb\0'

    def test_it_returns_at_once_when_host_mode_is_not_on(self):
        m = self.fake(); sock, t, result = self.start(m); t.join(2)
        self.assertFalse(t.is_alive()); self.assertEqual(result, [0])

    def test_external_power_appearing_ends_host_mode_and_says_why(self):
        m = self.fake(); self.host_on(m); sock, t, result = self.start(m); time.sleep(0.2)
        self.assertTrue(t.is_alive()); self.assertEqual(self.gate(), '1')
        self.set_supply('axp20x-usb', 'online', '1'); sock.send(self.UEVENT); t.join(3)
        self.assertFalse(t.is_alive()); self.assertEqual(self.gate(), '0')
        self.assertIn('Host mode ended: external power appeared.', (self.root / 'run/wisp-usb/note').read_text())

    def test_other_uevents_do_not_make_it_look(self):
        m = self.fake(); self.host_on(m); sock, t, result = self.start(m); time.sleep(0.2)
        self.set_supply('axp20x-usb', 'online', '1'); sock.send(b'add@/block/mmcblk0\0ACTION=add\0SUBSYSTEM=block\0'); time.sleep(0.4)
        self.assertTrue(t.is_alive()); self.assertEqual(self.gate(), '1')                       # the charger is there but nothing told it to look
        sock.send(self.UEVENT); t.join(3); self.assertEqual(self.gate(), '0')

    def test_the_battery_falling_below_the_floor_is_caught_by_the_slow_look(self):
        m = self.fake(WISP_USB_RECHECK='0.2'); self.host_on(m); sock, t, result = self.start(m); time.sleep(0.1)
        self.set_supply('axp20x-battery', 'capacity', '14'); t.join(3)
        self.assertFalse(t.is_alive()); self.assertEqual(self.gate(), '0')
        self.assertIn('fell to 14%', (self.root / 'run/wisp-usb/note').read_text())

    def test_a_busy_uevent_stream_does_not_postpone_the_slow_look(self):
        m = self.fake(WISP_USB_RECHECK='0.3'); self.host_on(m); sock, t, result = self.start(m)
        self.set_supply('axp20x-battery', 'capacity', '5'); end = time.time() + 3
        while t.is_alive() and time.time() < end:
            sock.send(b'change@/x\0SUBSYSTEM=hid\0'); time.sleep(0.05)                          # never a power-supply event, never a quiet moment
        self.assertFalse(t.is_alive()); self.assertEqual(self.gate(), '0')

    def test_it_ends_quietly_when_someone_else_turned_host_mode_off(self):
        m = self.fake(); self.host_on(m); sock, t, result = self.start(m); time.sleep(0.2)
        (self.root / 'sys/bus/platform/drivers/sun4i-usb-phy/5100400.phy/host_enable').write_text('0\n'); sock.send(self.UEVENT); t.join(3)
        self.assertFalse(t.is_alive()); self.assertEqual(result, [0]); self.assertFalse((self.root / 'run/wisp-usb/note').exists())

    def test_a_supply_that_stops_being_readable_ends_host_mode(self):
        m = self.fake(); self.host_on(m); sock, t, result = self.start(m); time.sleep(0.2)
        for a in ('online', 'present'): (self.root / 'sys/class/power_supply/axp20x-usb' / a).unlink()
        sock.send(self.UEVENT); t.join(3); self.assertEqual(self.gate(), '0'); self.assertIn('cannot tell', (self.root / 'run/wisp-usb/note').read_text())


if __name__ == '__main__':
    unittest.main()
