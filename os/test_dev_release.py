"""os/dev/test-release.sh and os/dev/serve_release.py: the signed TEST release of the current candidate that a dev image installs from the LAN (docs/WISPOS-NOTES.md "Next device session").

Run on a synthetic work directory (a tiny SYSTEM squashfs, a stand-in kernel, a throwaway signing key), so nothing real is touched and the test takes seconds:
 - the restamped SYSTEM differs from the candidate's in /etc/os-release and nowhere else (every other file identical), and says the new build and version;
 - --broken additionally lacks the init (the initramfs then stops at "Could not find systemd" and the card goes back to the old slot), and still differs nowhere else;
 - the manifest is signed, its hashes match the files, and the REAL helper (os/steamos/wisp-update) on a fake dev image installs it from the served directory over plain http, while a
   fake release image (no DEV-BUILD marker) does not even try;
 - the server answers Range requests the way a resume needs (206 and Content-Range), serves only the files of its directory and nothing outside it.
Needs fakeroot, unsquashfs/mksquashfs and openssl; skipped with the reason when one is missing. The work directory must be on the HDD (the script insists)."""
import hashlib, importlib.util, json, os, shutil, subprocess, sys, tempfile, threading, unittest, urllib.request, urllib.error
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))
SCRIPT = HERE / 'dev/test-release.sh'
SERVER = HERE / 'dev/serve_release.py'
HELPER = HERE / 'steamos/wisp-update'
LOCK = json.loads((HERE.parent / 'manifest/lockfile.json').read_text())
OS_RELEASE = '''NAME="WispOS"
ID="wispos"
PRETTY_NAME="WispOS {version}"
VERSION_ID="{version}"
BUILD_ID="{build}"
WISPOS_VERSION="{version}"
HW_DEVICE="H700"
'''


def missing():
    if not str(WORK).startswith('/mnt/hdd/'): return 'the work directory must be on the HDD (WISP_WORK=%s)' % WORK
    for tool in ('fakeroot', 'unsquashfs', 'mksquashfs', 'openssl'):
        if not shutil.which(tool): return 'no ' + tool
    return None


