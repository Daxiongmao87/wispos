"""Development-build tooling: the developer-machine listener, and the guarantee that none of it reaches a release image."""
import json, os, re, subprocess, sys, tempfile, threading, unittest, urllib.error, urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'dev')); sys.path.insert(0, str(HERE))
import listen, stage_rootfs


class ListenerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='wispos-listen-', dir=os.environ.get('TMPDIR'))
        self.state = listen.State(self.dir)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), listen.make_handler(self.state))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self): self.server.shutdown(); self.server.server_close()

    def post(self, body, path='/hello', raw=None):
        req = urllib.request.Request(self.base + path, data=raw if raw is not None else json.dumps(body).encode(), method='POST', headers={'Content-Type': 'application/json'})
        try:
            return urllib.request.urlopen(req, timeout=5).status
        except urllib.error.HTTPError as e:
            return e.code

    def test_only_private_and_loopback_addresses_are_accepted(self):
        for ok in ('10.0.2.15', '192.168.0.112', '172.16.5.4', '127.0.0.1', '::1', 'fd00::1'): self.assertTrue(listen.allowed(ok), ok)
        for bad in ('8.8.8.8', '1.1.1.1', '203.0.113.9', '2001:4860:4860::8888'): self.assertFalse(listen.allowed(bad), bad)

    def test_a_hello_is_recorded_and_the_latest_per_device_is_kept(self):
        hello = {'hostname': 'WispOS', 'dev': 'wlan0', 'ip': '192.168.0.112', 'mac': 'aa:bb:cc:dd:ee:ff', 'kernel': '7.2.0', 'uptime': 42, 'state': 'running', 'failed': '', 'ssh_user': 'root'}
        self.assertEqual(self.post(hello), 204)
        self.post(dict(hello, ip='192.168.0.120', uptime=100))                     # same device, new address
        devices = json.loads((Path(self.dir) / 'devices.json').read_text())
        self.assertEqual(list(devices), ['aa:bb:cc:dd:ee:ff']); self.assertEqual(devices['aa:bb:cc:dd:ee:ff']['ip'], '192.168.0.120')
        self.assertEqual(len((Path(self.dir) / 'devices.jsonl').read_text().splitlines()), 2)
        with urllib.request.urlopen(self.base + '/devices', timeout=5) as r: self.assertIn('192.168.0.120', r.read().decode())

    def test_bad_input_is_refused_and_unknown_fields_are_dropped(self):
        self.assertEqual(self.post(None, raw=b'not json'), 400)
        self.assertEqual(self.post(None, raw=b'[1,2]'), 400)
        self.assertEqual(self.post(None, raw=b'x' * 5000), 413)
        self.assertEqual(self.post({'ip': '1.2.3.4'}, path='/other'), 404)
        self.post({'ip': '10.0.0.9', 'mac': 'm', 'evil': '<script>', 'uptime': 'abc'})
        entry = json.loads((Path(self.dir) / 'devices.json').read_text())['m']
        self.assertNotIn('evil', entry); self.assertEqual(entry['uptime'], 0)


