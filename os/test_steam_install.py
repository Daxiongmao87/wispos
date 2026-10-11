"""steam-install: download, resume, verification, extraction and the READY handoff, against a local server with synthetic components."""
import hashlib, http.server, importlib.machinery, importlib.util, io, json, os, shutil, struct, subprocess, sys, tarfile, tempfile, threading, types, unittest, zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
INSTALLER = HERE / 'rootfs/usr/lib/wispos/steam-install'
EXTRACTOR = HERE.parent / 'steam/native-client.py'
OLD = b'exec taskset 0x7c $(pwd)/steamwebhelper "$@" &> ~/.steam/steam/logs/steamwebhelper.log'


def aarch64_elf():
    header = bytearray(b'\x7fELF\x02\x01\x01' + bytes(57)); header[18:20] = struct.pack('<H', 183)
    return bytes(header)


def make_zip(path, files):
    with zipfile.ZipFile(path, 'w') as z:
        for name, data in files.items():
            z.writestr(name, data)


class Server(http.server.ThreadingHTTPServer):
    """Serves a directory with Range support. cut_after: close the connection after that many body bytes once. ignore_range: always 200."""
    def __init__(self, root):
        self.root, self.cut_after, self.ignore_range, self.requests, self.cuts = root, None, False, [], 0
        self.cut_suffixes = ('.zip', '.zst')               # which downloads a cut may hit (the first matching one)
        super().__init__(('127.0.0.1', 0), self.Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            srv = self.server; self.server.requests.append((self.path, self.headers.get('Range')))
            path = srv.root / self.path.lstrip('/')
            if not path.is_file(): self.send_error(404); return
            data = path.read_bytes(); start = 0; rng = self.headers.get('Range')
            if rng and not srv.ignore_range:
                start = int(rng.split('=')[1].split('-')[0])
                if start >= len(data): self.send_error(416); return
                self.send_response(206); self.send_header('Content-Range', f'bytes {start}-{len(data)-1}/{len(data)}')
            else:
                self.send_response(200)
            body = data[start:]
            self.send_header('Content-Length', str(len(body))); self.end_headers()
            if srv.cut_after is not None and path.suffix in srv.cut_suffixes:
                cut, srv.cut_after = srv.cut_after, None; srv.cuts += 1
                self.wfile.write(body[:cut]); self.wfile.flush(); self.connection.close(); return
            self.wfile.write(body)


class Fixture(unittest.TestCase):
    """The shared setup: a local server with synthetic Steam components (and, on request, a synthetic compatibility tool), the installer copied as the image has it."""
    TOOL = 'Proton-Test-v80'

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='wispos-steam-install-', dir=os.environ.get('TMPDIR')))
        self.web = self.tmp / 'web'; self.web.mkdir()
        self.lib = self.tmp / 'lib'; self.lib.mkdir()                       # what /usr/lib/wispos holds in the image
        shutil.copy(INSTALLER, self.lib / 'steam-install'); shutil.copy(EXTRACTOR, self.lib / 'native_client.py')
        wrapper = b'#!/bin/bash\n' + OLD + b'\n'
        pad = os.urandom(600_000)                                            # big enough that a cut lands mid-file
        self.parts = {'bins': {'steamrtarm64/steam': aarch64_elf(), 'steamrtarm64/steamwebhelper': aarch64_elf(), 'steamrtarm64/steamclient.so': aarch64_elf(),
                               'steamrtarm64/steamwebhelper.sh': wrapper, 'steamrtarm64/blob.bin': pad},
                      'ui': {'steamui/index.html': b'<html></html>'}}
        components = []
        for name, files in self.parts.items():
            zp = self.web / f'{name}.zip'; make_zip(zp, files); data = zp.read_bytes()
            components.append({'name': name, 'url': None, 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data), 'file': zp.name})
        (self.web / 'manifest').write_bytes(b'"linuxarm64"\n')
        self.components = components
        self.server = Server(self.web); self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.write_lock()
        self.runtime, self.cache = self.tmp / 'runtime', self.tmp / 'cache'
        self.tool_lock = self.tmp / 'no-tool-lock.json'                     # absent = no tool step (an image from before the tool, and every Steam-only test)

    def make_tool(self, files=None, entries=(), name=None):
        """Build web/tool.tar.zst like the real archive (a './' entry, then everything under <name>/, relative symlinks) and write the tool lock for it.
        files: {path inside <name>: bytes}; entries: extra raw TarInfo(+data) objects appended as they are (for hostile archives)."""
        name = name or self.TOOL
        files = files if files is not None else {'proton': b'#!/usr/bin/python3\n', 'compatibilitytool.vdf': b'"compatibilitytools"\n', 'version': b'1 test\n',
                                                'files/bin/wine': os.urandom(600_000)}          # big enough that a cut lands mid-file
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w') as tar:
            root = tarfile.TarInfo('./'); root.type = tarfile.DIRTYPE; root.mode = 0o755; tar.addfile(root)
            for path, data in files.items():
                info = tarfile.TarInfo(f'{name}/{path}'); info.size = len(data); info.mode = 0o755 if path in ('proton', 'files/bin/wine') else 0o644
                tar.addfile(info, io.BytesIO(data))
            link = tarfile.TarInfo(f'{name}/files/bin/alias'); link.type = tarfile.SYMTYPE; link.linkname = 'wine'; tar.addfile(link)
            port = tarfile.TarInfo(f'{name}/files/share/pfx/dosdevices/com1'); port.type = tarfile.SYMTYPE; port.linkname = '/dev/ttyS0'; tar.addfile(port)   # the real archive has 32 of these
            for info, data in entries:
                tar.addfile(info, io.BytesIO(data) if data is not None else None)
        packed = subprocess.run(['zstd', '-q', '-c'], input=buffer.getvalue(), capture_output=True, check=True).stdout
        (self.web / 'tool.tar.zst').write_bytes(packed)
        self.tool_sha = hashlib.sha256(packed).hexdigest(); self.tool_size = len(packed)
        self.write_tool_lock()

    def write_tool_lock(self, sha=None):
        self.tool_lock = self.tmp / 'tool-lock.json'
        self.tool_lock.write_text(json.dumps({'schema': 1, 'name': self.TOOL, 'url': f'{self.base}/tool.tar.zst', 'size': self.tool_size,
                                              'sha256': sha or self.tool_sha, 'format': 'tar.zst'}))

    def write_lock(self, tamper=None):
        comps = [{**{k: v for k, v in c.items() if k != 'file'}, 'url': f'{self.base}/{c["file"]}'} for c in self.components]
        if tamper: comps[0]['sha256'] = '0' * 64
        self.lock = self.tmp / 'lock.json'
        self.lock.write_text(json.dumps({'schema': 1, 'version': '42', 'manifest_url': f'{self.base}/manifest',
                                         'manifest_sha256': hashlib.sha256((self.web / 'manifest').read_bytes()).hexdigest(), 'components': comps}))

    def tearDown(self): self.server.shutdown(); self.server.server_close(); shutil.rmtree(self.tmp, ignore_errors=True)

    def run_installer(self, *extra):
        out = subprocess.run([sys.executable, '-I', str(self.lib / 'steam-install'), '--lock', str(self.lock), '--runtime', str(self.runtime),
                              '--cache', str(self.cache), '--min-free-mib', '1', '--tool-lock', str(self.tool_lock), '--tool-min-free-mib', '1', *extra],
                             capture_output=True, text=True)
        return out.returncode, out.stdout.splitlines()