def run(*cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


def tree_of(squashfs, dest):
    r = run('fakeroot', '--', 'unsquashfs', '-no-xattrs', '-f', '-q', '-d', str(dest), str(squashfs)); assert r.returncode == 0, r.stderr
    return {str(p.relative_to(dest)): (p.read_bytes() if p.is_file() and not p.is_symlink() else os.readlink(p) if p.is_symlink() else None) for p in sorted(Path(dest).rglob('*'))}


def load_server():
    spec = importlib.util.spec_from_file_location('serve_release', SERVER); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


@unittest.skipIf(missing(), missing() or '')
class TestReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
        cls.d = Path(tempfile.mkdtemp(prefix='wispos-testrel-', dir=WORK / 'tmp'))
        cls.work = cls.d / 'work'
        tree = cls.work / 'rootfs/tree'
        for rel, data in (('etc/os-release', OS_RELEASE.format(version=LOCK['version'], build=LOCK['build_id'])), ('usr/lib/systemd/systemd', 'init\n'), ('usr/lib/wispos/other', os.urandom(3000).hex()),
                          ('usr/bin/tool', '#!/bin/sh\n')):
            p = tree / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(data)
        (tree / 'usr/bin/link').symlink_to('tool')
        (cls.work / 'out/kernel').mkdir(parents=True); img = bytearray(os.urandom(50_000)); img[56:60] = b'ARM\x64'; (cls.work / 'out/kernel/Image').write_bytes(bytes(img))      # an arm64 Image header
        r = run('mksquashfs', str(tree), str(cls.work / 'out/SYSTEM'), '-noappend', '-quiet', '-no-xattrs', '-comp', 'gzip', '-all-root'); assert r.returncode == 0, r.stderr
        cls.key, cls.pub = cls.d / 'key.pem', cls.d / 'pub.pem'
        assert run('openssl', 'genpkey', '-algorithm', 'ED25519', '-out', str(cls.key)).returncode == 0; os.chmod(cls.key, 0o600)
        assert run('openssl', 'pkey', '-in', str(cls.key), '-pubout', '-out', str(cls.pub)).returncode == 0

    @classmethod
    def tearDownClass(cls): shutil.rmtree(cls.d, True)

    def make(self, *args, port='18765'):
        env = {**os.environ, 'WISP_RELEASE_KEY': str(self.key), 'WISP_BAKED_PUBLIC': str(self.pub), 'TMPDIR': str(WORK / 'tmp')}
        shutil.rmtree(self.work / 'test-release', True)
        return run('bash', str(SCRIPT), '--no-serve', '--address', '127.0.0.1', '--port', port, *args, str(self.work), env=env, timeout=300)

    def release_dir(self, name): return self.work / 'test-release' / name / 'release'

    def test_the_restamped_system_differs_from_the_candidate_in_os_release_only(self):
        r = self.make(); self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        new_build = '%s.%d' % (LOCK['build_id'].split('.')[0], int(LOCK['build_id'].split('.')[1]) + 1)
        new_version = '%s.%d' % (LOCK['version'].rsplit('.', 1)[0], int(LOCK['version'].rsplit('.', 1)[1]) + 1)
        rel = self.release_dir(new_build); old = tree_of(self.work / 'out/SYSTEM', self.d / 'old'); new = tree_of(rel / 'SYSTEM', self.d / 'new')
        self.assertEqual(set(old), set(new)); self.assertEqual([k for k in old if old[k] != new[k]], ['etc/os-release'])
        text = new['etc/os-release'].decode()
        for line in ('BUILD_ID="%s"' % new_build, 'WISPOS_VERSION="%s"' % new_version, 'VERSION_ID="%s"' % new_version, 'PRETTY_NAME="WispOS %s"' % new_version, 'HW_DEVICE="H700"'): self.assertIn(line, text)
        m = json.loads((rel / 'manifest.json').read_text())
        self.assertEqual((m['build_id'], m['version']), (new_build, new_version)); self.assertEqual(m['files']['KERNEL']['url'], 'http://127.0.0.1:18765/KERNEL')
        for name in ('KERNEL', 'SYSTEM'): self.assertEqual(m['files'][name]['sha256'], hashlib.sha256((rel / name).read_bytes()).hexdigest())
        self.assertEqual((rel / 'KERNEL').read_bytes(), (self.work / 'out/kernel/Image').read_bytes())          # the candidate's own kernel

    def test_the_broken_one_lacks_the_init_and_nothing_else(self):
        r = self.make('--broken'); self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rel = next((self.work / 'test-release').glob('*-broken')) / 'release'
        old = tree_of(self.work / 'out/SYSTEM', self.d / 'old2'); new = tree_of(rel / 'SYSTEM', self.d / 'new2')
        self.assertEqual(sorted(set(old) - set(new)), ['usr/lib/systemd/systemd']); self.assertEqual(sorted(set(new) - set(old)), [])
        self.assertEqual(sorted(k for k in new if old[k] != new[k]), ['etc/os-release'])

    def test_the_dead_kernel_differs_from_the_candidates_in_its_first_instruction_only(self):
        r = self.make('--dead-kernel'); self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rel = next((self.work / 'test-release').glob('*-dead-kernel')) / 'release'; real = (self.work / 'out/kernel/Image').read_bytes(); dead = (rel / 'KERNEL').read_bytes()
        self.assertEqual(len(dead), len(real)); self.assertEqual(dead[4:], real[4:]); self.assertEqual(dead[:4], bytes.fromhex('00000014'))        # b . (little endian 0x14000000)
        self.assertEqual(dead[56:60], b'ARM\x64')                                                                                              # still a Linux Image: it loads
        self.assertEqual(tree_of(rel / 'SYSTEM', self.d / 'dk')['etc/os-release'].count(b'BUILD_ID'), 1)                                      # the system is the restamped one
        m = json.loads((rel / 'manifest.json').read_text()); self.assertEqual(m['files']['KERNEL']['sha256'], hashlib.sha256(dead).hexdigest())
        self.assertFalse((rel.parent / 'Image.dead').exists())

    def test_the_two_experiments_are_exclusive_and_a_non_image_kernel_is_refused(self):
        self.assertEqual(self.make('--broken', '--dead-kernel').returncode, 2)
        (self.work / 'out/kernel/Image.keep').write_bytes((self.work / 'out/kernel/Image').read_bytes()); (self.work / 'out/kernel/Image').write_bytes(b'x' * 100)
        try: self.assertNotEqual(self.make('--dead-kernel').returncode, 0)
        finally: shutil.move(str(self.work / 'out/kernel/Image.keep'), str(self.work / 'out/kernel/Image'))

    def test_explicit_ids_are_validated_before_anything_is_deleted_or_written(self):
        (self.work / 'x').mkdir(parents=True, exist_ok=True); (self.work / 'x/keep').write_text('x')                      # what a build id of "../x" would make the old clean-up delete
        for args in (('--build-id', '../x'), ('--build-id', '1.2'), ('--version', '1.2'), ('--version', 'x.y.z')):
            env = {**os.environ, 'WISP_RELEASE_KEY': str(self.key), 'WISP_BAKED_PUBLIC': str(self.pub), 'TMPDIR': str(WORK / 'tmp')}
            r = run('bash', str(SCRIPT), '--no-serve', '--address', '127.0.0.1', *args, str(self.work), env=env)
            self.assertEqual(r.returncode, 2, (args, r.stdout, r.stderr)); self.assertTrue((self.work / 'x/keep').exists(), args)

    def test_a_work_directory_off_the_hdd_is_refused(self):
        r = run('bash', str(SCRIPT), '--no-serve', '/tmp'); self.assertEqual(r.returncode, 2); self.assertIn('HDD', r.stderr)

    def serve(self, directory, port=0):
        mod = load_server(); mod.Handler.root = Path(directory).resolve(); mod.Handler.log_message = lambda *a: None                 # quiet: the access log is for the person running the server
        import http.server
        srv = http.server.ThreadingHTTPServer(('127.0.0.1', port), mod.Handler); srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start(); self.addCleanup(srv.server_close); self.addCleanup(srv.shutdown)
        return 'http://127.0.0.1:%d' % srv.server_address[1]

    def test_the_real_helper_on_a_dev_image_installs_it_and_a_release_image_does_not_try(self):
        import socket
        with socket.socket() as probe: probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]               # the manifest names the port the files will be served on
        r = self.make(port=str(port)); self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rel = next((self.work / 'test-release').glob('*/release'))                                           # the one release the run made
        base = self.serve(rel, port)
        flash = self.d / 'flash'; shutil.rmtree(flash, True); (flash / 'extlinux').mkdir(parents=True)
        (flash / 'extlinux/extlinux.conf').write_text('DEFAULT a\nLABEL a\n  LINUX /KERNEL\n  APPEND quiet wisp.slot=a\nLABEL b\n  LINUX /wispos/b/KERNEL\n  APPEND quiet wisp.slot=b\n')
        (flash / 'KERNEL').write_bytes(b'old'); (flash / 'SYSTEM').write_bytes(b'old')
        (self.d / 'cmdline').write_text('quiet wisp.slot=a\n'); (self.d / 'os-release').write_text(OS_RELEASE.format(version=LOCK['version'], build=LOCK['build_id']))
        (self.d / 'update.conf').write_text('{}'); mark, devconf = self.d / 'DEV-BUILD', self.d / 'update-dev.json'
        devconf.write_text(json.dumps({'manifest_url': base + '/manifest.json', 'allow_http': True}))
        env = {**os.environ, 'WISP_UPDATE_CONF': str(self.d / 'update.conf'), 'WISP_UPDATE_PUBKEY': str(self.pub), 'WISP_OS_RELEASE': str(self.d / 'os-release'), 'WISP_FLASH_DIR': str(flash),
               'WISP_CMDLINE': str(self.d / 'cmdline'), 'WISP_UPDATE_ALLOW_HTTP': '0', 'WISP_UPDATE_NO_REMOUNT': '1', 'WISP_UPDATE_FREE_MARGIN': '0', 'WISP_UPDATE_NET_TIMEOUT': '10', 'WISP_UPDATE_RETRIES': '1',
               'WISP_DEV_MARK': str(mark), 'WISP_UPDATE_DEV_CONF': str(devconf), 'TMPDIR': str(WORK / 'tmp')}
        def helper(*a): return run('python3', str(HELPER), *a, env=env)
        r = helper('check'); self.assertEqual(r.returncode, 1); self.assertIn('NoUpdateSource', r.stdout)                  # a release image (no marker): the file is never read
        mark.write_text('')
        r = helper('check'); self.assertEqual(r.returncode, 0, r.stdout + r.stderr); build = r.stdout.split()[1]
        self.assertTrue(r.stdout.startswith('update %s ' % build))
        r = helper('apply', build); self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((flash / 'wispos/b/KERNEL').read_bytes(), (rel / 'KERNEL').read_bytes()); self.assertEqual((flash / 'wispos/b/SYSTEM').read_bytes(), (rel / 'SYSTEM').read_bytes())
        self.assertEqual((flash / 'extlinux/extlinux.conf').read_text().splitlines()[0], 'DEFAULT b'); self.assertEqual((flash / 'KERNEL').read_bytes(), b'old')

    def test_the_server_resumes_and_stays_inside_its_directory(self):
        d = self.d / 'serve'; shutil.rmtree(d, True); d.mkdir(); data = os.urandom(5000); (d / 'file').write_bytes(data); (self.d / 'secret').write_text('no')
        os.symlink(self.d / 'secret', d / 'link')
        base = self.serve(d)
        with urllib.request.urlopen(base + '/file') as r: self.assertEqual((r.status, r.read(), r.headers['Accept-Ranges']), (200, data, 'bytes'))
        req = urllib.request.Request(base + '/file', headers={'Range': 'bytes=1234-'})
        with urllib.request.urlopen(req) as r: self.assertEqual((r.status, r.read(), r.headers['Content-Range']), (206, data[1234:], 'bytes 1234-4999/5000'))
        for rng, want in (('bytes=0-99', data), ('bytes=-500', data), ('garbage', data)):                          # forms a resume never sends: answered with the whole file
            with urllib.request.urlopen(urllib.request.Request(base + '/file', headers={'Range': rng})) as r: self.assertEqual((r.status, r.read()), (200, want), rng)
        with self.assertRaises(urllib.error.HTTPError) as e: urllib.request.urlopen(urllib.request.Request(base + '/file', headers={'Range': 'bytes=5000-'}))
        self.assertEqual((e.exception.code, e.exception.headers['Content-Range']), (416, 'bytes */5000'))            # asking for more than there is
        for path in ('/missing', '/../secret', '/%2e%2e/secret', '//etc/passwd', '/sub/file', '/link', '/'):
            with self.assertRaises(urllib.error.HTTPError, msg=path) as e: urllib.request.urlopen(base + path)
            self.assertEqual(e.exception.code, 404, path)