class DevGatingTests(unittest.TestCase):
    def test_the_dev_overlay_adds_the_announcer_and_nothing_adds_it_by_default(self):
        tree = Path(tempfile.mkdtemp(prefix='wispos-devtree-', dir=os.environ.get('TMPDIR')))
        stage_rootfs.install_dev(tree)
        self.assertTrue(os.access(tree / 'usr/bin/wisp-dev-announce', os.X_OK))
        self.assertTrue((tree / 'usr/lib/wispos/DEV-BUILD').exists())
        self.assertTrue((tree / 'usr/lib/systemd/system/multi-user.target.wants/wisp-dev-announce.service').is_symlink())
        source = (HERE / 'stage_rootfs.py').read_text()
        self.assertIn("if os.environ.get('WISP_DEV') == '1':\n        install_dev(tree)", source)      # the only call, behind the flag
        self.assertEqual(source.count('install_dev(tree)'), 2)                                      # the call and the def line
        self.assertFalse(list((HERE / 'rootfs').rglob('*dev-announce*')))

    def test_dev_images_carry_the_recorder_watchdog_panel_test_and_diagnostic_tools(self):
        tree = Path(tempfile.mkdtemp(prefix='wispos-devtree-', dir=os.environ.get('TMPDIR')))
        stage_rootfs.install_dev(tree)
        system = tree / 'usr/lib/systemd/system'
        for unit in ('wisp-dev-blackbox', 'wisp-dev-watchdog', 'wisp-dev-paneltest'):
            self.assertTrue((system / f'{unit}.service').is_file(), unit)
            self.assertEqual(os.readlink(system / 'multi-user.target.wants' / f'{unit}.service'), f'../{unit}.service')
        for script in ('wisp-dev-blackbox', 'wisp-dev-watchdog'):
            self.assertTrue(os.access(tree / 'usr/bin' / script, os.X_OK), script)
            subprocess.run(['sh', '-n', str(tree / 'usr/bin' / script)], check=True)
        # every program an ExecStart runs exists in the staged tree: our own script (first token, or the .py after /usr/bin/python3) - python itself comes from the base image
        for unit in ('wisp-dev-blackbox', 'wisp-dev-watchdog', 'wisp-dev-paneltest'):
            line = [l for l in (system / f'{unit}.service').read_text().splitlines() if l.startswith('ExecStart=')][0]
            tokens = line.split('=', 1)[1].split()
            ours = [t for t in tokens if t.startswith('/usr/bin/wisp-dev-') or t.startswith('/usr/lib/wispos-dev/')]
            self.assertEqual(len(ours), 1, (unit, tokens)); self.assertTrue((tree / ours[0].lstrip('/')).is_file(), (unit, ours[0]))
        diag = tree / 'usr/lib/wispos-dev/diag'
        for tool in ('paneltest.py', 'kmsflip.py', 'gsstub.py', 'applyhz.py', 'kill-game.sh', 'gamelaunch.sh', 'replay_app.py'): self.assertTrue((diag / tool).is_file(), tool)
        self.assertEqual(os.readlink(tree / 'usr/lib/wispos-dev/kill-game.sh'), 'diag/kill-game.sh')
        rules = (tree / 'usr/lib/tmpfiles.d/wisp-dev.conf').read_text().splitlines()
        self.assertIn('d /storage/wispos/dev 0755 root root -', rules)                                  # a real, writable directory: the tools write their logs there
        self.assertNotIn('L /storage/wispos/dev - - - - /usr/lib/wispos-dev', rules)                    # not a link into the read-only image (that was a bug: tools could not write)
        for name, target in (('diag', 'diag'), ('kill-game.sh', 'diag/kill-game.sh'), ('gamelaunch.sh', 'diag/gamelaunch.sh'), ('replay_app.py', 'diag/replay_app.py')):
            self.assertIn(f'L /storage/wispos/dev/{name} - - - - /usr/lib/wispos-dev/{target}', rules)

    def test_nothing_of_the_dev_tools_is_in_the_release_overlay(self):
        # release overlay (rootfs/) must have none of the dev names; check_rootfs refuses a release tree that contains any dev file
        self.assertFalse([p for p in (HERE / 'rootfs').rglob('*') if 'wisp-dev' in p.name or 'wispos-dev' in str(p)])
        checker = (HERE / 'check_rootfs.py').read_text()
        for name in ('wisp-dev-watchdog', 'wisp-dev-blackbox.service', 'wisp-dev-paneltest.service', 'wisp-dev.conf', 'wispos-dev/diag/paneltest.py'): self.assertIn(name, checker)

    def test_dev_inputs_are_refused_without_the_dev_flag(self):
        for var in ('WISP_SSH_KEY', 'WISP_DEV_ANNOUNCE', 'WISP_WIFI_CONF'):
            out = subprocess.run(['bash', str(HERE / 'build-image.sh'), '/mnt/hdd/wispos-staging/wispos-os-20261007'], capture_output=True, text=True,
                                 env={**os.environ, var: '/dev/null', 'WISP_DEV': ''})
            self.assertEqual(out.returncode, 2, var + ': ' + out.stderr)
            self.assertIn('needs WISP_DEV=1', out.stderr)

    def test_dev_build_writes_to_its_own_system_and_work_tree(self):
        text = (HERE / 'build-rootfs.sh').read_text()
        self.assertIn('variants/}SYSTEM${TAG:+-$TAG}', text)
        self.assertIn('rootfs-dev', text)
        self.assertIn('DEFAULT_SYSTEM=$OUT/variants/SYSTEM-dev', (HERE / 'build-image.sh').read_text())

    def test_announcer_sends_no_secrets_and_only_to_the_configured_target(self):
        text = (HERE / 'rootfs-dev/usr/bin/wisp-dev-announce').read_text()
        self.assertIn('/flash/wispos/dev.conf', text)
        for forbidden in ('shadow', 'authorized_keys', 'psk', 'wifi.conf', 'password'):
            self.assertNotIn(forbidden, text)
        subprocess.run(['sh', '-n', str(HERE / 'rootfs-dev/usr/bin/wisp-dev-announce')], check=True)


def load_pad():
    import importlib.machinery, importlib.util
    path = str(HERE / 'rootfs-dev/usr/bin/wisp-dev-pad')
    spec = importlib.util.spec_from_loader('wisp_dev_pad', importlib.machinery.SourceFileLoader('wisp_dev_pad', path))
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


class VirtualPadTests(unittest.TestCase):
    """wisp-dev-pad's pure logic (the uinput part is exercised in the emulator by os/setup/flow-test.sh)."""
    def setUp(self): self.pad = load_pad()

    def test_kernel_capability_bitmaps_are_read_most_significant_word_first(self):
        self.assertEqual(self.pad.bits_from_sysfs('0 5'), {0, 2})
        self.assertEqual(self.pad.bits_from_sysfs('3 0'), {64, 65})
        self.assertEqual(self.pad.bits_from_sysfs('0'), set())

    def test_button_numbers_follow_sdls_order_codes_from_btn_joystick_up_then_the_lower_ones(self):
        self.assertEqual(self.pad.sdl_button_order({0x13b, 0x130, 0x103, 0x131, 0x13a}), [0x130, 0x131, 0x13a, 0x13b, 0x103])

    def test_logical_names_come_from_the_pads_own_database_entry(self):
        db = ['# comment', '19000000010000000100000000010000,Test Pad,a:b0,b:b1,x:b2,y:b3,back:b4,start:b5,dpup:h0.1,dpdown:h0.4,dpleft:h0.8,dpright:h0.2,platform:Linux,']
        keys = {0x130, 0x131, 0x133, 0x134, 0x13a, 0x13b}
        mapping = self.pad.logical_map('test pad', keys, db)
        self.assertEqual(mapping['a'], ('key', 0x130)); self.assertEqual(mapping['y'], ('key', 0x134))
        self.assertEqual(mapping['select'], ('key', 0x13a)); self.assertEqual(mapping['start'], ('key', 0x13b))
        self.assertEqual(mapping['up'], ('hat', 0x11, -1)); self.assertEqual(mapping['right'], ('hat', 0x10, 1))
        self.assertIsNone(self.pad.logical_map('Another Pad', keys, db))                      # no entry: no guessing
        self.assertNotIn('l', mapping)                                                       # buttons the entry does not name stay unmapped

    def test_dpad_wired_as_buttons_in_the_database_maps_to_those_buttons(self):
        # key codes {0x130, 0x103, 0x104}: SDL numbers 0x130 first (>= BTN_JOYSTICK), then the lower ones ascending: b0=0x130, b1=0x103, b2=0x104
        db = ['0,Btn Pad,a:b0,dpup:b1,dpdown:b2,platform:Linux,']
        mapping = self.pad.logical_map('Btn Pad', {0x130, 0x103, 0x104}, db)
        self.assertEqual((mapping['a'], mapping['up'], mapping['down']), (('key', 0x130), ('key', 0x103), ('key', 0x104)))

    def test_it_never_types_text(self):
        text = (HERE / 'rootfs-dev/usr/bin/wisp-dev-pad').read_text()
        self.assertNotIn('KEY_A', text); self.assertIn("never types", text)


