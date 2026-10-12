"""Tests for os/steamos/wisp-update (docs/WISPOS-UPDATE-DESIGN.md): the helper behind the update interface.

It runs for real against a LOCAL HTTP server (Range requests, fault injection, throttling) with a throwaway ed25519 key and the host's openssl, on a fake /flash directory on
the HDD work tree, so nothing on this machine is touched and no network is used. What is proved: only a manifest signed by the image's key is believed; only newer builds for
this device (and not ones the card sent back) are offered; staging goes into the slot that is NOT running, is resumable, verified and cancel-safe; the commit is two steps in a fixed
order (the trial marker, then one byte of extlinux.conf) and a power cut after any step leaves a card that boots and an update that can be finished; nothing is staged while one is
on trial; confirm ends a trial; /flash goes read-write only while it is written and back to read-only after, also on failure. BootContract runs the real initramfs step
(os/boot/wisp-boot.sh) on what this helper wrote: the two halves agree on the format, and a version that never confirms is sent back and not offered again.
"""
import base64, hashlib, http.server, json, os, re, shutil, signal, subprocess, sys, tempfile, threading, time, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
HELPER = Path(os.environ.get('WISP_UPDATE_PATH', HERE / 'steamos/wisp-update'))      # the override is how the mutant check below runs the tests against a changed copy
WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))
KERNEL, SYSTEM = os.urandom(300_000), os.urandom(700_000)          # stand-ins: the helper never looks inside the files, only at sizes and hashes
OLD_KERNEL, OLD_SYSTEM = os.urandom(200_000), os.urandom(500_000)  # what the running slot holds
EXTLINUX = 'DEFAULT %s\nLABEL a\n  LINUX /KERNEL\n  APPEND quiet wisp.slot=a\nLABEL b\n  LINUX /wispos/b/KERNEL\n  APPEND quiet wisp.slot=b\n'


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        srv = self.server; srv.log.append((self.path, self.headers.get('Range')))
        body = srv.files.get(self.path)
        if body is None: self.send_error(404); return
        start, status = 0, 200
        m = re.match(r'bytes=(\d+)-', self.headers.get('Range') or '')
        if m and srv.honor_range: start, status = int(m.group(1)), 206
        data = body[start:]
        self.send_response(status); self.send_header('Content-Length', str(len(data)))
        if status == 206: self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, len(body) - 1, len(body)))
        self.end_headers()
        cut = srv.drop.pop(self.path, None)                          # fault: send only this many bytes, then hang up
        try:
            if cut is not None and cut < len(data): self.wfile.write(data[:cut]); return
            for i in range(0, len(data), 65536):
                self.wfile.write(data[i:i + 65536])
                if srv.throttle: time.sleep(srv.throttle)
        except (BrokenPipeError, ConnectionResetError): pass          # the client hung up (a cancel): not an error of the server


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True


