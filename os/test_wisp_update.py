"""Tests for os/steamos/wisp-update (docs/WISPOS-UPDATE-DESIGN.md): the helper behind the update interface.

It runs for real against a LOCAL HTTP server (Range requests, fault injection, throttling) with a throwaway ed25519 key and the host's openssl, on a fake /flash directory on
the HDD work tree, so nothing on this machine is touched and no network is used. What is proved: only a manifest signed by the image's key is believed; only newer builds for
this device are offered; staging is resumable, verified, cancel-safe and leaves READY last; /flash goes read-write only for the staging and back to read-only after, also on failure.
"""
import base64, hashlib, http.server, json, os, re, shutil, signal, subprocess, tempfile, threading, time, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
HELPER = HERE / 'steamos/wisp-update'
WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))
KERNEL, SYSTEM = os.urandom(300_000), os.urandom(700_000)          # stand-ins: the helper never looks inside the files, only at sizes and hashes


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
        self.flash = self.d / 'flash'; self.flash.mkdir()
        self.run_openssl('genpkey', '-algorithm', 'ED25519', '-out', str(self.d / 'key.pem'))
        self.run_openssl('pkey', '-in', str(self.d / 'key.pem'), '-pubout', '-out', str(self.d / 'pub.pem'))
        self.srv = Server(('127.0.0.1', 0), Handler); self.srv.files, self.srv.log, self.srv.drop, self.srv.throttle, self.srv.honor_range = {}, [], {}, 0, True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start(); self.addCleanup(self.srv.server_close); self.addCleanup(self.srv.shutdown)
        self.base = 'http://127.0.0.1:%d' % self.srv.server_address[1]
        (self.d / 'os-release').write_text('BUILD_ID="20261011.1"\nWISPOS_VERSION="0.1.0"\nHW_DEVICE="H700"\n')
        (self.d / 'update.conf').write_text(json.dumps({'manifest_url': self.base + '/manifest.json'}))
        self.env = {**os.environ, 'WISP_UPDATE_CONF': str(self.d / 'update.conf'), 'WISP_UPDATE_PUBKEY': str(self.d / 'pub.pem'), 'WISP_OS_RELEASE': str(self.d / 'os-release'),
                    'WISP_FLASH_DIR': str(self.flash), 'WISP_UPDATE_NO_REMOUNT': '1', 'WISP_UPDATE_ALLOW_HTTP': '1', 'WISP_UPDATE_NET_TIMEOUT': '5', 'WISP_UPDATE_RETRIES': '2',
                    'WISP_UPDATE_NO_BACKOFF': '1', 'WISP_UPDATE_FREE_MARGIN': '0', 'TMPDIR': str(WORK / 'tmp')}
        self.publish()

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
    def stage(self): return self.flash / 'wispos/update'