class PadGatingTests(unittest.TestCase):
    def test_the_pad_is_in_the_dev_overlay_only(self):
        self.assertTrue(os.access(HERE / 'rootfs-dev/usr/bin/wisp-dev-pad', os.X_OK))
        self.assertFalse(list((HERE / 'rootfs').rglob('*dev-pad*')))
        self.assertIn("'usr/bin/wisp-dev-pad'", (HERE / 'check_rootfs.py').read_text())


class WriteNothingTests(unittest.TestCase):
    """Operator, 2026-10-11: the card is written as little as possible, in dev too. The dev recorder and watchdog log to the journal (RAM); the host keeps the stream."""

    def test_the_recorder_and_the_watchdog_write_nothing_on_the_card(self):
        for name in ('wisp-dev-blackbox', 'wisp-dev-watchdog'):
            text = (HERE / 'rootfs-dev/usr/bin' / name).read_text()
            self.assertNotIn('/storage', text, name)
            self.assertNotRegex(text, r'>>\s*"?\$', name)                                   # no append to a file variable either
        unit = (HERE / 'rootfs-dev/usr/lib/systemd/system/wisp-dev-blackbox.service').read_text()
        self.assertNotIn('/storage', unit.split('[Unit]')[1])

    def test_the_recorder_asks_sway_rarely(self):                                           # sway's IPC leaks every reply (notes, "MEMORY FLOOR")
        text = (HERE / 'rootfs-dev/usr/bin/wisp-dev-blackbox').read_text()
        self.assertEqual(text.count('swaymsg'), 1)
        self.assertIn('n % 30', text)

    def test_the_recorder_stays_cheap(self):                                              # a recorder that costs CPU distorts every measurement it sits next to
        text = (HERE / 'rootfs-dev/usr/bin/wisp-dev-blackbox').read_text()
        self.assertNotRegex(text, r'(?m)^[^#]*\\btop\\b')                                     # no `top`
        self.assertNotIn('/proc/[0-9]', text)                                                 # no per-process scan
        self.assertNotIn('RUNNER', text)                                                      # the stale game-specific check
        self.assertEqual(len(re.findall(r'(?m)^\s+awk ', text)), 1)                                   # one awk per round (comments do not count)