class InstallTests(Fixture):
    def test_full_install_extracts_patches_and_writes_ready_last(self):
        code, lines = self.run_installer()
        self.assertEqual(code, 0, lines); self.assertEqual(lines[-1], 'done')
        self.assertEqual([l for l in lines if l.startswith('phase')], ['phase download', 'phase extract', 'phase finish'])
        self.assertTrue((self.runtime / 'READY').exists())
        steam = self.runtime / 'steam'
        self.assertTrue((steam / 'steamrtarm64/steam').exists() and os.access(steam / 'steamrtarm64/steam', os.X_OK))
        wrapper = (steam / 'steamrtarm64/steamwebhelper.sh').read_bytes()
        self.assertNotIn(b'taskset', wrapper); self.assertIn(b'exec "$(pwd)/steamwebhelper"', wrapper)
        self.assertEqual(json.loads((self.runtime / 'meta/native-client.lock.json').read_text())['version'], '42')
        self.assertFalse(self.cache.exists(), 'the cache is removed after a successful install')
        self.assertFalse((self.runtime / 'steam.installing').exists())
        total = sum(c['size'] for c in self.components)
        progress = [l.split() for l in lines if l.startswith('progress')]
        self.assertEqual(progress[-1][1:3], [str(total), str(total)])
        self.assertTrue(all(int(a[1]) <= int(b[1]) for a, b in zip(progress, progress[1:])), 'progress never goes backwards')

    def test_an_interrupted_download_resumes_with_a_range_request(self):
        self.server.cut_after = 200_000
        code, lines = self.run_installer()
        self.assertEqual(self.server.cuts, 1, 'the server really dropped the connection')
        self.assertEqual(code, 1); self.assertTrue(any(l.startswith('error') for l in lines)); self.assertFalse((self.runtime / 'READY').exists())
        self.assertTrue(list(self.cache.glob('*.part')), 'the partial file is kept')
        code, lines = self.run_installer()
        self.assertEqual(code, 0, lines)
        self.assertTrue(any(r and r.startswith('bytes=') and not r.endswith('=0-') for _, r in self.server.requests), self.server.requests)

    def test_a_server_that_ignores_range_restarts_the_file_and_still_verifies(self):
        self.server.cut_after = 150_000
        self.run_installer()
        self.assertEqual(self.server.cuts, 1)
        self.server.ignore_range = True
        code, lines = self.run_installer()
        self.assertEqual(code, 0, lines); self.assertTrue((self.runtime / 'READY').exists())

    def test_a_corrupt_download_is_discarded_and_nothing_is_marked_ready(self):
        self.write_lock(tamper=True)
        code, lines = self.run_installer()
        self.assertEqual(code, 1); self.assertTrue(any('failed its check' in l for l in lines), lines)
        self.assertFalse((self.runtime / 'READY').exists()); self.assertFalse((self.runtime / 'steam').exists())
        self.assertFalse(list(self.cache.glob('*.part')) or [p for p in self.cache.glob('*.zip')], 'the bad file is not kept')

    def test_the_install_does_not_depend_on_steams_moving_manifest(self):
        # Valve's manifest URL always serves the current release, so its hash changes with every Steam update (it already did,
        # a week after the lock was made). The components are content-addressed and pinned by their own hashes; the install
        # must not need the manifest at all, so a changed or missing manifest must not break first-run installs.
        lock = json.loads(self.lock.read_text()); lock['manifest_sha256'] = '1' * 64; lock['manifest_url'] = f'{self.base}/no-such-manifest'
        self.lock.write_text(json.dumps(lock))
        code, lines = self.run_installer()
        self.assertEqual(code, 0, lines); self.assertTrue((self.runtime / 'READY').exists())
        self.assertNotIn('/no-such-manifest', [path for path, _ in self.server.requests]); self.assertNotIn('/manifest', [path for path, _ in self.server.requests])

    def test_running_again_when_installed_does_nothing(self):
        self.run_installer(); self.server.requests.clear()
        code, lines = self.run_installer()
        self.assertEqual((code, lines), (0, ['done'])); self.assertEqual(self.server.requests, [])

    def test_too_little_space_stops_before_downloading(self):
        code, lines = self.run_installer('--min-free-mib', str(1 << 40))
        self.assertEqual(code, 1); self.assertTrue(any('free space' in l for l in lines)); self.assertEqual(self.server.requests, [])

    def test_a_client_for_another_architecture_is_refused(self):
        bad = dict(self.parts['bins']); bad['steamrtarm64/steam'] = b'\x7fELF\x02\x01' + bytes(60)    # machine 0, not AArch64
        make_zip(self.web / 'bins.zip', bad); data = (self.web / 'bins.zip').read_bytes()
        self.components[0].update(sha256=hashlib.sha256(data).hexdigest(), size=len(data)); self.write_lock()
        code, lines = self.run_installer()
        self.assertEqual(code, 1); self.assertFalse((self.runtime / 'READY').exists())

    def test_the_wrapper_is_patched_exactly_once_or_the_install_fails(self):
        two = dict(self.parts['bins']); two['steamrtarm64/steamwebhelper.sh'] = (OLD + b'\n') * 2
        make_zip(self.web / 'bins.zip', two); data = (self.web / 'bins.zip').read_bytes()
        self.components[0].update(sha256=hashlib.sha256(data).hexdigest(), size=len(data)); self.write_lock()
        code, lines = self.run_installer()
        self.assertEqual(code, 1); self.assertFalse((self.runtime / 'READY').exists())

    def test_the_installer_has_no_unbounded_timeouts_and_uses_the_pinned_lock_only(self):
        text = INSTALLER.read_text()
        self.assertIn('timeout=60', text); self.assertNotIn('shell=True', text)