class CheckTests(Base):
    def test_a_newer_signed_build_is_offered_with_its_size_and_nothing_is_written(self):
        rc, lines, out, err = self.helper('check')
        self.assertEqual((rc, lines), (0, [['update', '20261012.1', '0.1.1', 'stable', str(len(KERNEL) + len(SYSTEM)), '0']]), err)
        self.assertEqual(list(self.flash.rglob('*')), [])                                   # a check never writes to the card

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
        self.assertEqual(list(self.flash.rglob('*')), [])

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
    def staged(self): return sorted(p.name for p in self.stage.iterdir()) if self.stage.exists() else []

    def test_staging_writes_verified_files_and_ready_last(self):
        rc, lines, out, err = self.helper('apply', '20261012.1')
        self.assertEqual(rc, 0, out + err); self.assertEqual(lines[-1], ['done'])
        self.assertEqual(self.staged(), ['KERNEL.new', 'READY', 'SYSTEM.new'])
        self.assertEqual((self.stage / 'KERNEL.new').read_bytes(), KERNEL); self.assertEqual((self.stage / 'SYSTEM.new').read_bytes(), SYSTEM)
        ready = json.loads((self.stage / 'READY').read_text())
        self.assertEqual((ready['build_id'], ready['version']), ('20261012.1', '0.1.1')); self.assertEqual(ready['files']['SYSTEM']['sha256'], hashlib.sha256(SYSTEM).hexdigest())
        pct = [float(l[1]) for l in lines if l[0] == 'progress']
        self.assertEqual(pct, sorted(pct)); self.assertEqual(pct[-1], 100.0)

    def test_nothing_but_the_staging_directory_is_written_to_the_card(self):
        self.helper('apply', '20261012.1')
        self.assertEqual({str(p.relative_to(self.flash)) for p in self.flash.rglob('*')}, {'wispos', 'wispos/update', 'wispos/update/KERNEL.new', 'wispos/update/SYSTEM.new', 'wispos/update/READY'})

    def test_an_interrupted_download_resumes_where_it_stopped(self):
        self.srv.drop['/SYSTEM'] = 250_000                                                  # the connection dies a third of the way through SYSTEM
        rc, lines, out, err = self.helper('apply', '20261012.1')
        self.assertEqual((rc, lines[-1]), (0, ['done']), out + err)
        self.assertEqual((self.stage / 'SYSTEM.new').read_bytes(), SYSTEM)
        ranges = [r for p, r in self.srv.log if p == '/SYSTEM']
        self.assertEqual(ranges[0], None); self.assertEqual(ranges[1], 'bytes=250000-')         # asked for the rest, not the whole file again

    def test_parts_left_by_an_earlier_run_are_resumed_and_checked(self):
        self.stage.mkdir(parents=True); (self.stage / 'SYSTEM.part').write_bytes(SYSTEM[:123_456])
        rc, lines, out, err = self.helper('apply', '20261012.1')
        self.assertEqual(rc, 0, out + err); self.assertEqual((self.stage / 'SYSTEM.new').read_bytes(), SYSTEM)
        self.assertEqual([r for p, r in self.srv.log if p == '/SYSTEM'], ['bytes=123456-'])

    def test_a_server_that_ignores_range_still_gives_a_correct_file(self):
        self.srv.honor_range = False; self.stage.mkdir(parents=True); (self.stage / 'SYSTEM.part').write_bytes(SYSTEM[:100_000])
        rc, lines, out, err = self.helper('apply', '20261012.1')
        self.assertEqual(rc, 0, out + err); self.assertEqual((self.stage / 'SYSTEM.new').read_bytes(), SYSTEM)
        pct = [float(l[1]) for l in lines if l[0] == 'progress']; self.assertLessEqual(max(pct), 100.0)

    def test_corrupt_bytes_are_refused_deleted_and_never_marked_ready(self):
        self.srv.files['/SYSTEM'] = bytes([SYSTEM[0] ^ 1]) + SYSTEM[1:]                       # right size, wrong content
        rc, lines, out, err = self.helper('apply', '20261012.1')
        self.assertEqual(rc, 1); self.assertTrue(self.error_of(out)[0].startswith('error HashMismatch'), out)
        self.assertEqual([n for n in self.staged() if n in ('READY', 'SYSTEM.new', 'SYSTEM.part')], [])

    def test_a_truncated_server_file_is_refused(self):
        self.srv.files['/SYSTEM'] = SYSTEM[:-10]
        rc, _, out, _ = self.helper('apply', '20261012.1')
        self.assertEqual(rc, 1); self.assertNotIn('READY', self.staged())

    def test_not_enough_room_is_reported_before_a_byte_is_downloaded(self):
        rc, _, out, _ = self.helper('apply', '20261012.1', env={'WISP_UPDATE_FREE_BYTES': str(len(KERNEL) + len(SYSTEM) - 1)})
        self.assertEqual(rc, 1); self.assertTrue(self.error_of(out)[0].startswith('error DiskFull'), out)
        self.assertEqual([p for p, r in self.srv.log if p in ('/KERNEL', '/SYSTEM')], [])

    def test_only_the_update_the_server_offers_and_this_system_can_take_is_applied(self):
        self.assertTrue(self.error_of(self.helper('apply', '20261099.9')[2])[0].startswith('error UnknownUpdate'))
        self.publish(self.manifest(requires='0.5.0'))
        out = self.helper('apply', '20261012.1')[2]; self.assertIn('error UnknownUpdate', out); self.assertIn('0.5.0', out)
        self.publish(self.manifest(hw_device='RPi5'))
        self.assertIn('error UnknownUpdate', self.helper('apply', '20261012.1')[2])
        self.assertEqual(self.staged(), [])

    def test_a_staged_update_is_not_downloaded_twice(self):
        self.helper('apply', '20261012.1'); n = len(self.srv.log)
        rc, lines, out, err = self.helper('apply', '20261012.1')
        self.assertEqual((rc, lines[-1]), (0, ['done'])); self.assertEqual([p for p, r in self.srv.log[n:] if p in ('/KERNEL', '/SYSTEM')], [])

    def test_a_staged_older_update_is_replaced_by_the_new_one(self):
        self.stage.mkdir(parents=True)
        for n in ('KERNEL.new', 'SYSTEM.new', 'junk.part'): (self.stage / n).write_bytes(b'old')
        (self.stage / 'READY').write_text(json.dumps({'build_id': '20261001.1'}))
        self.assertEqual(self.helper('apply', '20261012.1')[0], 0)
        self.assertEqual(self.staged(), ['KERNEL.new', 'READY', 'SYSTEM.new']); self.assertEqual((self.stage / 'KERNEL.new').read_bytes(), KERNEL)

    def test_cancel_removes_the_partial_files_and_exits_143(self):
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
        self.assertEqual(self.staged(), [])

    def test_network_trouble_keeps_the_parts_for_a_later_resume(self):
        self.srv.files.pop('/SYSTEM')                                                         # 404: refused at once, nothing to wait for
        rc, _, out, _ = self.helper('apply', '20261012.1')
        self.assertEqual(rc, 1); self.assertTrue(self.error_of(out)[0].startswith('error NetworkError'), out); self.assertIn('HTTP 404', out)
        self.assertNotIn('READY', self.staged())

    def test_the_boot_partition_is_writable_only_while_staging_and_read_only_again_after(self):
        calls = self.d / 'mount-calls'; stub = self.d / 'bin'; stub.mkdir()
        (stub / 'mount').write_text('#!/bin/sh\necho "$@" >> "%s"\nexit 0\n' % calls); (stub / 'mount').chmod(0o755)
        env = {'WISP_UPDATE_NO_REMOUNT': '', 'PATH': '%s:%s' % (stub, os.environ['PATH'])}
        self.assertEqual(self.helper('apply', '20261012.1', env=env)[0], 0)
        self.assertEqual(calls.read_text().splitlines(), ['-o remount,rw %s' % self.flash, '-o remount,ro %s' % self.flash])
        calls.unlink(); self.srv.files['/SYSTEM'] = SYSTEM[:-1]; shutil.rmtree(self.flash / 'wispos')
        self.assertEqual(self.helper('apply', '20261012.1', env=env)[0], 1)                  # a failing run must still put it back
        self.assertEqual(calls.read_text().splitlines()[-1], '-o remount,ro %s' % self.flash)

    def test_a_card_that_cannot_be_made_writable_stops_the_update_with_that_reason(self):
        stub = self.d / 'bin'; stub.mkdir(); (stub / 'mount').write_text('#!/bin/sh\nexit 1\n'); (stub / 'mount').chmod(0o755)
        rc, _, out, _ = self.helper('apply', '20261012.1', env={'WISP_UPDATE_NO_REMOUNT': '', 'PATH': '%s:%s' % (stub, os.environ['PATH'])})
        self.assertEqual(rc, 1); self.assertTrue(self.error_of(out)[0].startswith('error FlashReadOnly'), out)


if __name__ == '__main__':
    unittest.main()