class MemStreamTests(unittest.TestCase):
    """mem-stream.py against a fake /proc and /sys: the fields, the since-last-line deltas, the Chromium roles, and survival of odd processes."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        spec = importlib.util.spec_from_file_location('mem_stream', HERE / 'dev/diag/mem-stream.py')
        cls.ms = importlib.util.module_from_spec(spec); spec.loader.exec_module(cls.ms)

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='wispos-memstream-', dir=os.environ.get('TMPDIR')))
        self.proc, self.sys = self.root / 'proc', self.root / 'sys'
        (self.proc / 'pressure').mkdir(parents=True); (self.sys / 'block/zram0').mkdir(parents=True)
        (self.proc / 'meminfo').write_text('MemTotal: 999424 kB\nMemAvailable: 307200 kB\nShmem: 57344 kB\nSwapTotal: 2097152 kB\nSwapFree: 1572864 kB\n')
        (self.proc / 'uptime').write_text('1000.00 900.00\n')
        (self.proc / 'pressure/memory').write_text('some avg10=1.50 avg60=0.10 avg300=0.00 total=1\nfull avg10=0.25 avg60=0.00 avg300=0.00 total=1\n')
        (self.sys / 'block/zram0/mm_stat').write_text('524288000 157286400 180355072 471859200 472059904 376 0 0 0\n')
        self.set_sectors(4096)
        hz = os.sysconf('SC_CLK_TCK')
        self.add(100, 'steamwebhelper', 1, 40960, 20480, ['steamwebhelper', '-nocrashdialog'], 1000 * hz - 50 * hz, io_kib=512)
        self.add(101, 'steamwebhelper', 100, 153600, 204800, ['steamwebhelper', '--type=zygote', '--no-sandbox'], 1000 * hz - 40 * hz)
        self.add(102, 'steamwebhelper', 100, 2048, 1024, ['steamwebhelper', '--type=utility', '--utility-sub-type=network.mojom.NetworkService'], 1000 * hz - 30 * hz)
        self.add(103, 'steam', 1, 27648, 58368, ['steam', '-gamepadui'], 1000 * hz - 60 * hz)
        self.add(104, 'odd) (name', 1, 10240, 0, ['odd'], 1000 * hz - 5 * hz)
        (self.proc / '105').mkdir()                                                          # a pid that vanished half-way: no files
        (self.proc / '106').mkdir(); (self.proc / '106/status').write_text('Name:\tkworker\nPPid:\t2\n')   # kernel thread: no RssAnon

    def set_sectors(self, n):
        (self.proc / 'diskstats').write_text('179 0 mmcblk0 100 0 2000 50 300 0 %d 70 0 0 0\n179 32 mmcblk1 5 0 40 1 6 0 0 1 0 0 0\n' % n)

    def add(self, pid, name, ppid, anon_kb, swap_kb, args, start_ticks, io_kib=0):
        d = self.proc / str(pid); d.mkdir()
        (d / 'status').write_text('Name:\t%s\nPPid:\t%d\nRssAnon:\t%d kB\nVmSwap:\t%d kB\n' % (name, ppid, anon_kb, swap_kb))
        (d / 'cmdline').write_bytes(b'\0'.join(a.encode() for a in args) + b'\0')
        (d / 'stat').write_text('%d (%s) S %d 1 1 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 %d 0 0\n' % (pid, name, ppid, start_ticks))
        (d / 'io').write_text('rchar: 0\nwchar: 0\nsyscr: 0\nsyscw: 0\nread_bytes: 0\nwrite_bytes: %d\ncancelled_write_bytes: 0\n' % (io_kib * 1024))

    def test_one_line_has_the_memory_numbers_and_every_steam_process(self):
        rec, _ = self.ms.sample(str(self.proc), str(self.sys))
        self.assertEqual((rec['avail'], rec['shmem'], rec['swap_used']), (300, 56, 512))
        self.assertEqual((rec['zram_orig'], rec['zram_used'], rec['psi_some'], rec['psi_full']), (500, 172, 1.5, 0.25))
        roles = {p['pid']: p['kind'] for p in rec['procs']}
        self.assertEqual(roles, {100: 'browser', 101: 'zygote', 102: 'utility:NetworkService', 103: ''})
        self.assertEqual(rec['steam_mb'], (40960 + 20480 + 153600 + 204800 + 2048 + 1024 + 27648 + 58368) >> 10)
        big = rec['procs'][0]; self.assertEqual((big['pid'], big['anon_mb'], big['swap_mb'], big['ppid'], big['age_s']), (101, 150, 200, 100, 40))
        self.assertEqual(rec['others'], [['odd) (name', 1, 10, 0]])                          # a name with ")" and spaces did not break the stat parse; the kernel thread is not listed
        json.dumps(rec)

    def test_since_the_last_line_deltas_and_the_writers(self):
        _, state = self.ms.sample(str(self.proc), str(self.sys))
        rec, _ = self.ms.sample(str(self.proc), str(self.sys), state)
        self.assertEqual((rec['sd_wr_kib'], rec['wr']), (0, []))                              # nothing written between the two lines
        self.set_sectors(4096 + 2048)                                                         # 1024 KiB to the boot card
        (self.proc / '100/io').write_text('write_bytes: %d\n' % ((512 + 300) * 1024))        # the browser process wrote 300 KiB
        rec, _ = self.ms.sample(str(self.proc), str(self.sys), state)
        self.assertEqual(rec['sd_wr_kib'], 1024); self.assertEqual(rec['sd2_wr_kib'], 0)
        self.assertEqual(rec['wr'], [['steamwebhelper', 100, 300]])
        first, _ = self.ms.sample(str(self.proc), str(self.sys))
        self.assertEqual((first['sd_wr_kib'], first['wr']), (0, []))                          # the first line has no previous to subtract

    def test_a_kernel_without_pressure_or_zram_still_gives_a_line(self):
        (self.proc / 'pressure/memory').unlink(); (self.sys / 'block/zram0/mm_stat').unlink()
        rec, _ = self.ms.sample(str(self.proc), str(self.sys))
        self.assertEqual((rec['psi_some'], rec['psi_full'], rec['zram_orig']), (None, None, 0))

    def test_roles_of_chromium_processes(self):
        k = self.ms.kind_of
        self.assertEqual(k('steamwebhelper', ['steamwebhelper']), 'browser')
        self.assertEqual(k('steamwebhelper', ['x', '--type=gpu-process']), 'gpu-process')
        self.assertEqual(k('steamwebhelper', ['x', '--type=utility', '--utility-sub-type=audio.mojom.AudioService']), 'utility:AudioService')
        self.assertEqual(k('steam', ['steam']), '')


class LogStreamTests(unittest.TestCase):
    SCRIPT = HERE / 'dev/log-stream.sh'

    def run_stream(self, *args, env=None, timeout=60):
        return subprocess.run(['bash', str(self.SCRIPT), *args], capture_output=True, text=True, timeout=timeout, env={**os.environ, **(env or {})})

    def test_a_directory_off_the_hdd_is_refused(self):
        out = self.run_stream('--host', '127.0.0.1', '--out', '/tmp/wispos-not-the-hdd', '--for', '5s')
        self.assertEqual(out.returncode, 2); self.assertIn('HDD', out.stderr)
        self.assertFalse(os.path.exists('/tmp/wispos-not-the-hdd'))

    def test_bad_arguments_are_refused(self):
        for args in (['--for', 'abc'], ['--for', '0'], ['--period', 'x'], ['--host', 'a b'], ['--nope']):
            out = self.run_stream('--host', '127.0.0.1', *args)
            self.assertEqual(out.returncode, 2, args)

    def test_it_streams_reconnects_resumes_the_journal_and_pipes_the_sampler(self):
        tmp = Path(tempfile.mkdtemp(prefix='wispos-logstream-', dir=os.environ.get('TMPDIR')))
        fake = tmp / 'fakessh'
        fake.write_text('''#!/bin/sh
for last; do :; done                                   # the remote command is the last argument
echo "$last" >> "$FAKE_LOG"
case $last in
    journalctl*) echo "2026-10-11T00:00:00+00:00 dev unit: a journal line"; exit 255;;
    python3*)    cat > "$FAKE_STDIN"; echo '{"t":1,"avail":9}'; exit 255;;
esac
'''); fake.chmod(0o755)
        out = self.run_stream('--host', '127.0.0.1', '--for', '6s', '--period', '3', '--out', str(tmp / 'out'),
                              env={'WISP_SSH': str(fake), 'WISP_HDD': str(tmp), 'WISP_STREAM_RETRY': '1', 'FAKE_LOG': str(tmp / 'cmds'), 'FAKE_STDIN': str(tmp / 'stdin')})
        self.assertEqual(out.returncode, 0, out.stderr)
        journal = (tmp / 'out/journal-127.0.0.1.log').read_text()
        self.assertIn('dev unit: a journal line', journal)
        self.assertGreaterEqual(journal.count('connect (journal)'), 3)                        # connect markers, one per attempt (the word "disconnected" has its own)
        self.assertIn('disconnected (journal) rc=255', journal)
        cmds = (tmp / 'cmds').read_text().splitlines()
        journal_cmds = [c for c in cmds if c.startswith('journalctl')]
        self.assertIn('-n 40', journal_cmds[0]); self.assertIn('--since @', journal_cmds[1])    # first start shows context, a reconnect asks for what it missed
        sampler = [json.loads(l) for l in (tmp / 'out/mem-127.0.0.1.jsonl').read_text().splitlines()]
        self.assertTrue(any(e.get('avail') == 9 for e in sampler)); self.assertTrue(any(str(e.get('event', '')).startswith('disconnected (sampler) rc=255') for e in sampler))
        self.assertIn('def sample(', (tmp / 'stdin').read_text())                              # the sampler reaches the device through the pipe, not through a copy on the card
        self.assertTrue(any(c.startswith('python3 -u - 3 ') for c in cmds))


class PerfStreamTests(unittest.TestCase):
    """perf-stream.py and perf-summary.py against a fake /proc and /sys."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        for name, file in (('perf_stream', 'dev/diag/perf-stream.py'), ('perf_summary', 'dev/perf-summary.py')):
            spec = importlib.util.spec_from_file_location(name, HERE / file); mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); setattr(cls, name, mod)

    def stat_line(self, pid, comm, ppid, ut, st, nt=1):
        return '%d (%s) S %d 1 1 0 -1 0 0 0 0 0 %d %d 0 0 20 0 %d 0 1000 0 0\n' % (pid, comm, ppid, ut, st, nt)

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='wispos-perfstream-', dir=os.environ.get('TMPDIR')))
        self.proc, self.sys = self.root / 'proc', self.root / 'sys'
        self.proc.mkdir(); (self.sys / 'class/devfreq/1800000.gpu').mkdir(parents=True); (self.sys / 'class/thermal/thermal_zone0').mkdir(parents=True)
        for c in (0, 1): (self.sys / ('devices/system/cpu/cpu%d/cpufreq' % c)).mkdir(parents=True); (self.sys / ('devices/system/cpu/cpu%d/cpufreq/scaling_cur_freq' % c)).write_text('1416000\n')
        (self.sys / 'class/devfreq/1800000.gpu/cur_freq').write_text('420000000\n'); (self.sys / 'class/thermal/thermal_zone0/temp').write_text('45500\n')
        (self.proc / 'loadavg').write_text('1.50 1.00 0.50 1/100 999\n'); (self.proc / 'meminfo').write_text('MemAvailable: 307200 kB\n')
        (self.proc / 'vmstat').write_text('pswpin 100\npswpout 200\n')
        (self.proc / 'pressure').mkdir()
        (self.proc / 'pressure/memory').write_text('some avg10=2.00 avg60=0 avg300=0 total=0\nfull avg10=0.50 avg60=0 avg300=0 total=0\n')
        (self.proc / 'pressure/cpu').write_text('some avg10=30.00 avg60=0 avg300=0 total=0\n')
        self.cpu(0, (100, 0, 20, 100, 0, 0, 0, 0), (100, 0, 10, 300, 0, 0, 0, 0))
        self.proc_files(50, 'reaper', 1, 0, 0, cmd=['reaper', 'SteamLaunch', 'AppId=2200', '--'])
        self.proc_files(51, 'quake3.exe', 50, 100, 10, nt=4, threads={51: ('quake3.exe', 100, 10), 53: ('wine_dsound', 20, 5)})
        self.proc_files(52, 'wineserver', 51, 40, 5)
        self.proc_files(40, 'steam', 1, 10, 5)
        self.proc_files(41, 'steamwebhelper', 40, 10, 0)

    def cpu(self, n, a, b):
        self.cpu_state = (a, b)
        self.write_cpu(a)

    def write_cpu(self, rows):
        # two cores; each tuple is user nice system idle iowait irq softirq steal
        self.rows = rows
        (self.proc / 'stat').write_text('cpu  0 0 0 0 0 0 0 0\ncpu0 %s\ncpu1 %s\n' % (' '.join(map(str, self.cpu_state[0])), ' '.join(map(str, self.cpu_state[1]))))

    def proc_files(self, pid, comm, ppid, ut, st, nt=1, cmd=None, threads=None):
        d = self.proc / str(pid); d.mkdir(exist_ok=True)
        (d / 'stat').write_text(self.stat_line(pid, comm, ppid, ut, st, nt)); (d / 'cmdline').write_bytes(b'\0'.join(c.encode() for c in (cmd or [comm])) + b'\0')
        for tid, (tcomm, tut, tst) in (threads or {pid: (comm, ut, st)}).items():
            t = d / 'task' / str(tid); t.mkdir(parents=True, exist_ok=True); (t / 'stat').write_text(self.stat_line(tid, tcomm, ppid, tut, tst, 1))

    def test_stat_parsing_survives_odd_command_names(self):
        self.assertEqual(self.perf_stream.parse_stat('7 (a) (b c) S 1 1 1 0 -1 0 0 0 0 0 11 22 0 0 20 0 3 0 1000 0 0'), (7, 'a) (b c', 1, 11, 22, 3))

    def test_a_second_of_a_game_attributed_to_the_game_the_ui_and_the_cores(self):
        ps = self.perf_stream
        before = ps.snapshot(str(self.proc), str(self.sys)); before['threads'] = {}
        self.cpu_state = ((160, 0, 40, 120, 0, 0, 0, 0), (110, 0, 12, 378, 0, 0, 0, 0)); self.write_cpu(self.cpu_state[0])      # core 0: +60 user +20 system +20 idle
        self.proc_files(51, 'quake3.exe', 50, 150, 20, nt=4, threads={51: ('quake3.exe', 150, 20), 53: ('wine_dsound', 30, 10)})   # +60 user ticks in all
        self.proc_files(52, 'wineserver', 51, 60, 5)
        self.proc_files(41, 'steamwebhelper', 40, 14, 0)
        cur = ps.snapshot(str(self.proc), str(self.sys)); cur['t'] = before['t'] + 1.0
        rec = ps.line(before, cur, str(self.proc), str(self.sys))
        self.assertEqual(rec['game']['procs'], 2)                                        # the game and wineserver; the launcher is not counted
        self.assertEqual((rec['game']['user'], rec['game']['sys'], rec['game']['cpu']), (70, 10, 80))
        self.assertEqual(rec['ui']['cpu'], 4)
        self.assertIsNone(rec['target'])                                                 # the first line has no thread baseline yet
        self.proc_files(51, 'quake3.exe', 50, 190, 25, nt=4, threads={51: ('quake3.exe', 190, 25), 53: ('wine_dsound', 40, 12)})    # next second: main thread +40 user, mixer +10
        cur2 = ps.snapshot(str(self.proc), str(self.sys)); cur2['t'] = cur['t'] + 1.0
        rec2 = ps.line(cur, cur2, str(self.proc), str(self.sys))
        self.assertEqual(rec2['target'], [51, 51, 'quake3.exe'])                         # the game thread with the most CPU is the one to watch next
        self.assertEqual(rec2['thr'][0][:4], ['quake3.exe', 51, 'quake3.exe', 45])
        self.assertEqual(rec['cpu'][0], [80, 60, 20]); self.assertEqual(rec['freq'], [1416, 1416]); self.assertEqual(rec['gpu_mhz'], 420)
        self.assertEqual((rec['psi_mem_some'], rec['psi_mem_full'], rec['psi_cpu'], rec['avail_mb']), (2.0, 0.5, 30.0, 300))
        json.dumps(rec)

    def test_wait_samples_say_running_or_what_a_sleeping_thread_waits_in(self):
        ps = self.perf_stream
        t = self.proc / '51/task/51'
        def thread(state, stack=None, wchan=None):
            (t / 'stat').write_text('51 (quake3.exe) %s 50 1 1 0 -1 0 0 0 0 0 1 1 0 0 20 0 1 0 1000 0 0\n' % state)
            for name, text in (('stack', stack), ('wchan', wchan)):
                f = t / name
                if text is None: f.unlink(missing_ok=True)
                else: f.write_text(text)
        thread('R', stack='[<0>] whatever\n')
        self.assertEqual(ps.wait_key(str(self.proc), 51, 51), 'R')
        thread('S', stack='[<0>] __futex_wait+0xc8/0x1bc\n[<0>] futex_wait+0x88/0x110\n[<0>] do_futex+0x1a0/0x1b0\n[<0>] __arm64_sys_futex+0x84/0x14c\n')
        self.assertEqual(ps.wait_key(str(self.proc), 51, 51), 'S:futex')
        thread('D', stack='[<0>] dma_fence_default_wait+0x1c0/0x230\n[<0>] __arm64_sys_ioctl+0xac/0xf0\n')
        self.assertEqual(ps.wait_key(str(self.proc), 51, 51), 'D:ioctl')
        thread('S', stack=None, wchan='do_epoll_wait')                                  # no stack file: the wait channel
        self.assertEqual(ps.wait_key(str(self.proc), 51, 51), 'S:do_epoll_wait')
        thread('S', stack=None, wchan='0')
        self.assertEqual(ps.wait_key(str(self.proc), 51, 51), 'S:?')
        self.assertIsNone(ps.wait_key(str(self.proc), 51, 999))                       # a thread that is gone

    def test_summary_numbers(self):
        rows = [{'t': 100.0 + i, 'cpu': [[90, 80, 10], [20, 15, 5]], 'freq': [1416, 1008], 'gpu_mhz': 420, 'temp_c': 50.0, 'psi_cpu': 10.0, 'psi_mem_full': 0.0, 'avail_mb': 300, 'swin': 0, 'swout': 4,
                 'game': {'cpu': 100 if i % 2 else 60, 'user': 90, 'sys': 10}, 'ui': {'cpu': 3}, 'thr': [['quake3.exe', 5, 'quake3.exe', 70, 4]], 'wait': {'R': 17, 'S:futex': 3}} for i in range(10)]
        rows.append({**rows[0], 't': 120.0, 'game': {'cpu': 1, 'user': 1, 'sys': 0}})                    # an idle second is not in the window
        ps = self.perf_summary
        s = ps.summarize(rows, ps.window(rows))
        self.assertEqual(s['wait'][0][0], 'R'); self.assertEqual(round(s['wait'][0][1]), 85)
        self.assertEqual(s['seconds'], 8); self.assertEqual(round(s['game_cpu']), 80);                  # the steady window is trimmed by an eighth each side
        self.assertEqual(s['gpu_mhz'], {420: 100}); self.assertEqual(s['threads'][0][0], 'quake3.exe (quake3.exe)')
        csv = self.root / 'stat.csv'
        csv.write_text('1.0,1000,,cycles,1,100\n1.0,500,,instructions,1,100\n1.0,10,,branch-misses,1,100\n2.0,1000,,cycles,1,100\n2.0,500,,instructions,1,100\n9.0,5,,cycles,1,100\n')
        c = ps.stat_ipc(str(csv), 0.5, 2.5)
        self.assertEqual((c['cycles'], c['instructions']), (2000, 1000))