@unittest.skipUnless(shutil.which('zstd'), 'the zstd program is needed to build the synthetic tool archives')
class ToolInstallTests(Fixture):
    """The compatibility tool: a hash-pinned .tar.zst unpacked as a stream into Steam's compatibilitytools.d, in the same run as the client or alone."""

    def tool_dir(self): return self.runtime / 'steam' / 'compatibilitytools.d' / self.TOOL

    def assert_tool_absent(self):
        self.assertFalse(self.tool_dir().exists()); self.assertFalse((self.runtime / 'meta' / 'compat-tool.sha256').exists())
        self.assertFalse((self.runtime / 'tool.installing').exists(), 'no half-unpacked tree is left behind')

    def test_a_fresh_install_gets_steam_and_the_tool_and_reports_both_in_one_progress_bar(self):
        self.make_tool()
        code, lines = self.run_installer()
        self.assertEqual(code, 0, lines); self.assertEqual(lines[-1], 'done'); self.assertTrue((self.runtime / 'READY').exists())
        self.assertTrue(os.access(self.tool_dir() / 'proton', os.X_OK) and os.access(self.tool_dir() / 'files/bin/wine', os.X_OK))
        self.assertTrue((self.tool_dir() / 'compatibilitytool.vdf').is_file())
        self.assertEqual(os.readlink(self.tool_dir() / 'files/bin/alias'), 'wine', 'relative links inside the tree are kept')
        self.assertFalse((self.tool_dir() / 'files/share/pfx/dosdevices/com1').is_symlink(), "Wine's absolute COM-port links into /dev are skipped, not created")
        self.assertEqual((self.runtime / 'meta/compat-tool.sha256').read_text().strip(), self.tool_sha)
        self.assertFalse((self.runtime / 'tool.installing').exists()); self.assertFalse(self.cache.exists())
        total = sum(c['size'] for c in self.components) + self.tool_size
        progress = [l.split() for l in lines if l.startswith('progress')]
        self.assertEqual(progress[-1][1:3], [str(total), str(total)]); self.assertEqual(progress[-1][3], self.TOOL)
        self.assertTrue(all(int(a[1]) <= int(b[1]) for a, b in zip(progress, progress[1:])), 'progress never goes backwards')

    def test_a_device_that_has_steam_but_not_the_tool_downloads_only_the_tool(self):
        self.assertEqual(self.run_installer()[0], 0)                      # an install from before the tool existed
        before = (self.runtime / 'steam/steamrtarm64/steam').stat().st_mtime_ns; self.server.requests.clear()
        self.make_tool()
        code, lines = self.run_installer()
        self.assertEqual(code, 0, lines); self.assertEqual({path for path, _ in self.server.requests}, {'/tool.tar.zst'}, 'the client is not fetched again')
        self.assertEqual((self.runtime / 'steam/steamrtarm64/steam').stat().st_mtime_ns, before, 'and not touched')
        self.assertTrue((self.tool_dir() / 'proton').is_file()); self.assertTrue((self.runtime / 'READY').exists())
        progress = [l.split() for l in lines if l.startswith('progress')]
        self.assertEqual(progress[-1][1:3], [str(self.tool_size)] * 2)

    def test_running_again_with_everything_installed_does_nothing(self):
        self.make_tool(); self.run_installer(); self.server.requests.clear()
        code, lines = self.run_installer()
        self.assertEqual((code, lines), (0, ['done'])); self.assertEqual(self.server.requests, [])

    def test_a_new_pin_replaces_the_installed_tool(self):
        self.make_tool(); self.run_installer()
        self.make_tool({'proton': b'#!/usr/bin/python3\n# new\n', 'compatibilitytool.vdf': b'x\n'}); self.server.requests.clear()
        code, lines = self.run_installer()
        self.assertEqual(code, 0, lines); self.assertIn(b'# new', (self.tool_dir() / 'proton').read_bytes())
        self.assertEqual((self.runtime / 'meta/compat-tool.sha256').read_text().strip(), self.tool_sha)
        self.assertFalse((self.tool_dir() / 'files/bin/wine').exists(), 'nothing of the old tree survives next to the new one')

    def test_a_new_client_version_keeps_the_tool_and_downloads_only_the_client(self):
        # The tool lives in compatibilitytools.d, which a client replacement never touches (it used to be wiped with the whole Steam directory).
        self.make_tool(); self.run_installer()
        proton = self.tool_dir() / 'proton'; before = (proton.read_bytes(), proton.stat().st_mtime_ns)
        lock = json.loads(self.lock.read_text()); lock['version'] = '43'; self.lock.write_text(json.dumps(lock)); self.server.requests.clear()
        code, lines = self.run_installer()
        self.assertEqual(code, 0, lines); self.assertEqual((proton.read_bytes(), proton.stat().st_mtime_ns), before)
        self.assertNotIn('/tool.tar.zst', {path for path, _ in self.server.requests}, 'the tool is not downloaded again')
        self.assertEqual(json.loads((self.runtime / 'meta/native-client.lock.json').read_text())['version'], '43')

    def test_an_interrupted_tool_download_resumes(self):
        self.make_tool(); self.server.cut_after = 100_000; self.server.cut_suffixes = ('.zst',)
        code, lines = self.run_installer()
        self.assertEqual(self.server.cuts, 1); self.assertEqual(code, 1, lines); self.assertTrue(any(l.startswith('error') for l in lines))
        self.assertFalse((self.runtime / 'READY').exists(), 'a fresh install is not ready without its tool'); self.assertTrue(list(self.cache.glob('*.part')))
        code, lines = self.run_installer()
        self.assertEqual(code, 0, lines); self.assertTrue((self.tool_dir() / 'proton').is_file())
        self.assertTrue(any(r and r.startswith('bytes=') and p == '/tool.tar.zst' for p, r in self.server.requests), self.server.requests)

    def test_a_tool_that_fails_its_hash_is_discarded_and_an_installed_client_stays_ready(self):
        self.assertEqual(self.run_installer()[0], 0)
        self.make_tool(); self.write_tool_lock(sha='0' * 64)
        code, lines = self.run_installer()
        self.assertEqual(code, 1); self.assertTrue(any('failed its check' in l for l in lines), lines)
        self.assert_tool_absent(); self.assertTrue((self.runtime / 'READY').exists()); self.assertTrue((self.runtime / 'steam/steamrtarm64/steam').exists())
        self.assertFalse(list(self.cache.glob('tool*')), 'the bad file is not kept')

    def test_too_little_space_for_the_tool_stops_before_downloading_it(self):
        self.assertEqual(self.run_installer()[0], 0); self.server.requests.clear(); self.make_tool()
        code, lines = self.run_installer('--tool-min-free-mib', str(1 << 40))
        self.assertEqual(code, 1); self.assertTrue(any('free space' in l for l in lines)); self.assertEqual(self.server.requests, []); self.assert_tool_absent()

    def test_a_hostile_archive_is_refused_and_writes_nothing_outside_its_folder(self):
        def info(name, kind=tarfile.REGTYPE, link=None, size=0):
            i = tarfile.TarInfo(name); i.type = kind; i.size = size; i.mode = 0o644
            if link: i.linkname = link
            return i
        hostile = {'a path that climbs out': [(info(f'{self.TOOL}/../../evil', size=4), b'evil')],
                   'an absolute path': [(info('/tmp/wispos-evil', size=4), b'evil')],
                   'a link to an absolute path': [(info(f'{self.TOOL}/files/passwd', tarfile.SYMTYPE, link='/etc/passwd'), None)],
                   'a link that climbs out': [(info(f'{self.TOOL}/files/up', tarfile.SYMTYPE, link='../../../outside'), None)],
                   'a device file': [(info(f'{self.TOOL}/files/null', tarfile.CHRTYPE), None)],
                   'a second top-level folder': [(info('other/file', size=4), b'evil')]}
        for what, entries in hostile.items():
            with self.subTest(what):
                shutil.rmtree(self.runtime, ignore_errors=True); shutil.rmtree(self.cache, ignore_errors=True)
                self.make_tool(entries=entries)
                code, lines = self.run_installer()
                self.assertEqual(code, 1, (what, lines)); self.assertTrue(any(l.startswith('error') for l in lines), lines)
                self.assert_tool_absent(); self.assertFalse((self.runtime / 'READY').exists())
                self.assertFalse((self.tmp / 'evil').exists() or (self.runtime / 'evil').exists() or Path('/tmp/wispos-evil').exists())

    def test_an_archive_without_the_launcher_is_refused(self):
        self.make_tool({'compatibilitytool.vdf': b'x\n', 'version': b'1\n'})
        code, lines = self.run_installer()
        self.assertEqual(code, 1); self.assertTrue(any('not what this version' in l for l in lines), lines); self.assert_tool_absent()

    def test_python_314s_own_zstd_module_is_used_when_it_exists_and_the_program_only_otherwise(self):
        spec = importlib.util.spec_from_loader('steam_install_under_test', importlib.machinery.SourceFileLoader('steam_install_under_test', str(INSTALLER)))
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        calls = []
        fake = types.ModuleType('compression.zstd'); fake.open = lambda path, mode: (calls.append((str(path), mode)), io.BytesIO(b'payload'))[1]
        package = types.ModuleType('compression'); package.zstd = fake
        saved = {k: sys.modules.get(k) for k in ('compression', 'compression.zstd')}
        sys.modules['compression'], sys.modules['compression.zstd'] = package, fake
        try:
            with module.zstd_stream('/some/archive.tar.zst') as stream:
                self.assertEqual(stream.read(), b'payload')
        finally:
            for key, value in saved.items():
                if value is None: sys.modules.pop(key, None)
                else: sys.modules[key] = value
        self.assertEqual(calls, [('/some/archive.tar.zst', 'rb')], 'the module was used, not a subprocess')