MUTANTS = [
    ('BUILD_ID is not restamped', '("BUILD_ID", build), ', ''),
    ('the broken variant keeps the init', '[ -z "$BROKEN" ] || rm -f "$T/tree-parent/tree/usr/lib/systemd/systemd"', ':'),
    ('the new build id is not newer', 'BUILD=${CUR_BUILD%.*}.$(( ${CUR_BUILD##*.} + 1 ))', 'BUILD=$CUR_BUILD'),
    ('the dead kernel is not dead', 'data[0:4] = (0x14000000).to_bytes(4, \'little\')', 'data[0:4] = data[0:4]'),
    ('the dead kernel is not checked to be an Image', "assert data[56:60] == b'ARM\\x64', 'not an arm64 Image'", 'pass'),
    ('both experiments at once', '[ -z "$BROKEN" ] || [ -z "$DEAD" ] ||', 'false ||'),
    ('the id check is gone', '[[ $BUILD =~ ^[0-9]{8}\\.[0-9]+$ ]] ||', 'true ||'),
    ('the work directory may be anywhere', 'case "$WORK" in /mnt/hdd/*) ;;', 'case "$WORK" in /*) ;;'),
]


class Mutants(unittest.TestCase):
    """The script changed in turn (a copy, with the original's location kept for its helpers): the tests above must fail every time."""
    @unittest.skipIf(missing(), missing() or '')
    def test_every_mutant_is_caught(self):
        if os.environ.get('WISP_TESTREL_MUTANT'): self.skipTest('running a mutant already')
        import concurrent.futures
        tmp = Path(tempfile.mkdtemp(prefix='wispos-testrel-mut-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, tmp, True)
        text = SCRIPT.read_text(); jobs = []
        for i, (what, old, new) in enumerate(MUTANTS):
            self.assertEqual(text.count(old), 1, 'mutant %r: the pattern must occur exactly once' % what)
            root = tmp / str(i); (root / 'os/dev').mkdir(parents=True)                                              # a mini repository root: the script finds its helpers relative to itself
            (root / 'os/make-release.py').symlink_to(HERE / 'make-release.py'); (root / 'os/dev/serve_release.py').symlink_to(SERVER); (root / 'manifest').symlink_to(HERE.parent / 'manifest')
            m = root / 'os/dev/test-release.sh'; m.write_text(text.replace(old, new)); jobs.append((what, m))
        def go(job):
            what, m = job
            env = {**os.environ, 'WISP_TESTREL_MUTANT': str(m)}
            r = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), 'TestReleaseTests'], env=env, capture_output=True, text=True, timeout=1800)
            return what, r.returncode
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex: results = list(ex.map(go, jobs))
        self.assertEqual([w for w, rc in results if rc == 0], [], 'mutants that no test noticed')


if os.environ.get('WISP_TESTREL_MUTANT'): SCRIPT = Path(os.environ['WISP_TESTREL_MUTANT'])


if __name__ == '__main__':
    unittest.main()