class PerfSetTests(unittest.TestCase):
    """perf-set.sh builds the Proton user_settings.py the device will receive; the ssh program is faked to capture it."""
    SCRIPT = HERE / 'dev/perf-set.sh'

    def run_set(self, *args):
        tmp = Path(tempfile.mkdtemp(prefix='wispos-perfset-', dir=os.environ.get('TMPDIR')))
        fake = tmp / 'fakessh'
        fake.write_text('#!/bin/sh\nfor last; do :; done\necho "$last" > "$FAKE_CMD"\ncat > "$FAKE_STDIN"\n'); fake.chmod(0o755)
        out = subprocess.run(['bash', str(self.SCRIPT), '127.0.0.1', *args], capture_output=True, text=True, env={**os.environ, 'WISP_SSH': str(fake), 'FAKE_CMD': str(tmp / 'cmd'), 'FAKE_STDIN': str(tmp / 'stdin')})
        return out, tmp

    def test_env_becomes_a_valid_python_dict_in_one_b64_argument(self):
        out, tmp = self.run_set('env', 'FEX_X87REDUCEDPRECISION=1', 'GALLIUM_HUD=fps,frametime')
        self.assertEqual(out.returncode, 0, out.stderr)
        cmd = (tmp / 'cmd').read_text()
        import base64
        b64 = cmd.split('B64=')[1].split()[0]
        ns = {}; exec(base64.b64decode(b64).decode(), ns)                                       # it is valid Python
        self.assertEqual(ns['user_settings'], {'FEX_X87REDUCEDPRECISION': '1', 'GALLIUM_HUD': 'fps,frametime'})
        self.assertIn('CMD=env', cmd); self.assertIn('REVERT_S=3600', cmd)                       # and it always arms the revert timer (the remote script does; the default is passed)
        self.assertIn('wisp-perf-revert', (tmp / 'stdin').read_text())

    def test_the_ui_reclaim_lever_runs_the_daemon_from_ram_stops_with_the_restore_script_and_warns_about_old_helpers(self):
        out, tmp = self.run_set('ui-reclaim-on')
        self.assertEqual(out.returncode, 0, out.stderr)
        remote = (tmp / 'stdin').read_text()
        self.assertIn('/run/wisp-dev/wisp-ui-reclaim', remote); self.assertIn('systemd-run --quiet --unit=wisp-dev-reclaim', remote)           # RAM binary, transient unit: nothing on the card
        start = remote.index('cat > $RESTORE <<EOF2'); restore = remote[start:remote.index('\nEOF2', start)]
        self.assertIn('systemctl stop wisp-dev-reclaim', restore)                       # `default` and the revert timer both stop it (SIGTERM lifts the ceiling)
        self.assertIn('WARNING: only', remote); self.assertIn('wisp-ui cgroup', remote)   # helpers charged only if the daemon saw them young: say so when it did not
        self.assertIn('RFREEZE=', (tmp / 'cmd').read_text()); self.assertNotIn('RFREEZE=--freeze', (tmp / 'cmd').read_text())
        out, tmp = self.run_set('ui-reclaim-on', '--freeze'); self.assertIn('RFREEZE=--freeze', (tmp / 'cmd').read_text())
        out, tmp = self.run_set('ui-reclaim-on', '--rm-rf'); self.assertEqual(out.returncode, 2); self.assertFalse((tmp / 'cmd').exists())     # only --freeze is accepted

    def test_fex_options_become_a_json_config_and_never_a_per_game_file(self):
        out, tmp = self.run_set('fex', 'TSOEnabled=0', 'X87ReducedPrecision=1')
        self.assertEqual(out.returncode, 0, out.stderr)
        cmd = (tmp / 'cmd').read_text()
        import base64
        cfg = json.loads(base64.b64decode(cmd.split('JB64=')[1].split()[0]))
        self.assertEqual(cfg, {'Config': {'TSOEnabled': '0', 'X87ReducedPrecision': '1'}, 'ThunksDB': {}})
        remote = (tmp / 'stdin').read_text()
        self.assertIn('FEX_APP_CONFIG', remote)                                           # the launcher's own hook, in the tool's global user_settings.py
        self.assertNotIn('compatdata', remote)                                            # not a per-game (per-prefix) file

    def test_bad_input_is_refused_before_ssh(self):
        for args in (['fex'], ['fex', 'Bad Name=1'], ['fex', 'A=b c'], ['fex', 'A=\"x'], ['env'], ['env', 'NOEQUALS'], ['env', 'A B=1'], ['env', 'K=a"b'], ['env', 'K=a\\b'], ['bogus']):
            out, tmp = self.run_set(*args)
            self.assertEqual(out.returncode, 2, args); self.assertFalse((tmp / 'cmd').exists(), args)

    def test_the_remote_script_is_valid_shell_and_freezes_only_web_helpers(self):
        out, tmp = self.run_set('ui-freeze')
        self.assertEqual(out.returncode, 0, out.stderr)
        remote = (tmp / 'stdin').read_text()
        subprocess.run(['sh', '-n', str(tmp / 'stdin')], check=True)                          # the whole remote script (with its nested here-document) parses
        self.assertIn('pidof steamwebhelper', remote); self.assertIn('kill -STOP', remote); self.assertIn('kill -CONT', remote)
        self.assertNotIn('pidof steam)', remote)                                              # never the `steam` client itself

    def test_governor_commands_send_no_environment(self):
        out, tmp = self.run_set('cpu-performance')
        self.assertEqual(out.returncode, 0); self.assertIn('B64= ', (tmp / 'cmd').read_text() + ' ')