class ClientReplacementTests(Fixture):
    """Replacing the client never deletes what the package does not own: runtime/steam is also Steam's own data directory (games, userdata, config, logs, other tools)."""
    OLD_ONLY, NEW_ONLY, CHANGED = 'steamrtarm64/old-only.txt', 'steamrtarm64/new-only.txt', 'steamrtarm64/blob.bin'

    def publish(self, version, add=None, drop=(), change=None, tamper=False):
        """Rebuild the 'bins' component with the given changes and point the lock at it (a new client release)."""
        files = dict(self.parts['bins']); files.update(add or {}); files.update(change or {})
        for name in drop: files.pop(name, None)           # (the base package never had the file a test added in an earlier release)
        make_zip(self.web / 'bins.zip', files); data = (self.web / 'bins.zip').read_bytes()
        self.components[0].update(sha256=hashlib.sha256(data).hexdigest(), size=len(data)); self.write_lock(tamper=tamper)
        lock = json.loads(self.lock.read_text()); lock['version'] = version; self.lock.write_text(json.dumps(lock))

    def add_environment(self):
        """Everything a running Steam and its user leave in the directory, none of it part of any package."""
        steam = self.runtime / 'steam'
        for rel, data in (('steamapps/common/Game/data.bin', os.urandom(300_000)), ('steamapps/appmanifest_1.acf', b'"AppState" {}\n'), ('userdata/1/config/localconfig.vdf', b'"UserLocalConfigStore" {}\n'),
                          ('config/config.vdf', b'"InstallConfigStore" {}\n'), ('logs/console_log.txt', b'log\n'), ('compatibilitytools.d/OtherTool/proton', b'#!/bin/sh\n'),
                          ('ssfn123456', os.urandom(100)), ('registry.vdf', b'"Registry" {}\n')):
            path = steam / rel; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        (steam / 'games-link').symlink_to('steamapps')

    def snapshot(self, only=None):
        """{relative path: (content hash or link target, mtime_ns)} of everything under runtime/steam (or only the paths given)."""
        steam, out = self.runtime / 'steam', {}
        for dirpath, dirnames, filenames in os.walk(steam):
            for name in filenames + [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]:
                path = Path(dirpath) / name; rel = str(path.relative_to(steam))
                if only is not None and not any(rel == o or rel.startswith(o + '/') for o in only): continue
                out[rel] = (os.readlink(path) if path.is_symlink() else hashlib.sha256(path.read_bytes()).hexdigest(), path.lstat().st_mtime_ns)
        return out

    ENV = ['steamapps', 'userdata', 'config', 'logs', 'compatibilitytools.d', 'ssfn123456', 'registry.vdf', 'games-link']

    def test_replacing_the_client_changes_only_the_client(self):
        self.publish('42', add={self.OLD_ONLY: b'from release 42\n'})
        self.assertEqual(self.run_installer()[0], 0); self.add_environment()
        environment = self.snapshot(self.ENV); self.assertGreater(len(environment), 7)
        new_blob = os.urandom(500_000)
        self.publish('43', drop=[self.OLD_ONLY], add={self.NEW_ONLY: b'from release 43\n'}, change={self.CHANGED: new_blob})
        code, lines = self.run_installer()
        self.assertEqual(code, 0, lines); self.assertEqual(lines[-1], 'done')
        self.assertEqual(self.snapshot(self.ENV), environment, 'games, user data, config, logs, other tools and root files are byte for byte and mtime for mtime unchanged')
        steam = self.runtime / 'steam'
        self.assertFalse((steam / self.OLD_ONLY).exists(), 'a file the old package shipped and the new one does not is removed')
        self.assertEqual((steam / self.NEW_ONLY).read_bytes(), b'from release 43\n'); self.assertEqual((steam / self.CHANGED).read_bytes(), new_blob)
        manifest = json.loads((self.runtime / 'meta/client-files.json').read_text())
        self.assertEqual(manifest['version'], '43'); self.assertIn(self.NEW_ONLY, manifest['files']); self.assertNotIn(self.OLD_ONLY, manifest['files'])
        self.assertTrue((self.runtime / 'READY').exists()); self.assertFalse((self.runtime / 'steam.installing').exists())
        self.assertEqual(json.loads((self.runtime / 'meta/native-client.lock.json').read_text())['version'], '43')

    def test_a_device_installed_before_the_manifest_existed_loses_nothing_either(self):
        self.publish('42', add={self.OLD_ONLY: b'from release 42\n'})
        self.assertEqual(self.run_installer()[0], 0); self.add_environment()
        (self.runtime / 'meta/client-files.json').unlink()                                    # how every earlier install looks
        environment = self.snapshot(self.ENV)
        self.publish('43', drop=[self.OLD_ONLY], add={self.NEW_ONLY: b'from release 43\n'})
        code, lines = self.run_installer()
        self.assertEqual(code, 0, lines); self.assertEqual(self.snapshot(self.ENV), environment)
        self.assertTrue((self.runtime / 'steam' / self.OLD_ONLY).exists(), 'without a record of what is ours nothing is deleted (a stale file is harmless, a lost game is not)')
        self.assertEqual((self.runtime / 'steam' / self.NEW_ONLY).read_bytes(), b'from release 43\n')
        self.assertIn(self.NEW_ONLY, json.loads((self.runtime / 'meta/client-files.json').read_text())['files'])

    def test_a_failed_replacement_changes_nothing_at_all(self):
        self.publish('42', add={self.OLD_ONLY: b'from release 42\n'})
        self.assertEqual(self.run_installer()[0], 0); self.add_environment()
        everything = self.snapshot()
        self.publish('43', drop=[self.OLD_ONLY], tamper=True)                                 # the download fails its hash check
        code, lines = self.run_installer()
        self.assertEqual(code, 1); self.assertTrue(any(l.startswith('error') for l in lines))
        self.assertEqual(self.snapshot(), everything); self.assertTrue((self.runtime / 'READY').exists())
        self.assertEqual(json.loads((self.runtime / 'meta/native-client.lock.json').read_text())['version'], '42')

    def test_a_package_file_that_would_replace_user_data_is_refused_before_anything_changes(self):
        self.publish('42', add={self.OLD_ONLY: b'from release 42\n'})
        self.assertEqual(self.run_installer()[0], 0); self.add_environment()
        everything = self.snapshot()
        for hostile in ({'steamapps': b'a file where the games folder is'}, {'userdata/1': b'a file where a user folder is'}, {'registry.vdf/inner': b'a path below a file'}):
            with self.subTest(list(hostile)):
                self.publish('43', drop=[self.OLD_ONLY], add=hostile)
                code, lines = self.run_installer()
                self.assertEqual(code, 1, lines); self.assertTrue(any('does not fit' in l for l in lines), lines)
                self.assertEqual(self.snapshot(), everything, 'not even the files the new package could have added or removed were touched')
                self.assertTrue((self.runtime / 'READY').exists()); self.assertTrue((self.runtime / 'steam' / self.OLD_ONLY).exists())

    def test_the_client_is_not_ready_while_it_is_being_replaced_and_ready_comes_last(self):
        spec = importlib.util.spec_from_loader('steam_install_replace', importlib.machinery.SourceFileLoader('steam_install_replace', str(INSTALLER)))
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        runtime = self.tmp / 'rt'; (runtime / 'steam').mkdir(parents=True); (runtime / 'staging' / 'steamrtarm64').mkdir(parents=True); (runtime / 'READY').write_text('Ready\n')
        (runtime / 'staging' / 'steamrtarm64' / 'steam').write_bytes(b'new'); lock = self.tmp / 'l.json'; lock.write_text('{"version": "9"}')
        seen, real = [], os.replace
        def spy(src, dst): seen.append((runtime / 'READY').exists()); return real(src, dst)
        module.os.replace = spy
        try: module.replace_client(runtime, runtime / 'staging', lock, '9')
        finally: module.os.replace = real
        self.assertTrue(seen and not any(seen), 'READY was gone for every move of the client files')
        self.assertFalse((runtime / 'READY').exists(), 'replace_client does not write READY: install() does, after everything else')
        self.assertEqual((runtime / 'steam' / 'steamrtarm64' / 'steam').read_bytes(), b'new')


if __name__ == '__main__':
    unittest.main()