class Base(unittest.TestCase):
    def setUp(self):
        if not str(WORK).startswith('/mnt/hdd/'): self.skipTest('the work directory must be on the HDD')
        (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
        self.d = Path(tempfile.mkdtemp(prefix='wispos-upd-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, self.d, True)
        self.flash = self.d / 'flash'; (self.flash / 'extlinux').mkdir(parents=True)
        (self.flash / 'extlinux/extlinux.conf').write_text(EXTLINUX % 'a'); (self.flash / 'KERNEL').write_bytes(OLD_KERNEL); (self.flash / 'SYSTEM').write_bytes(OLD_SYSTEM)       # the card as flashed: slot a
        (self.d / 'cmdline').write_text('boot=LABEL=ROCKNIX quiet wisp.slot=a disk=LABEL=STORAGE\n')
        self.run_openssl('genpkey', '-algorithm', 'ED25519', '-out', str(self.d / 'key.pem'))
        self.run_openssl('pkey', '-in', str(self.d / 'key.pem'), '-pubout', '-out', str(self.d / 'pub.pem'))
        self.srv = Server(('127.0.0.1', 0), Handler); self.srv.files, self.srv.log, self.srv.drop, self.srv.throttle, self.srv.honor_range = {}, [], {}, 0, True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start(); self.addCleanup(self.srv.server_close); self.addCleanup(self.srv.shutdown)
        self.base = 'http://127.0.0.1:%d' % self.srv.server_address[1]
        (self.d / 'os-release').write_text('BUILD_ID="20261011.1"\nWISPOS_VERSION="0.1.0"\nHW_DEVICE="H700"\n')
        (self.d / 'update.conf').write_text(json.dumps({'manifest_url': self.base + '/manifest.json'}))
        self.env = {**os.environ, 'WISP_UPDATE_CONF': str(self.d / 'update.conf'), 'WISP_UPDATE_PUBKEY': str(self.d / 'pub.pem'), 'WISP_OS_RELEASE': str(self.d / 'os-release'),
                    'WISP_FLASH_DIR': str(self.flash), 'WISP_CMDLINE': str(self.d / 'cmdline'), 'WISP_UPDATE_NO_REMOUNT': '1', 'WISP_UPDATE_ALLOW_HTTP': '1', 'WISP_UPDATE_NET_TIMEOUT': '5', 'WISP_UPDATE_RETRIES': '2',
                    'WISP_UPDATE_NO_BACKOFF': '1', 'WISP_UPDATE_FREE_MARGIN': '0', 'TMPDIR': str(WORK / 'tmp')}
        self.publish(); self.card0 = self.snapshot()

    def snapshot(self):
        """Every file on the fake card: relative path -> bytes."""
        return {str(p.relative_to(self.flash)): p.read_bytes() for p in sorted(self.flash.rglob('*')) if p.is_file()}

    def set_slot(self, run, default=None):
        """Make the card look as if slot `run` were running (and U-Boot started `default`, default the same)."""
        (self.d / 'cmdline').write_text('boot=LABEL=ROCKNIX wisp.slot=%s\n' % run)
        (self.flash / 'extlinux/extlinux.conf').write_text(EXTLINUX % (default or run))

    def set_pending(self, slot='b', build_id='20261012.1', version='0.1.1', tries=0):
        (self.flash / 'wispos/update').mkdir(parents=True, exist_ok=True)
        (self.flash / 'wispos/update/PENDING').write_text('slot=%s\nbuild_id=%s\nversion=%s\ntries=%s\n' % (slot, build_id, version, tries))

    def run_openssl(self, *args, data=None):
        r = subprocess.run(['openssl', *args], capture_output=True, input=data); self.assertEqual(r.returncode, 0, r.stderr); return r.stdout

    def manifest(self, **over):
        m = {'format': 1, 'variant': 'stable', 'build_id': '20261012.1', 'version': '0.1.1', 'requires': '', 'update_type': 0, 'hw_device': 'H700',
             'files': {'KERNEL': {'url': self.base + '/KERNEL', 'size': len(KERNEL), 'sha256': hashlib.sha256(KERNEL).hexdigest()},
                       'SYSTEM': {'url': self.base + '/SYSTEM', 'size': len(SYSTEM), 'sha256': hashlib.sha256(SYSTEM).hexdigest()}}}
        m.update(over); return m

    def publish(self, manifest=None, key='key.pem', raw=None, sign=True):
        raw = raw if raw is not None else json.dumps(manifest or self.manifest()).encode()
        self.srv.files['/manifest.json'] = raw
        if sign:
            (self.d / 'm.bin').write_bytes(raw)
            sig = self.run_openssl('pkeyutl', '-sign', '-rawin', '-in', str(self.d / 'm.bin'), '-inkey', str(self.d / key))
            self.srv.files['/manifest.json.sig'] = base64.b64encode(sig)
        else: self.srv.files.pop('/manifest.json.sig', None)
        self.srv.files['/KERNEL'], self.srv.files['/SYSTEM'] = KERNEL, SYSTEM

    def helper(self, *args, env=None, timeout=60):
        r = subprocess.run(['python3', str(HELPER), *args], capture_output=True, text=True, env={**self.env, **(env or {})}, timeout=timeout)
        return r.returncode, [l.split() for l in r.stdout.splitlines()], r.stdout, r.stderr

    def error_of(self, out): return [l for l in out.splitlines() if l.startswith('error ')]

    @property
    def stage(self): return self.flash / 'wispos/b'                       # where slot b's files go; the tests start with slot a running

    @property
    def default(self): return (self.flash / 'extlinux/extlinux.conf').read_text().splitlines()[0]

    @property
    def pending(self):
        p = self.flash / 'wispos/update/PENDING'
        return dict(l.split('=', 1) for l in p.read_text().splitlines()) if p.exists() else None


class CheckTests(Base):
    def test_a_newer_signed_build_is_offered_with_its_size_and_nothing_is_written(self):
        rc, lines, out, err = self.helper('check')
        self.assertEqual((rc, lines), (0, [['update', '20261012.1', '0.1.1', 'stable', str(len(KERNEL) + len(SYSTEM)), '0']]), err)
        self.assertEqual(self.snapshot(), self.card0)                                       # a check never writes to the card

    def test_a_major_update_says_so(self):
        self.publish(self.manifest(update_type=1))
        self.assertEqual(self.helper('check')[1][0][-1], '1')

    def test_an_update_that_needs_a_newer_base_is_reported_as_later(self):
        self.publish(self.manifest(requires='0.1.5'))
        self.assertEqual(self.helper('check')[1], [['later', '20261012.1', '0.1.1', 'stable', str(len(KERNEL) + len(SYSTEM)), '0', '0.1.5']])
        self.publish(self.manifest(requires='0.1.0'))                                       # satisfied by what runs
        self.assertEqual(self.helper('check')[1][0][0], 'update')

    def test_same_or_older_builds_the_wrong_device_and_the_wrong_variant_are_not_offered(self):
        for over in ({'build_id': '20261011.1'}, {'build_id': '20261010.9'}, {'hw_device': 'RPi5'}, {'variant': 'beta'}):
            self.publish(self.manifest(**over)); rc, lines, out, err = self.helper('check')
            self.assertEqual((rc, lines), (0, []), over)
        self.publish(self.manifest(build_id='20261011.2')); self.assertEqual(self.helper('check')[1][0][1], '20261011.2')          # same day, later sequence: newer
        self.publish(self.manifest(build_id='20261011.10')); self.assertEqual(self.helper('check')[1][0][1], '20261011.10')        # .10 is later than .1 (numbers, not text)

    def test_a_tampered_manifest_is_refused(self):
        self.publish(); self.srv.files['/manifest.json'] = self.srv.files['/manifest.json'].replace(b'0.1.1', b'9.9.9')
        rc, lines, out, err = self.helper('check')
        self.assertEqual(rc, 1); self.assertTrue(self.error_of(out)[0].startswith('error BadSignature'), out)

    def test_a_manifest_signed_by_another_key_is_refused(self):
        self.run_openssl('genpkey', '-algorithm', 'ED25519', '-out', str(self.d / 'other.pem'))
        self.publish(key='other.pem'); rc, lines, out, err = self.helper('check')
        self.assertEqual(rc, 1); self.assertTrue(self.error_of(out)[0].startswith('error BadSignature'), out)

    def test_an_unsigned_or_garbage_signature_is_refused(self):
        self.publish(sign=False); rc, _, out, _ = self.helper('check')
        self.assertEqual(rc, 1); self.assertTrue(self.error_of(out)[0].startswith('error NoRelease'), out)               # no .sig on the server at all: the release is incomplete, nothing is believed
        self.srv.files['/manifest.json.sig'] = b'not base64 at all !!'
        self.assertTrue(self.error_of(self.helper('check')[2])[0].startswith('error BadSignature'))
        self.srv.files['/manifest.json.sig'] = base64.b64encode(b'short')
        self.assertTrue(self.error_of(self.helper('check')[2])[0].startswith('error BadSignature'))

    def test_a_signed_but_malformed_manifest_is_refused(self):
        bad = [self.manifest(format=2), self.manifest(build_id='tuesday'), self.manifest(update_type=7), self.manifest(files={'KERNEL': self.manifest()['files']['KERNEL']}),
               self.manifest(files={**self.manifest()['files'], 'EVIL': self.manifest()['files']['KERNEL']}),
               self.manifest(files={'KERNEL': {**self.manifest()['files']['KERNEL'], 'sha256': 'abc'}, 'SYSTEM': self.manifest()['files']['SYSTEM']}),
               self.manifest(files={'KERNEL': {**self.manifest()['files']['KERNEL'], 'size': -5}, 'SYSTEM': self.manifest()['files']['SYSTEM']}),
               self.manifest(files={'KERNEL': {**self.manifest()['files']['KERNEL'], 'url': 'ftp://x/y'}, 'SYSTEM': self.manifest()['files']['SYSTEM']})]
        for m in bad:
            self.publish(m); rc, _, out, _ = self.helper('check')
            self.assertEqual(rc, 1, m); self.assertTrue(self.error_of(out)[0].startswith('error BadManifest'), (m, out))
        self.publish(raw=b'{not json'); self.assertTrue(self.error_of(self.helper('check')[2])[0].startswith('error BadManifest'))
        self.assertEqual(self.snapshot(), self.card0)

    def test_a_build_the_card_sent_back_is_not_offered_again_but_a_newer_one_is(self):
        (self.flash / 'wispos/update').mkdir(parents=True); (self.flash / 'wispos/update/FAILED-20261012.1').write_text('slot=b\n')
        rc, lines, out, err = self.helper('check'); self.assertEqual((rc, lines), (0, []), out + err)
        self.assertIn('error UnknownUpdate', self.helper('apply', '20261012.1')[2])                  # nor can it be applied by naming it
        self.publish(self.manifest(build_id='20261012.2', version='0.1.2')); self.assertEqual(self.helper('check')[1][0][1], '20261012.2')

    def test_plain_http_is_refused_in_production(self):
        env = {k: v for k, v in self.env.items() if k != 'WISP_UPDATE_ALLOW_HTTP'}
        r = subprocess.run(['python3', str(HELPER), 'check'], capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 1); self.assertIn('error BadManifest refusing a non-https update url', r.stdout)

    def test_no_source_and_no_key_are_said_plainly(self):
        (self.d / 'update.conf').write_text('{}')
        self.assertTrue(self.error_of(self.helper('check')[2])[0].startswith('error NoUpdateSource'))
        (self.d / 'update.conf').write_text(json.dumps({'manifest_url': self.base + '/manifest.json'})); (self.d / 'pub.pem').unlink()
        self.assertTrue(self.error_of(self.helper('check')[2])[0].startswith('error NoSigningKey'))

    def test_no_release_published_yet_is_said_plainly(self):
        self.srv.files.pop('/manifest.json'); self.srv.files.pop('/manifest.json.sig', None)
        rc, _, out, _ = self.helper('check')
        self.assertEqual(rc, 1); self.assertIn('error NoRelease no update has been published yet', out)

    def test_an_unreachable_server_is_a_network_error(self):
        (self.d / 'update.conf').write_text(json.dumps({'manifest_url': 'http://127.0.0.1:9/manifest.json'}))
        rc, _, out, _ = self.helper('check'); self.assertEqual(rc, 1); self.assertTrue(self.error_of(out)[0].startswith('error NetworkError'), out)

    def test_usage_errors_exit_2(self):
        for args in ((), ('bogus',), ('check', 'x'), ('apply',)): self.assertEqual(self.helper(*args)[0], 2, args)


class ApplyTests(Base):
    def parts(self): return sorted(str(p.relative_to(self.flash)) for p in self.flash.rglob('*.part'))

    def changed(self, before):
        now = self.snapshot(); return sorted(k for k in set(before) | set(now) if before.get(k) != now.get(k))

    def test_staging_goes_into_the_other_slot_and_the_switch_is_last(self):
        rc, lines, out, err = self.helper('apply', '20261012.1')
        self.assertEqual(rc, 0, out + err); self.assertEqual(lines[-1], ['done'])
        self.assertEqual((self.stage / 'KERNEL').read_bytes(), KERNEL); self.assertEqual((self.stage / 'SYSTEM').read_bytes(), SYSTEM)
        self.assertEqual(self.pending, {'slot': 'b', 'build_id': '20261012.1', 'version': '0.1.1', 'tries': '0'})
        self.assertEqual(self.default, 'DEFAULT b')
        self.assertEqual((self.flash / 'KERNEL').read_bytes(), OLD_KERNEL); self.assertEqual((self.flash / 'SYSTEM').read_bytes(), OLD_SYSTEM)       # the running slot is not touched
        pct = [float(l[1]) for l in lines if l[0] == 'progress']
        self.assertEqual(pct, sorted(pct)); self.assertEqual(pct[-1], 100.0)

    def test_only_the_new_slot_the_trial_marker_and_one_letter_of_extlinux_are_written(self):
        self.helper('apply', '20261012.1')
        self.assertEqual(self.changed(self.card0), ['extlinux/extlinux.conf', 'wispos/b/KERNEL', 'wispos/b/SYSTEM', 'wispos/update/PENDING'])
        old, new = self.card0['extlinux/extlinux.conf'], self.snapshot()['extlinux/extlinux.conf']
        self.assertEqual(new, old.replace(b'DEFAULT a', b'DEFAULT b', 1)); self.assertEqual(len(new), len(old))
        self.assertEqual(self.parts(), []); self.assertFalse(list(self.flash.rglob('*.tmp')))

    def test_an_interrupted_download_resumes_where_it_stopped(self):
        self.srv.drop['/SYSTEM'] = 250_000                                                  # the connection dies a third of the way through SYSTEM
        rc, lines, out, err = self.helper('apply', '20261012.1')
        self.assertEqual((rc, lines[-1]), (0, ['done']), out + err)
        self.assertEqual((self.stage / 'SYSTEM').read_bytes(), SYSTEM)
        ranges = [r for p, r in self.srv.log if p == '/SYSTEM']
        self.assertEqual(ranges[0], None); self.assertEqual(ranges[1], 'bytes=250000-')         # asked for the rest, not the whole file again

    def test_parts_left_by_an_earlier_run_are_resumed_and_checked(self):
        self.stage.mkdir(parents=True); (self.stage / 'SYSTEM.part').write_bytes(SYSTEM[:123_456])
        rc, lines, out, err = self.helper('apply', '20261012.1')
        self.assertEqual(rc, 0, out + err); self.assertEqual((self.stage / 'SYSTEM').read_bytes(), SYSTEM)
        self.assertEqual([r for p, r in self.srv.log if p == '/SYSTEM'], ['bytes=123456-'])

    def test_a_server_that_ignores_range_still_gives_a_correct_file(self):
        self.srv.honor_range = False; self.stage.mkdir(parents=True); (self.stage / 'SYSTEM.part').write_bytes(SYSTEM[:100_000])
        rc, lines, out, err = self.helper('apply', '20261012.1')
        self.assertEqual(rc, 0, out + err); self.assertEqual((self.stage / 'SYSTEM').read_bytes(), SYSTEM)
        pct = [float(l[1]) for l in lines if l[0] == 'progress']; self.assertLessEqual(max(pct), 100.0)

    def test_corrupt_bytes_are_refused_deleted_and_never_switched_to(self):
        self.srv.files['/SYSTEM'] = bytes([SYSTEM[0] ^ 1]) + SYSTEM[1:]                       # right size, wrong content
        rc, lines, out, err = self.helper('apply', '20261012.1')
        self.assertEqual(rc, 1); self.assertTrue(self.error_of(out)[0].startswith('error HashMismatch'), out)
        self.assertEqual(self.parts(), []); self.assertFalse((self.stage / 'SYSTEM').exists())
        self.assertEqual((self.pending, self.default), (None, 'DEFAULT a'))

    def test_a_hash_mismatch_leaves_the_slots_other_files_alone(self):
        self.stage.mkdir(parents=True); (self.stage / 'SYSTEM').write_bytes(b'the previous generation'); (self.stage / 'KERNEL').write_bytes(b'its kernel'); before = self.snapshot()
        self.srv.files['/SYSTEM'] = bytes([SYSTEM[0] ^ 1]) + SYSTEM[1:]
        self.assertEqual(self.helper('apply', '20261012.1')[0], 1)
        self.assertEqual((self.stage / 'SYSTEM').read_bytes(), b'the previous generation')                      # the file that was not replaced is still the old one
        self.assertEqual((self.pending, self.default), (None, 'DEFAULT a'))

    def test_a_file_the_card_did_not_keep_is_caught_before_anything_points_at_it(self):
        for name in ('KERNEL', 'SYSTEM'):
            self.setUp()                                                                                          # a fresh card for each
            rc, _, out, _ = self.helper('apply', '20261012.1', env={'WISP_UPDATE_CORRUPT': name})
            self.assertEqual(rc, 1, name); self.assertTrue(self.error_of(out)[0].startswith('error HashMismatch'), out); self.assertIn('nothing was switched', out)
            self.assertEqual((self.pending, self.default), (None, 'DEFAULT a')); self.assertEqual(self.parts(), [])

    def test_a_truncated_server_file_is_refused(self):
        self.srv.files['/SYSTEM'] = SYSTEM[:-10]
        rc, _, out, _ = self.helper('apply', '20261012.1')
        self.assertEqual(rc, 1); self.assertEqual((self.pending, self.default), (None, 'DEFAULT a'))

    def test_not_enough_room_is_reported_before_a_byte_is_downloaded(self):
        rc, _, out, _ = self.helper('apply', '20261012.1', env={'WISP_UPDATE_FREE_BYTES': str(len(KERNEL) + len(SYSTEM) - 1)})
        self.assertEqual(rc, 1); self.assertTrue(self.error_of(out)[0].startswith('error DiskFull'), out)
        self.assertEqual([p for p, r in self.srv.log if p in ('/KERNEL', '/SYSTEM')], [])
        self.assertEqual(self.snapshot(), self.card0)

    def test_only_the_update_the_server_offers_and_this_system_can_take_is_applied(self):
        self.assertTrue(self.error_of(self.helper('apply', '20261099.9')[2])[0].startswith('error UnknownUpdate'))
        self.publish(self.manifest(requires='0.5.0'))
        out = self.helper('apply', '20261012.1')[2]; self.assertIn('error UnknownUpdate', out); self.assertIn('0.5.0', out)
        self.publish(self.manifest(hw_device='RPi5'))
        self.assertIn('error UnknownUpdate', self.helper('apply', '20261012.1')[2])
        self.assertEqual(self.snapshot(), self.card0)

    def test_a_switched_update_is_not_downloaded_twice(self):
        self.helper('apply', '20261012.1'); n = len(self.srv.log); after = self.snapshot()
        rc, lines, out, err = self.helper('apply', '20261012.1')
        self.assertEqual((rc, lines[-1]), (0, ['done'])); self.assertEqual([p for p, r in self.srv.log[n:] if p in ('/KERNEL', '/SYSTEM')], [])
        self.assertEqual(self.snapshot(), after)

    def test_files_the_slot_already_holds_are_kept_and_old_ones_replaced(self):
        self.stage.mkdir(parents=True); (self.stage / 'KERNEL').write_bytes(KERNEL); (self.stage / 'SYSTEM').write_bytes(b'the previous generation')        # an earlier try; an older system
        rc, lines, out, err = self.helper('apply', '20261012.1')
        self.assertEqual(rc, 0, out + err); self.assertEqual([p for p, r in self.srv.log if p in ('/KERNEL', '/SYSTEM')], ['/SYSTEM'])
        self.assertEqual((self.stage / 'SYSTEM').read_bytes(), SYSTEM)
        pct = [float(l[1]) for l in lines if l[0] == 'progress']; self.assertEqual(pct, sorted(pct))

    def test_cancel_removes_the_partial_files_and_exits_143_and_the_card_is_otherwise_unchanged(self):
        self.stage.mkdir(parents=True); (self.stage / 'KERNEL').write_bytes(KERNEL); before = self.snapshot()           # slot b holds a good kernel from an earlier try
        self.srv.throttle = 0.15
        p = subprocess.Popen(['python3', str(HELPER), 'apply', '20261012.1'], env=self.env, stdout=subprocess.PIPE, text=True)
        try:
            end = time.time() + 20
            while time.time() < end and not list(self.stage.glob('*.part')): time.sleep(0.05)
            self.assertTrue(list(self.stage.glob('*.part')), 'the download never started')
            p.send_signal(signal.SIGTERM); self.assertEqual(p.wait(15), 143)
        finally:
            if p.poll() is None: p.kill()
            p.stdout.close()
        self.assertEqual(self.parts(), []); self.assertEqual(self.snapshot(), before)

    def test_network_trouble_keeps_the_parts_for_a_later_resume(self):
        self.srv.files.pop('/SYSTEM')                                                         # 404: refused at once, nothing to wait for
        rc, _, out, _ = self.helper('apply', '20261012.1')
        self.assertEqual(rc, 1); self.assertTrue(self.error_of(out)[0].startswith('error NetworkError'), out); self.assertIn('HTTP 404', out)
        self.assertEqual((self.pending, self.default), (None, 'DEFAULT a'))

    def test_the_boot_partition_is_writable_only_while_staging_and_read_only_again_after(self):
        calls = self.d / 'mount-calls'; stub = self.d / 'bin'; stub.mkdir()
        (stub / 'mount').write_text('#!/bin/sh\necho "$@" >> "%s"\nexit 0\n' % calls); (stub / 'mount').chmod(0o755)
        env = {'WISP_UPDATE_NO_REMOUNT': '', 'PATH': '%s:%s' % (stub, os.environ['PATH'])}
        self.assertEqual(self.helper('apply', '20261012.1', env=env)[0], 0)
        self.assertEqual(calls.read_text().splitlines(), ['-o remount,rw %s' % self.flash, '-o remount,ro %s' % self.flash])
        calls.unlink(); self.srv.files['/SYSTEM'] = SYSTEM[:-1]; self.set_slot('a'); shutil.rmtree(self.flash / 'wispos')
        self.assertEqual(self.helper('apply', '20261012.1', env=env)[0], 1)                  # a failing run must still put it back
        self.assertEqual(calls.read_text().splitlines()[-1], '-o remount,ro %s' % self.flash)

    def test_a_card_that_cannot_be_made_writable_stops_the_update_with_that_reason(self):
        stub = self.d / 'bin'; stub.mkdir(); (stub / 'mount').write_text('#!/bin/sh\nexit 1\n'); (stub / 'mount').chmod(0o755)
        rc, _, out, _ = self.helper('apply', '20261012.1', env={'WISP_UPDATE_NO_REMOUNT': '', 'PATH': '%s:%s' % (stub, os.environ['PATH'])})
        self.assertEqual(rc, 1); self.assertTrue(self.error_of(out)[0].startswith('error FlashReadOnly'), out)
        self.assertEqual(self.snapshot(), self.card0)


class DurabilityTests(Base):
    """Found by the QEMU power-cut test (os/boot/qemu_update_test.py): the helper fsynced every file and the directory the files are IN, but a NEW directory's entry lives in its parent, so a
    cut after the staging lost the new directory with everything in it. Run in-process here, with fsync_dir recorded."""
    def load(self):
        import importlib.machinery, unittest.mock
        patcher = unittest.mock.patch.dict(os.environ, self.env); patcher.start(); self.addCleanup(patcher.stop)         # the helper reads some settings when it runs, not only when it is loaded
        return importlib.machinery.SourceFileLoader('wisp_update_under_test', str(HELPER)).load_module()

    def test_a_new_directory_is_synced_in_every_directory_above_it_up_to_the_card_root(self):
        m = self.load(); calls = []; real = m.fsync_dir
        m.fsync_dir = lambda p: (calls.append(p), real(p))[1]
        target = str(self.flash / 'wispos/deeper/still/newer'); m.ensure_dir(target)
        self.assertEqual(calls, [target, str(self.flash / 'wispos/deeper/still'), str(self.flash / 'wispos/deeper'), str(self.flash / 'wispos'), str(self.flash)])      # child first, then each parent, never above the root
        calls.clear(); m.ensure_dir(target); self.assertEqual(calls[-1], str(self.flash))                                  # an existing directory is simply synced again

    def test_a_renamed_file_is_synced_under_its_new_name_before_its_directory(self):
        import tempfile
        m = self.load(); events = []; real_fsync, real_replace = os.fsync, os.replace
        d = Path(tempfile.mkdtemp(prefix='wispos-rename-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, d, True)
        src, dst = d / 'a.part', d / 'a'; src.write_bytes(b'x')
        m.os = type('OsProbe', (), {})()                                    # a probe in place of the module's os: records the order of the three calls the module makes
        for name in dir(os):
            if not name.startswith('__'): setattr(m.os, name, getattr(os, name))
        m.os.replace = lambda a, b: (events.append(('replace', Path(a).name, Path(b).name)), real_replace(a, b))[1]
        m.os.fsync = lambda fd: (events.append(('fsync', os.readlink('/proc/self/fd/%d' % fd))), real_fsync(fd))[1]
        m.durable_rename(str(src), str(dst))
        self.assertEqual(events, [('replace', 'a.part', 'a'), ('fsync', str(dst)), ('fsync', str(d))])             # the file under the NEW name, then the directory

    def test_the_staged_files_and_the_trial_marker_are_renamed_durably(self):
        import contextlib, io
        m = self.load(); renames = []; real = m.durable_rename
        m.durable_rename = lambda a, b: (renames.append((Path(a).name, str(Path(b).relative_to(self.flash)))), real(a, b))[1]
        with contextlib.redirect_stdout(io.StringIO()): m.apply('20261012.1')
        self.assertEqual(renames, [('KERNEL.part', 'wispos/b/KERNEL'), ('SYSTEM.part', 'wispos/b/SYSTEM'), ('PENDING.tmp', 'wispos/update/PENDING')])

    def test_apply_makes_the_slot_directory_and_the_update_directory_durable_before_it_writes_into_them(self):
        import contextlib, io
        m = self.load(); events = []; real_fsync, real_replace = m.fsync_dir, os.replace
        m.fsync_dir = lambda p: (events.append(('fsync', p)), real_fsync(p))[1]
        slot, upd, root, wispos = str(self.flash / 'wispos/b'), str(self.flash / 'wispos/update'), str(self.flash), str(self.flash / 'wispos')
        with contextlib.redirect_stdout(io.StringIO()): m.apply('20261012.1')
        self.assertEqual(events[:6], [('fsync', slot), ('fsync', wispos), ('fsync', root), ('fsync', upd), ('fsync', wispos), ('fsync', root)])        # both new directories, each synced in every directory above it, before any download


class SlotTests(Base):
    """Which slot is running decides where an update goes; and when nothing may be staged."""
    def test_running_slot_b_updates_slot_a_in_the_card_root(self):
        self.set_slot('b'); (self.flash / 'wispos/b').mkdir(parents=True); (self.flash / 'wispos/b/KERNEL').write_bytes(OLD_KERNEL); (self.flash / 'wispos/b/SYSTEM').write_bytes(OLD_SYSTEM)
        rc, lines, out, err = self.helper('apply', '20261012.1'); self.assertEqual(rc, 0, out + err)
        self.assertEqual((self.flash / 'KERNEL').read_bytes(), KERNEL); self.assertEqual((self.flash / 'SYSTEM').read_bytes(), SYSTEM)
        self.assertEqual((self.flash / 'wispos/b/KERNEL').read_bytes(), OLD_KERNEL)                  # the running slot is untouched
        self.assertEqual((self.pending['slot'], self.default), ('a', 'DEFAULT a')); self.assertFalse(list(self.flash.rglob('*.part')))

    def test_a_command_line_without_a_slot_means_slot_a(self):
        (self.d / 'cmdline').write_text('boot=LABEL=ROCKNIX quiet\n')
        self.assertEqual(self.helper('apply', '20261012.1')[0], 0); self.assertEqual(self.pending['slot'], 'b')

    def test_nothing_is_staged_while_the_running_version_is_on_trial(self):
        self.set_slot('b'); self.set_pending(slot='b', build_id='20261011.5', tries=1); before = self.snapshot()
        rc, _, out, _ = self.helper('apply', '20261012.1')
        self.assertEqual(rc, 1); self.assertTrue(self.error_of(out)[0].startswith('error UpdatePending'), out); self.assertIn('confirmed itself', out)
        self.assertEqual(self.snapshot(), before); self.assertEqual([p for p, r in self.srv.log if p in ('/KERNEL', '/SYSTEM')], [])             # the other slot is the way back: not touched

    def test_nothing_is_staged_while_another_update_waits_for_its_restart(self):
        self.set_slot('a', default='b'); self.set_pending(slot='b', build_id='20261011.9'); before = self.snapshot()
        rc, _, out, _ = self.helper('apply', '20261012.1')
        self.assertEqual(rc, 1); self.assertTrue(self.error_of(out)[0].startswith('error UpdatePending'), out); self.assertIn('waiting for a restart', out); self.assertEqual(self.snapshot(), before)

    def test_the_update_that_already_waits_for_its_restart_is_reported_done(self):
        self.set_slot('a', default='b'); self.set_pending(slot='b', build_id='20261012.1'); before = self.snapshot()
        rc, lines, out, err = self.helper('apply', '20261012.1'); self.assertEqual((rc, lines[-1]), (0, ['done']), out + err); self.assertEqual(self.snapshot(), before)

    def test_a_marker_for_a_switch_that_never_happened_does_not_block_and_is_replaced(self):
        self.set_pending(slot='b', build_id='20261001.1')                                           # slot a runs and is the default: the old marker is stale
        rc, _, out, err = self.helper('apply', '20261012.1'); self.assertEqual(rc, 0, out + err)
        self.assertEqual((self.pending['build_id'], self.default), ('20261012.1', 'DEFAULT b'))

    def test_a_card_that_does_not_start_the_running_system_is_left_alone(self):
        self.set_slot('a', default='b'); before = self.snapshot()                                    # no marker, yet U-Boot would start b: somebody edited it
        rc, _, out, _ = self.helper('apply', '20261012.1')
        self.assertEqual(rc, 1); self.assertTrue(self.error_of(out)[0].startswith('error SlotMismatch'), out); self.assertEqual(self.snapshot(), before)

    def test_an_extlinux_conf_in_an_unexpected_form_is_never_edited(self):
        for text in ('', 'LABEL a\nDEFAULT a\n', 'DEFAULT  a\n', 'default a\n', 'DEFAULT c\n', 'DEFAULT a'):
            (self.flash / 'extlinux/extlinux.conf').write_text(text); before = self.snapshot()
            rc, _, out, _ = self.helper('apply', '20261012.1')
            self.assertEqual(rc, 1, text); self.assertTrue(self.error_of(out)[0].startswith('error BootConfig'), (text, out)); self.assertEqual(self.snapshot(), before, text)
            self.assertEqual([p for p, r in self.srv.log if p in ('/KERNEL', '/SYSTEM')], [])

    def test_status_says_what_the_card_says(self):
        rc, lines, out, err = self.helper('status'); self.assertEqual((rc, lines), (0, [['running', 'a'], ['default', 'a']]), err)
        self.set_slot('b'); self.set_pending(slot='b', tries=1); (self.flash / 'wispos/update/FAILED-20260101.1').write_text('slot=b\n')
        self.assertEqual(self.helper('status')[1], [['running', 'b'], ['default', 'b'], ['pending', 'b', '20261012.1', 'tries=1'], ['failed', '20260101.1']])
        (self.flash / 'extlinux/extlinux.conf').write_text('garbage\n'); self.assertEqual(self.helper('status')[1][1], ['default', 'unreadable'])


class DevSourceTests(Base):
    """A dev image's update-dev.json (what os/dev/test-release.sh serves a test release for): honoured only where the DEV-BUILD marker exists, plain http only when the file says so
    exactly, and the signature and hash checks exactly as before."""
    def setUp(self):
        super().setUp()
        self.mark, self.devconf = self.d / 'DEV-BUILD', self.d / 'update-dev.json'
        (self.d / 'update.conf').write_text('{}')                                             # the image's own source is empty here; the override supplies one
        self.env.update(WISP_DEV_MARK=str(self.mark), WISP_UPDATE_DEV_CONF=str(self.devconf), WISP_UPDATE_ALLOW_HTTP='0')

    def dev(self, **conf):
        self.mark.write_text(''); self.devconf.write_text(json.dumps({'manifest_url': self.base + '/manifest.json', **conf}))

    def test_a_dev_image_installs_from_the_source_in_its_dev_file(self):
        self.dev(allow_http=True)
        rc, lines, out, err = self.helper('check'); self.assertEqual((rc, lines[0][:3]), (0, ['update', '20261012.1', '0.1.1']), out + err)
        rc, lines, out, err = self.helper('apply', '20261012.1'); self.assertEqual(rc, 0, out + err)
        self.assertEqual((self.stage / 'SYSTEM').read_bytes(), SYSTEM); self.assertEqual(self.default, 'DEFAULT b')
        self.assertEqual(self.helper('status')[1][:2], [['running', 'a'], ['source', self.base + '/manifest.json']])        # and status says the override is in use

    def test_a_release_image_never_looks_at_the_dev_file(self):
        self.dev(allow_http=True); self.mark.unlink()                                          # no DEV-BUILD marker: a release image
        rc, _, out, _ = self.helper('check'); self.assertEqual(rc, 1); self.assertTrue(self.error_of(out)[0].startswith('error NoUpdateSource'), out)
        (self.d / 'update.conf').write_text(json.dumps({'manifest_url': self.base + '/manifest.json'}))              # and its own plain-http source is refused, whatever the dev file allows
        rc, _, out, _ = self.helper('check'); self.assertEqual(rc, 1); self.assertIn('non-https', out); self.assertEqual(self.srv.log, [])
        self.assertEqual(self.helper('status')[1], [['running', 'a'], ['default', 'a']])

    def test_plain_http_needs_the_dev_file_to_say_so_exactly(self):
        for conf in ({}, {'allow_http': False}, {'allow_http': 'true'}, {'allow_http': 1}):
            self.dev(**conf); rc, _, out, _ = self.helper('check')
            self.assertEqual(rc, 1, conf); self.assertIn('non-https', out, conf)
        self.assertEqual(self.srv.log, [])                                                     # not one request was made

    def test_the_dev_source_changes_nothing_about_who_may_sign(self):
        self.dev(allow_http=True); self.run_openssl('genpkey', '-algorithm', 'ED25519', '-out', str(self.d / 'other.pem')); self.publish(key='other.pem')
        rc, _, out, _ = self.helper('check'); self.assertEqual(rc, 1); self.assertTrue(self.error_of(out)[0].startswith('error BadSignature'), out)
        rc, _, out, _ = self.helper('apply', '20261012.1'); self.assertEqual(rc, 1); self.assertEqual(self.snapshot(), self.card0)

    def test_an_unusable_dev_file_leaves_the_images_own_source(self):
        (self.d / 'update.conf').write_text(json.dumps({'manifest_url': self.base + '/manifest.json'})); self.env['WISP_UPDATE_ALLOW_HTTP'] = '1'; self.mark.write_text('')
        for text in ('not json', '[1]', '{"manifest_url": 5}', '{"manifest_url": ""}', '{}'):
            self.devconf.write_text(text); rc, lines, out, err = self.helper('check'); self.assertEqual((rc, lines[0][0]), (0, 'update'), (text, out, err))
        self.assertEqual(self.helper('status')[1], [['running', 'a'], ['default', 'a']])       # none of them counted as an override


class BootsTests(Base):
    """The bootloader's count of starts that never reached the initramfs (wispos/update/BOOTS) belongs to one trial."""
    def put_boots(self, n=2):
        (self.flash / 'wispos/update').mkdir(parents=True, exist_ok=True); (self.flash / 'wispos/update/BOOTS').write_bytes(b'boot_tries=%d\n' % n)

    def test_a_new_trial_starts_from_zero(self):
        self.put_boots(2)
        rc, _, out, err = self.helper('apply', '20261012.1'); self.assertEqual(rc, 0, out + err)
        self.assertFalse((self.flash / 'wispos/update/BOOTS').exists()); self.assertEqual((self.pending['slot'], self.default), ('b', 'DEFAULT b'))

    def test_a_cut_before_the_marker_is_written_leaves_the_stale_count_harmless(self):
        self.put_boots(2)
        rc, _, out, _ = self.helper('apply', '20261012.1', env={'WISP_UPDATE_STOP_AFTER': 'staged'}); self.assertEqual(rc, 99)
        self.assertEqual((self.pending, self.default), (None, 'DEFAULT a'))                      # no marker, the letter still a: the bootloader counts nothing

    def test_confirming_ends_the_count_too(self):
        self.set_slot('b'); self.set_pending(slot='b', tries=1); self.put_boots(1)
        rc, lines, out, err = self.helper('confirm'); self.assertEqual((rc, lines), (0, [['confirmed', '20261012.1']]), err)
        self.assertFalse((self.flash / 'wispos/update/BOOTS').exists())

    def test_confirming_on_a_system_that_is_not_on_trial_leaves_the_count_alone(self):
        self.set_slot('a'); self.set_pending(slot='b', tries=0); self.put_boots(1)
        self.assertEqual(self.helper('confirm')[0], 0); self.assertTrue((self.flash / 'wispos/update/BOOTS').exists())


class PowerCutTests(Base):
    """The commit is: staged files -> PENDING -> the letter. The process is ended at once (no clean-up, nothing flushed by it) after each step."""
    def cut(self, point):
        rc, lines, out, err = self.helper('apply', '20261012.1', env={'WISP_UPDATE_STOP_AFTER': point}); self.assertEqual(rc, 99, (point, out, err)); return out

    def test_a_cut_after_the_files_are_staged_leaves_the_old_system_selected_and_the_update_finishable(self):
        self.cut('staged')
        self.assertEqual((self.pending, self.default), (None, 'DEFAULT a')); self.assertEqual((self.stage / 'SYSTEM').read_bytes(), SYSTEM)
        n = len(self.srv.log); self.assertEqual(self.helper('apply', '20261012.1')[0], 0)
        self.assertEqual([p for p, r in self.srv.log[n:] if p in ('/KERNEL', '/SYSTEM')], []); self.assertEqual(self.default, 'DEFAULT b')            # nothing downloaded again

    def test_a_cut_after_the_marker_leaves_a_stale_marker_and_the_old_system_selected(self):
        self.cut('pending')
        self.assertEqual((self.pending['slot'], self.default), ('b', 'DEFAULT a'))
        self.assertEqual(self.helper('apply', '20261012.1')[0], 0); self.assertEqual((self.pending['build_id'], self.default), ('20261012.1', 'DEFAULT b'))     # finishes, replacing the stale marker

    def test_a_cut_after_the_switch_leaves_the_new_system_selected_with_its_marker(self):
        self.cut('switched')
        self.assertEqual((self.pending, self.default), ({'slot': 'b', 'build_id': '20261012.1', 'version': '0.1.1', 'tries': '0'}, 'DEFAULT b'))
        self.assertEqual((self.flash / 'KERNEL').read_bytes(), OLD_KERNEL)

    def test_at_no_cut_point_is_the_selected_slot_incomplete(self):
        for point in ('staged', 'pending', 'switched'):
            d = Path(tempfile.mkdtemp(prefix='wispos-cut-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, d, True)
            shutil.copytree(self.flash, d / 'flash'); env = {**self.env, 'WISP_FLASH_DIR': str(d / 'flash'), 'WISP_UPDATE_STOP_AFTER': point}
            r = subprocess.run(['python3', str(HELPER), 'apply', '20261012.1'], env=env, capture_output=True, text=True); self.assertEqual(r.returncode, 99)
            letter = (d / 'flash/extlinux/extlinux.conf').read_text().splitlines()[0][-1]
            base = d / 'flash' if letter == 'a' else d / 'flash/wispos/b'
            self.assertTrue(all((base / n).is_file() and (base / n).stat().st_size > 0 for n in ('KERNEL', 'SYSTEM')), point)


class ConfirmTests(Base):
    def run_confirm(self, env=None): return self.helper('confirm', env=env)

    def test_a_version_on_trial_confirms_itself_and_the_marker_goes(self):
        self.set_slot('b'); self.set_pending(slot='b', tries=1)
        rc, lines, out, err = self.run_confirm(); self.assertEqual((rc, lines), (0, [['confirmed', '20261012.1']]), err)
        self.assertEqual(self.pending, None); self.assertEqual(self.default, 'DEFAULT b')

    def test_the_old_system_cannot_confirm_the_new_one(self):
        self.set_slot('a', default='b'); self.set_pending(slot='b')                               # the switch is made; the restart is not
        rc, lines, out, err = self.run_confirm(); self.assertEqual((rc, lines), (0, []), err); self.assertEqual(self.pending['slot'], 'b')

    def test_nothing_to_confirm_touches_nothing_and_does_not_even_remount(self):
        calls = self.d / 'mount-calls'; stub = self.d / 'bin'; stub.mkdir()
        (stub / 'mount').write_text('#!/bin/sh\necho "$@" >> "%s"\nexit 0\n' % calls); (stub / 'mount').chmod(0o755)
        rc, lines, out, err = self.run_confirm(env={'WISP_UPDATE_NO_REMOUNT': '', 'PATH': '%s:%s' % (stub, os.environ['PATH'])})
        self.assertEqual((rc, lines), (0, [])); self.assertFalse(calls.exists()); self.assertEqual(self.snapshot(), self.card0)

    def test_only_the_marker_is_removed_and_the_card_goes_read_only_again(self):
        self.set_slot('b'); self.set_pending(slot='b', tries=2); before = self.snapshot()
        calls = self.d / 'mount-calls'; stub = self.d / 'bin'; stub.mkdir()
        (stub / 'mount').write_text('#!/bin/sh\necho "$@" >> "%s"\nexit 0\n' % calls); (stub / 'mount').chmod(0o755)
        self.assertEqual(self.run_confirm(env={'WISP_UPDATE_NO_REMOUNT': '', 'PATH': '%s:%s' % (stub, os.environ['PATH'])})[0], 0)
        self.assertEqual(sorted(set(before) - set(self.snapshot())), ['wispos/update/PENDING']); self.assertEqual(set(self.snapshot()) - set(before), set())
        self.assertEqual(calls.read_text().splitlines(), ['-o remount,rw %s' % self.flash, '-o remount,ro %s' % self.flash])

    def test_a_card_that_will_not_become_writable_keeps_the_trial_and_says_why(self):
        self.set_slot('b'); self.set_pending(slot='b', tries=1)
        stub = self.d / 'bin'; stub.mkdir(); (stub / 'mount').write_text('#!/bin/sh\nexit 1\n'); (stub / 'mount').chmod(0o755)
        rc, _, out, _ = self.run_confirm(env={'WISP_UPDATE_NO_REMOUNT': '', 'PATH': '%s:%s' % (stub, os.environ['PATH'])})
        self.assertEqual(rc, 1); self.assertTrue(self.error_of(out)[0].startswith('error FlashReadOnly'), out); self.assertEqual(self.pending['tries'], '1')


class BootContract(Base):
    """What this helper writes is what the initramfs step reads (os/boot/wisp-boot.sh, run for real in dash on the same fake card)."""
    def boot(self, tries=None, crash_at=None):
        import test_boot_slots as T
        slot = self.default[-1]; cmd = self.d / 'bootcmd'; cmd.write_text('boot=LABEL=ROCKNIX wisp.slot=%s\n' % slot); log = self.d / 'bootlog'
        env = {'PATH': os.environ['PATH'], 'WT_CODE': '', 'WT_LOG': str(log), 'WT_SCRIPT': str(T.SCRIPT), 'WT_CRASH_AT': '', 'WISP_FLASH': str(self.flash), 'WISP_CMDLINE': str(cmd), 'WISP_KMSG': str(self.d / 'kmsg')}
        r = subprocess.run(['dash', '-c', T.DRIVER], capture_output=True, text=True, env=env, timeout=60); out = r.stdout.splitlines()
        (self.d / 'cmdline').write_text('boot=LABEL=ROCKNIX wisp.slot=%s\n' % self.default[-1])          # what the system that U-Boot starts next will report about itself
        return {'reboot': 'REBOOT' in out, 'image': next((l.split('=', 1)[1] for l in out if l.startswith('IMAGE_SYSTEM=')), None), 'slot': slot}

    def test_an_update_starts_on_trial_runs_its_own_system_and_confirms(self):
        if not shutil.which('dash'): self.skipTest('no dash')
        self.assertEqual(self.helper('apply', '20261012.1')[0], 0)
        r = self.boot(); self.assertEqual((r['slot'], r['image'], r['reboot'], self.pending['tries']), ('b', 'wispos/b/SYSTEM', False, '1'))
        self.assertEqual(self.helper('confirm')[1], [['confirmed', '20261012.1']]); self.assertEqual(self.pending, None)
        r = self.boot(); self.assertEqual((r['slot'], r['image'], r['reboot']), ('b', 'wispos/b/SYSTEM', False)); self.assertEqual(self.default, 'DEFAULT b')

    def test_an_update_that_never_confirms_is_sent_back_and_not_offered_again(self):
        if not shutil.which('dash'): self.skipTest('no dash')
        self.assertEqual(self.helper('apply', '20261012.1')[0], 0)
        self.assertEqual([self.boot()['reboot'], self.boot()['reboot'], self.boot()['reboot']], [False, False, True])           # two starts on trial, the third goes back
        self.assertEqual((self.default, self.pending), ('DEFAULT a', None)); self.assertTrue((self.flash / 'wispos/update/FAILED-20261012.1').exists())
        r = self.boot(); self.assertEqual((r['slot'], r['image'], r['reboot']), ('a', 'SYSTEM', False))
        self.assertEqual(self.helper('check')[1], []); self.assertIn('error UnknownUpdate', self.helper('apply', '20261012.1')[2])
        self.publish(self.manifest(build_id='20261012.2', version='0.1.2')); self.assertEqual(self.helper('apply', '20261012.2')[0], 0)           # a newer build goes through the same way
        self.assertEqual((self.pending['build_id'], self.default), ('20261012.2', 'DEFAULT b'))

    def test_the_second_update_goes_back_into_the_first_slot(self):
        if not shutil.which('dash'): self.skipTest('no dash')
        self.assertEqual(self.helper('apply', '20261012.1')[0], 0); self.boot(); self.helper('confirm')
        (self.d / 'os-release').write_text('BUILD_ID="20261012.1"\nWISPOS_VERSION="0.1.1"\nHW_DEVICE="H700"\n')                  # now the new system is the running one
        k2, s2 = os.urandom(1000), os.urandom(2000)
        self.srv.files['/KERNEL'], self.srv.files['/SYSTEM'] = k2, s2
        self.publish(self.manifest(build_id='20261020.1', version='0.1.2', files={'KERNEL': {'url': self.base + '/KERNEL', 'size': len(k2), 'sha256': hashlib.sha256(k2).hexdigest()},
                                                                                  'SYSTEM': {'url': self.base + '/SYSTEM', 'size': len(s2), 'sha256': hashlib.sha256(s2).hexdigest()}}))
        self.srv.files['/KERNEL'], self.srv.files['/SYSTEM'] = k2, s2
        rc, lines, out, err = self.helper('apply', '20261020.1'); self.assertEqual(rc, 0, out + err)
        self.assertEqual((self.flash / 'KERNEL').read_bytes(), k2); self.assertEqual((self.flash / 'wispos/b/KERNEL').read_bytes(), KERNEL)           # the running slot b still holds the first update
        self.assertEqual((self.pending['slot'], self.default), ('a', 'DEFAULT a'))


MUTANTS = [
    ('a new directory is not made durable in its parents', "    while True:\n        fsync_dir(p)\n        if p == top or not p.startswith(top): break\n        p = os.path.dirname(p)\n", "    fsync_dir(p)\n"),
    ('a renamed file is not synced under its new name', "    os.replace(src, dst)\n    fd = os.open(dst, os.O_RDONLY)\n    try: os.fsync(fd)\n    finally: os.close(fd)\n", "    os.replace(src, dst)\n"),
    ('the staged file is renamed without the durable rename', "    durable_rename(part, final)", "    os.replace(part, final)"),
    ('the trial marker is renamed without the durable rename', "    durable_rename(tmp, PENDING)", "    os.replace(tmp, PENDING)"),
    ('the update is written into the RUNNING slot', "target = other(run); slot_dir", "target = run; slot_dir"),
    ('the letter is flipped before the marker is written',
     "write_pending(target, update_id, m['version']); stop_after('pending')\n        set_default(target); os.sync(); stop_after('switched')",
     "set_default(target); stop_after('pending'); write_pending(target, update_id, m['version']); os.sync(); stop_after('switched')"),
    ('a version on trial does not block staging', "        raise Fail('UpdatePending', 'an update is waiting for a restart' if default != run else", "        if 0: raise Fail('UpdatePending', 'an update is waiting for a restart' if default != run else"),
    ('a stale marker blocks staging', "    if pend and pend.get('slot') == default:", "    if pend:"),
    ('a card that does not start the running system is switched anyway', "    if default != run: raise Fail('SlotMismatch',", "    if 0: raise Fail('SlotMismatch',"),
    ('files the slot already holds are downloaded again', "    todo = [n for n in FILES if not matches(os.path.join(slot_dir, n), m['files'][n])]", "    todo = list(FILES)"),
    ('a build the card sent back is offered again', "    if m['build_id'] in failed_builds(): return None", "    pass"),
    ('any system can confirm any trial', "    if not pend or pend.get('slot') != running_slot(): return", "    if not pend: return"),
    ('the letter is written one byte too far', "f.seek(8); f.write(slot.encode())", "f.seek(9); f.write(slot.encode())"),
    ('extlinux.conf is edited whatever it looks like', "    if first not in (b'DEFAULT a\\n', b'DEFAULT b\\n'): raise Fail(", "    if 0: raise Fail("),
    ('the staged files are not read back from the card before the switch', "        if not all(matches(os.path.join(slot_dir, n), m['files'][n], from_card=True) for n in FILES):", "        if 0:"),
    ('the free-space check is gone', "        if free_bytes() + partial < need + FREE_MARGIN: raise Fail(", "        if 0: raise Fail("),
    ('a cancel deletes the slot, not only the parts', "        os.unlink(part); raise Fail('HashMismatch'", "        os.unlink(final) if os.path.exists(final) else None; os.unlink(part); raise Fail('HashMismatch'"),
    ('the trial marker starts at one start already', "tries=0\\n'); f.flush(); os.fsync(f.fileno())", "tries=1\\n'); f.flush(); os.fsync(f.fileno())"),
    ('the marker names the wrong slot', "f.write(f'slot={slot}\\nbuild_id", "f.write(f'slot={other(slot)}\\nbuild_id"),
    ('confirm leaves the marker', "        try: os.unlink(PENDING)\n        except FileNotFoundError: pass\n        fsync_dir(UPDATE_DIR); drop_boots(); os.sync()\n    say('confirmed'", "        pass\n    say('confirmed'"),
    ('the second slot is staged in a directory that does not exist for slot a', "SLOT_DIR = {'a': FLASH, 'b':", "SLOT_DIR = {'a': os.path.join(FLASH, 'a'), 'b':"),
    ('the cancel handler does not clean up', "            drop_parts(slot_dir); raise\n        except Fail as e:", "            raise\n        except Fail as e:"),
    ('a stale count survives into a new trial', "drop_boots(); write_pending(target, update_id, m['version'])", "write_pending(target, update_id, m['version'])"),
    ('confirming leaves the count', "fsync_dir(UPDATE_DIR); drop_boots(); os.sync()", "fsync_dir(UPDATE_DIR); os.sync()"),
    ('a hash mismatch is accepted', "    if have != size or h.hexdigest() != want:", "    if have != size:"),
    ('a release image reads the dev file', "    if os.path.exists(DEV_MARK):", "    if True:"),
    ('plain http is allowed by any allow_http value', "if d.get('allow_http') is True: ALLOW_HTTP = True", "if d.get('allow_http'): ALLOW_HTTP = True"),
    ('plain http is always allowed', "(url.startswith('http://') and ALLOW_HTTP)", "url.startswith('http://')"),
    ('allow_http in the dev file does nothing', "if d.get('allow_http') is True: ALLOW_HTTP = True", "pass"),
    ('the dev url is not used', "url, dev = d['manifest_url'], True", "url, dev = url, False"),
    ('status does not say the override is in use', "if dev: say('source', url)", "pass"),
]


class Mutants(unittest.TestCase):
    """Each rule of the helper changed in turn: the other tests (run in parallel against the changed copy) must fail every time. (Not covered, because a directory cannot show it:
    that the read-back drops the page cache first, so it reads the card and not this process's own writes.)"""
    def test_every_mutant_is_caught(self):
        if 'WISP_UPDATE_PATH' in os.environ: self.skipTest('running a mutant already')
        import concurrent.futures
        (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix='wispos-updmut-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, tmp, True)
        text = HELPER.read_text(); jobs = []
        for i, (what, old, new) in enumerate(MUTANTS):
            self.assertEqual(text.count(old), 1, 'mutant %r: the pattern must occur exactly once' % what)
            m = tmp / ('mutant%d' % i); m.write_text(text.replace(old, new)); jobs.append((what, m))
        names = ['CheckTests', 'ApplyTests', 'DurabilityTests', 'SlotTests', 'PowerCutTests', 'ConfirmTests', 'BootContract', 'DevSourceTests', 'BootsTests']
        def run(job):
            what, m = job
            r = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), *names], env={**os.environ, 'WISP_UPDATE_PATH': str(m)}, capture_output=True, text=True, timeout=1800)
            return what, r.returncode
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex: results = list(ex.map(run, jobs))
        self.assertEqual([w for w, rc in results if rc == 0], [], 'mutants that no test noticed')


if __name__ == '__main__':
    unittest.main()