class SteamArgsTests(unittest.TestCase):
    """steam-args.sh refuses anything but plain switches before it ever opens a connection."""
    SCRIPT = HERE / 'dev/steam-args.sh'

    def run_args(self, *args):
        tmp = Path(tempfile.mkdtemp(prefix='wispos-steamargs-', dir=os.environ.get('TMPDIR')))
        fake = tmp / 'fakessh'; fake.write_text('#!/bin/sh\nfor last; do :; done\necho "$last" > "$FAKE_CMD"\ncat > "$FAKE_STDIN"\n'); fake.chmod(0o755)
        out = subprocess.run(['bash', str(self.SCRIPT), '127.0.0.1', *args], capture_output=True, text=True, env={**os.environ, 'WISP_SSH': str(fake), 'FAKE_CMD': str(tmp / 'cmd'), 'FAKE_STDIN': str(tmp / 'stdin')})
        return out, tmp

    def test_plain_switches_pass_and_reach_the_remote_script(self):
        out, tmp = self.run_args('set', '-steamos3 -steamdeck -cef-disable-gpu')
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("ARGS='-steamos3 -steamdeck -cef-disable-gpu'", (tmp / 'cmd').read_text())
        remote = (tmp / 'stdin').read_text()
        self.assertIn('mount --bind', remote); self.assertIn('wisp-steam-watch-off', remote)        # RAM copy bind-mounted, watchdog paused during the restart

    def test_anything_else_is_refused_before_ssh(self):
        for args in (['set'], ['set', '-a; reboot'], ['set', '$(id)'], ['set', "-a'b"], ['set', '-a`id`'], ['bogus']):
            out, tmp = self.run_args(*args)
            self.assertEqual(out.returncode, 2, args); self.assertFalse((tmp / 'cmd').exists(), args)


