"""Tests for os/make-release.py: the producer side of the update path. The decisive test is the round trip: a release built here, served by a local HTTP server, is checked
and staged by the REAL device helper (os/steamos/wisp-update), and a release signed by another key, or edited after signing, is refused."""
import base64, json, os, shutil, stat, subprocess, tempfile, threading, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL, HELPER = HERE / 'make-release.py', HERE / 'steamos/wisp-update'
WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))
import sys; sys.path.insert(0, str(HERE))
import test_wisp_update as tw          # the local update server


class Base(unittest.TestCase):
    def setUp(self):
        if not str(WORK).startswith('/mnt/hdd/'): self.skipTest('the work directory must be on the HDD')
        (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
        self.d = Path(tempfile.mkdtemp(prefix='wispos-rel-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, self.d, True)
        self.priv, self.pub = self.d / 'keys/update-signing.pem', self.d / 'update-key.pub'
        self.env = {**os.environ, 'WISP_BAKED_PUBLIC': str(self.pub), 'TMPDIR': str(WORK / 'tmp')}
        self.kernel, self.system = self.d / 'Image', self.d / 'SYSTEM'
        self.kernel.write_bytes(os.urandom(200_000))
        self.make_system()

    def make_system(self, build_id='20261012.1', version='0.1.1', device='H700', path=None):
        """A real (small) squashfs whose /etc/os-release names the release: the tool reads the identity out of the image it signs."""
        src = self.d / 'sysroot'; shutil.rmtree(src, True); (src / 'etc').mkdir(parents=True); (src / 'blob').write_bytes(os.urandom(300_000))
        (src / 'etc/os-release').write_text('ID="wispos"\nBUILD_ID="%s"\nWISPOS_VERSION="%s"\nHW_DEVICE="%s"\n' % (build_id, version, device))
        out = Path(path or self.system); out.unlink(missing_ok=True)
        subprocess.run(['mksquashfs', str(src), str(out), '-quiet', '-no-progress', '-noappend', '-comp', 'gzip'], check=True, capture_output=True)

    def tree(self, conditional_ssh):
        import accounts
        t = self.d / ('tree-ok' if conditional_ssh else 'tree-gaps')
        if t.exists(): return t
        (t / 'etc/ssh').mkdir(parents=True); (t / 'usr/lib/systemd/system').mkdir(parents=True); (t / 'usr/share/wispos').mkdir(parents=True)
        (t / 'etc/shadow').write_text('root:*:1::::::\nwisp:$6$x$y:1::::::\n'); (t / 'etc/os-release').write_text('ID="wispos"\n'); (t / 'etc/ssh/sshd_config').write_text('PasswordAuthentication no\nPermitRootLogin no\n')
        (t / 'usr/lib/systemd/system/sshd.service').write_text('[Unit]\n' + ('ConditionPathExists=/storage/.config/wispos/ssh-enabled\n' if conditional_ssh else '') + '[Install]\nWantedBy=multi-user.target\n')
        (t / 'usr/lib/systemd/system/multi-user.target.wants').mkdir(); (t / 'usr/lib/systemd/system/multi-user.target.wants/sshd.service').symlink_to('../sshd.service')
        (t / 'usr/share/wispos/update.conf').write_text('{}'); (t / 'usr/share/wispos/update-key.pub').write_text('k')
        return t

    def tool(self, *args, check=True):
        r = subprocess.run(['python3', str(TOOL), *args], capture_output=True, text=True, env=self.env)
        if check: self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def keygen(self): return self.tool('keygen', '--private', str(self.priv), '--public', str(self.pub))

    def build(self, out, *extra, base='http://127.0.0.1:1', check=True):
        return self.tool('build', '--kernel', str(self.kernel), '--system', str(self.system), '--build-id', '20261012.1', '--version', '0.1.1', '--base-url', base,
                         '--out', str(out), '--key', str(self.priv), '--allow-http', '--tree', str(self.tree(True)), '--extlinux', '/nonexistent', *extra, check=check)


class KeyTests(Base):
    def test_keygen_makes_a_private_key_only_the_owner_can_read_and_never_overwrites_it(self):
        self.keygen()
        self.assertEqual(stat.S_IMODE(self.priv.stat().st_mode), 0o600); self.assertEqual(stat.S_IMODE(self.priv.parent.stat().st_mode), 0o700)
        self.assertIn('BEGIN PUBLIC KEY', self.pub.read_text()); self.assertNotIn('PRIVATE', self.pub.read_text())
        before = self.priv.read_bytes()
        r = self.tool('keygen', '--private', str(self.priv), '--public', str(self.pub), check=False)
        self.assertEqual(r.returncode, 2); self.assertIn('not overwriting', r.stderr); self.assertEqual(self.priv.read_bytes(), before)


class BuildTests(Base):
    def test_a_release_has_the_four_assets_with_true_sizes_hashes_and_a_valid_signature(self):
        self.keygen(); out = self.d / 'rel'; self.build(out, '--requires', '0.1.0')
        self.assertEqual(sorted(p.name for p in out.iterdir()), ['KERNEL', 'RELEASE.txt', 'SYSTEM', 'manifest.json', 'manifest.json.sig'])
        m = json.loads((out / 'manifest.json').read_text())
        self.assertEqual((m['format'], m['variant'], m['build_id'], m['version'], m['requires'], m['update_type'], m['hw_device']), (1, 'stable', '20261012.1', '0.1.1', '0.1.0', 0, 'H700'))
        for name, src in (('KERNEL', self.kernel), ('SYSTEM', self.system)):
            self.assertEqual(m['files'][name]['size'], src.stat().st_size); self.assertEqual((out / name).read_bytes(), src.read_bytes())
            self.assertEqual(m['files'][name]['url'], 'http://127.0.0.1:1/' + name)
        sig = base64.b64decode((out / 'manifest.json.sig').read_bytes())
        r = subprocess.run(['openssl', 'pkeyutl', '-verify', '-rawin', '-in', str(out / 'manifest.json'), '-sigfile', '/dev/stdin', '-pubin', '-inkey', str(self.pub)], input=sig, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn('NOT the public key', (out / 'RELEASE.txt').read_text()); self.assertNotIn('--allow-gaps', (out / 'RELEASE.txt').read_text())

    def argv(self, out, **over):
        opts = {'--kernel': str(self.kernel), '--system': str(self.system), '--build-id': '20261012.1', '--version': '0.1.1', '--base-url': 'https://example.invalid/r', '--out': str(out),
                '--key': str(self.priv), **over}
        return ['build', '--tree', str(self.tree(True)), '--extlinux', '/nonexistent', *[x for kv in opts.items() for x in kv]]

    def test_a_major_update_is_marked(self):
        self.keygen(); self.build(self.d / 'rel', '--major')
        self.assertEqual(json.loads((self.d / 'rel/manifest.json').read_text())['update_type'], 1)

    def test_values_that_cannot_compare_or_that_are_not_https_are_refused_before_anything_is_written(self):
        self.keygen()
        for over in ({'--build-id': 'tuesday'}, {'--build-id': '2026-10-12'}, {'--version': 'one'}, {'--requires': 'x'}, {'--base-url': 'http://insecure.invalid/r'}):
            out = self.d / ('r%d' % len(list(self.d.glob('r*'))))
            r = self.tool(*self.argv(out, **over), check=False)
            self.assertEqual(r.returncode, 2, over); self.assertFalse(out.exists(), over)
        self.assertIn('must be https', self.tool(*self.argv(self.d / 'rx', **{'--base-url': 'http://insecure.invalid/r'}), check=False).stderr)

    def test_a_loose_key_a_missing_key_a_used_output_directory_are_refused(self):
        self.keygen(); os.chmod(self.priv, 0o644)
        self.assertIn('readable by others', self.build(self.d / 'a', check=False).stderr)
        os.chmod(self.priv, 0o600); (self.d / 'b').mkdir(); (self.d / 'b/x').write_text('x')
        self.assertIn('not empty', self.build(self.d / 'b', check=False).stderr)
        self.priv.unlink(); self.assertIn('keygen', self.build(self.d / 'c', check=False).stderr)

    def test_a_key_the_images_do_not_carry_is_a_loud_warning(self):
        self.keygen(); self.pub.write_text(subprocess.run(['openssl', 'pkey', '-in', str(self.priv), '-pubout'], capture_output=True, text=True).stdout.replace('A', 'B', 1))
        self.build(self.d / 'rel'); self.assertIn('WARNING: this key is NOT the public key baked into the images', (self.d / 'rel/RELEASE.txt').read_text())


class IdentityTests(Base):
    """The SYSTEM that is signed must say what the manifest says (the release would otherwise be offered again and again)."""
    def test_a_system_that_names_another_build_version_or_device_is_refused_and_nothing_is_written(self):
        self.keygen()
        for kw, what in (({'build_id': '20261011.9'}, 'BUILD_ID'), ({'version': '0.1.0'}, 'WISPOS_VERSION'), ({'device': 'RPi5'}, 'HW_DEVICE')):
            self.make_system(**kw); out = self.d / ('rel-' + what)
            r = self.build(out, check=False); self.assertEqual(r.returncode, 2, what); self.assertIn(what, r.stderr); self.assertIn('offer the same update again', r.stderr); self.assertFalse(out.exists(), what)

    def test_a_file_that_is_not_a_system_image_is_refused(self):
        self.keygen(); self.system.write_bytes(os.urandom(500_000))
        r = self.build(self.d / 'rel', check=False); self.assertEqual(r.returncode, 2); self.assertIn('cannot read etc/os-release', r.stderr); self.assertFalse((self.d / 'rel').exists())

    def test_version_and_build_id_default_to_the_lockfile(self):
        self.keygen(); lock = self.d / 'lockfile.json'; lock.write_text(json.dumps({'version': '0.1.1', 'build_id': '20261012.1'})); self.env['WISP_LOCKFILE'] = str(lock)
        r = self.tool('build', '--kernel', str(self.kernel), '--system', str(self.system), '--base-url', 'https://example.invalid/r', '--out', str(self.d / 'rel'), '--key', str(self.priv),
                      '--tree', str(self.tree(True)), '--extlinux', '/nonexistent')
        m = json.loads((self.d / 'rel/manifest.json').read_text()); self.assertEqual((m['version'], m['build_id']), ('0.1.1', '20261012.1'))
        lock.write_text(json.dumps({'version': '0.1.1'}))                                       # a lockfile without a build id, and none given
        r = self.tool('build', '--kernel', str(self.kernel), '--system', str(self.system), '--base-url', 'https://example.invalid/r', '--out', str(self.d / 'rel2'), '--key', str(self.priv),
                      '--tree', str(self.tree(True)), '--extlinux', '/nonexistent', check=False)
        self.assertEqual(r.returncode, 2); self.assertIn('no --build-id', r.stderr)


class GateTests(Base):
    """The release gate: check_release.py --strict decides before anything is written."""
    def args(self, out, *extra):
        return ['build', '--kernel', str(self.kernel), '--system', str(self.system), '--build-id', '20261012.1', '--version', '0.1.1', '--base-url', 'https://example.invalid/r', '--out', str(out),
                '--key', str(self.priv), '--tree', str(self.tree(self.conditional)), '--extlinux', '/nonexistent', *extra]

    def test_a_system_with_gaps_is_refused_before_anything_is_written_and_allow_gaps_says_so(self):
        self.keygen(); self.conditional = False
        r = self.tool(*self.args(self.d / 'rel'), check=False)
        self.assertEqual(r.returncode, 2); self.assertIn('release gate refuses', r.stderr); self.assertIn('sshd starts at boot', r.stderr); self.assertFalse((self.d / 'rel').exists())
        r = self.tool(*self.args(self.d / 'rel2', '--allow-gaps'))
        self.assertIn('released with --allow-gaps', (self.d / 'rel2/RELEASE.txt').read_text())

    def test_a_system_that_meets_the_profile_is_released_without_a_warning(self):
        self.keygen(); self.conditional = True
        self.tool(*self.args(self.d / 'rel'))
        self.assertNotIn('--allow-gaps', (self.d / 'rel/RELEASE.txt').read_text())

    def test_a_missing_tree_is_an_error_not_a_pass(self):
        self.keygen()
        r = self.tool('build', '--kernel', str(self.kernel), '--system', str(self.system), '--build-id', '20261012.1', '--version', '0.1.1', '--base-url', 'https://example.invalid/r',
                      '--out', str(self.d / 'rel'), '--key', str(self.priv), '--tree', str(self.d / 'nowhere'), check=False)
        self.assertEqual(r.returncode, 2); self.assertIn('not an unpacked SYSTEM tree', r.stderr)


class RoundTripTests(Base):
    """The release this tool makes, served locally, through the device's own helper."""
    def helper(self, base, *args, pub=None):
        flash = self.d / 'flash'
        if not flash.exists():                                          # a card as flashed: slot a running (docs/WISPOS-UPDATE-DESIGN.md "Boot slots")
            (flash / 'extlinux').mkdir(parents=True); (flash / 'extlinux/extlinux.conf').write_text('DEFAULT a\nLABEL a\n  LINUX /KERNEL\nLABEL b\n  LINUX /wispos/b/KERNEL\n')
            (flash / 'KERNEL').write_bytes(b'old kernel'); (flash / 'SYSTEM').write_bytes(b'old system'); (self.d / 'cmdline').write_text('boot=LABEL=ROCKNIX wisp.slot=a\n')
        (self.d / 'update.conf').write_text(json.dumps({'manifest_url': base + '/manifest.json'})); (self.d / 'os-release').write_text('BUILD_ID="20261011.1"\nWISPOS_VERSION="0.1.0"\nHW_DEVICE="H700"\n')
        env = {**os.environ, 'WISP_UPDATE_CONF': str(self.d / 'update.conf'), 'WISP_UPDATE_PUBKEY': str(pub or self.pub), 'WISP_OS_RELEASE': str(self.d / 'os-release'), 'WISP_FLASH_DIR': str(flash), 'WISP_CMDLINE': str(self.d / 'cmdline'),
               'WISP_UPDATE_NO_REMOUNT': '1', 'WISP_UPDATE_ALLOW_HTTP': '1', 'WISP_UPDATE_FREE_MARGIN': '0', 'TMPDIR': str(WORK / 'tmp')}
        r = subprocess.run(['python3', str(HELPER), *args], capture_output=True, text=True, env=env, timeout=60); return r.returncode, r.stdout

    def release_on_server(self, *extra):
        self.keygen(); out = self.d / 'rel'
        srv = tw.Server(('127.0.0.1', 0), tw.Handler); srv.files, srv.log, srv.drop, srv.throttle, srv.honor_range = {}, [], {}, 0, True
        threading.Thread(target=srv.serve_forever, daemon=True).start(); self.addCleanup(srv.server_close); self.addCleanup(srv.shutdown)
        base = 'http://127.0.0.1:%d' % srv.server_address[1]
        self.build(out, *extra, base=base)
        for p in out.iterdir(): srv.files['/' + p.name] = p.read_bytes()
        return srv, base, out

    def test_the_device_helper_accepts_a_release_made_here_and_switches_to_its_files(self):
        srv, base, out = self.release_on_server()
        rc, stdout = self.helper(base, 'check'); self.assertEqual(rc, 0, stdout)
        self.assertEqual(stdout.split(), ['update', '20261012.1', '0.1.1', 'stable', str(self.kernel.stat().st_size + self.system.stat().st_size), '0'])
        rc, stdout = self.helper(base, 'apply', '20261012.1'); self.assertEqual(rc, 0, stdout)
        slot_b = self.d / 'flash/wispos/b'
        self.assertEqual((slot_b / 'KERNEL').read_bytes(), self.kernel.read_bytes()); self.assertEqual((slot_b / 'SYSTEM').read_bytes(), self.system.read_bytes())
        self.assertEqual((self.d / 'flash/extlinux/extlinux.conf').read_text().splitlines()[0], 'DEFAULT b'); self.assertIn('build_id=20261012.1', (self.d / 'flash/wispos/update/PENDING').read_text())
        self.assertEqual((self.d / 'flash/KERNEL').read_bytes(), b'old kernel')                    # the running slot is untouched

    def test_an_edited_manifest_or_another_key_is_refused_by_the_device(self):
        srv, base, out = self.release_on_server()
        srv.files['/manifest.json'] = srv.files['/manifest.json'].replace(b'0.1.1', b'9.9.9')
        rc, stdout = self.helper(base, 'check'); self.assertEqual(rc, 1); self.assertIn('error BadSignature', stdout)
        srv.files['/manifest.json'] = (out / 'manifest.json').read_bytes()
        other = self.d / 'other.pem'; other.write_bytes(subprocess.run(['openssl', 'genpkey', '-algorithm', 'ED25519'], capture_output=True).stdout)
        otherpub = self.d / 'other.pub'; otherpub.write_text(subprocess.run(['openssl', 'pkey', '-in', str(other), '-pubout'], capture_output=True, text=True).stdout)
        rc, stdout = self.helper(base, 'check', pub=otherpub); self.assertEqual(rc, 1); self.assertIn('error BadSignature', stdout)


if __name__ == '__main__':
    unittest.main()