class BusCaptureTests(unittest.TestCase):
    """bus-capture.sh keeps everything in RAM on the device and refuses a root-disk destination; bus-summary.py reads busctl's capture format."""
    SCRIPT = HERE / 'dev/bus-capture.sh'
    SUMMARY = HERE / 'dev/diag/bus-summary.py'
    SAMPLE = ('''Monitoring bus message stream.
\u2023 Type=method_call  Endian=l  Flags=0  Version=1 Cookie=39  Timestamp="x"
  Sender=:1.168  Destination=com.steampowered.Atomupd1  Path=/com/steampowered/Atomupd1  Interface=com.steampowered.Atomupd1  Member=DisableHttpProxy
  MESSAGE "" {
  };

\u2023 Type=error  Endian=l  Flags=1  Version=1 Cookie=31  ReplyCookie=39  Timestamp="x"
  Sender=org.freedesktop.DBus  Destination=:1.168
  ErrorName=org.freedesktop.DBus.Error.ServiceUnknown  ErrorMessage="The name com.steampowered.Atomupd1 was not provided by any .service files"
  MESSAGE "s" {
          STRING "The name com.steampowered.Atomupd1 was not provided by any .service files";
  };

\u2023 Type=method_call  Endian=l  Flags=0  Version=1 Cookie=67  Timestamp="x"
  Sender=:1.6  Destination=org.freedesktop.DBus  Path=/org/freedesktop/DBus  Interface=org.freedesktop.DBus  Member=StartServiceByName
  MESSAGE "su" {
          STRING "com.steampowered.SteamOSManager1";
          UINT32 0;
  };

\u2023 Type=method_call  Endian=l  Flags=0  Version=1 Cookie=70  Timestamp="x"
  Sender=:1.6  Destination=org.freedesktop.UPower  Path=/org/freedesktop/UPower  Interface=org.freedesktop.UPower  Member=GetDisplayDevice
''')

    def fake(self):
        tmp = Path(tempfile.mkdtemp(prefix='wispos-buscap-', dir=os.environ.get('TMPDIR')))
        fake = tmp / 'fakessh'; fake.write_text('#!/bin/sh\nfor last; do :; done\necho "$last" >> "$FAKE_CMD"\ncat >> "$FAKE_STDIN"\n'); fake.chmod(0o755)
        return tmp, {**os.environ, 'WISP_SSH': str(fake), 'FAKE_CMD': str(tmp / 'cmd'), 'FAKE_STDIN': str(tmp / 'stdin')}

    def test_start_records_to_ram_only_and_stop_refuses_a_root_disk_destination(self):
        tmp, env = self.fake()
        out = subprocess.run(['bash', str(self.SCRIPT), '127.0.0.1', 'start'], capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 0, out.stderr)
        remote = (tmp / 'stdin').read_text()
        self.assertIn('busctl --system monitor', remote); self.assertIn('D=/run/wisp-dev', remote); self.assertIn('DBUS_SESSION_BUS_ADDRESS', remote)
        self.assertNotIn('/storage', remote); self.assertNotIn('/flash', remote)                  # nothing is written to the card
        for args in (['stop'], ['stop', '/tmp/x'], ['stop', '/home/agent/x'], ['bogus'], []):
            r = subprocess.run(['bash', str(self.SCRIPT), '127.0.0.1', *args], capture_output=True, text=True, env=env)
            self.assertEqual(r.returncode, 2, args)

    def test_summary_lists_calls_starts_and_what_was_missing(self):
        tmp = Path(tempfile.mkdtemp(prefix='wispos-buscap-', dir=os.environ.get('TMPDIR'))); cap = tmp / 'cap.txt'; cap.write_text(self.SAMPLE)
        out = subprocess.run(['python3', str(self.SUMMARY), str(cap)], capture_output=True, text=True).stdout
        self.assertIn('com.steampowered.Atomupd1 DisableHttpProxy', out); self.assertIn('start        com.steampowered.SteamOSManager1', out)
        self.assertIn('was not provided by any .service files', out); self.assertNotIn('UPower', out)             # only SteamOS names are of interest
        cap.write_text('Monitoring bus message stream.\n')
        self.assertIn('no SteamOS-related traffic', subprocess.run(['python3', str(self.SUMMARY), str(cap)], capture_output=True, text=True).stdout)


if __name__ == '__main__':
    unittest.main()
