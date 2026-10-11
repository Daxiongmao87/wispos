"""The initramfs patch for the boot splash (os/boot/patch_initramfs.py): the writer is exact, and a patched archive differs from ROCKNIX's reference in two entries only.
Run: python3 -I os/test_initramfs.py   (the real-reference tests need ref-boot/ref.cpio.* on the HDD staging tree and are skipped without it)"""
import glob, os, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'boot'))
import patch_initramfs as P

REF = (sorted(glob.glob('/mnt/hdd/wispos-staging/wispos-os-20261007/ref-boot/ref.cpio.*')) or [None])[0]


def tiny_archive():
    e = lambda ino, name, mode, data: {'ino': ino, 'mode': mode, 'uid': 0, 'gid': 0, 'nlink': 1, 'mtime': 1700000000, 'devmajor': 0, 'devminor': 0, 'rdevmajor': 0, 'rdevminor': 0, 'check': 0, 'name': name, 'data': data}
    return P.write_cpio([e(1, 'init', 0o100755, b'#!/bin/sh\necho hi\n'), e(2, 'usr/bin/rocknix-splash', 0o100755, b'\x7fELF-old' * 5), e(3, 'etc', 0o040755, b''),
                         e(4, 'etc/link', 0o120777, b'../init')], b'\0' * 100)


class Writer(unittest.TestCase):
    def test_round_trip_is_exact_on_a_small_archive(self):
        data = tiny_archive()
        entries, tail = P.read_cpio(data)
        self.assertEqual(P.write_cpio(entries, tail), data)
        self.assertEqual([x['name'] for x in entries], ['init', 'usr/bin/rocknix-splash', 'etc', 'etc/link'])

    @unittest.skipIf(REF is None, 'no reference archive')
    def test_round_trip_is_exact_on_the_real_reference(self):
        data = Path(REF).read_bytes()
        entries, tail = P.read_cpio(data)
        self.assertEqual(P.write_cpio(entries, tail), data)                   # not one byte changes just by reading and writing it back
        self.assertEqual(len(entries), 68)

    def test_the_host_cpio_reads_what_we_write(self):
        out = subprocess.run(['cpio', '-it', '--quiet'], input=tiny_archive(), capture_output=True)
        if out.returncode != 0 and not out.stdout: self.skipTest('no usable cpio on the host')
        self.assertEqual(out.stdout.decode().split(), ['init', 'usr/bin/rocknix-splash', 'etc', 'etc/link'])


class Patch(unittest.TestCase):
    def test_exactly_one_entry_replaced_and_one_added(self):
        data = tiny_archive()
        out = P.patch(data, b'#!/bin/sh\nexit 0\n', b'\x7fELFnew')
        a, ta = P.read_cpio(data); b, tb = P.read_cpio(out)
        self.assertEqual(ta, tb)                                              # the trailing padding is untouched
        self.assertEqual([x['name'] for x in b], ['init', 'usr/bin/rocknix-splash', 'usr/bin/wisp-splash', 'etc', 'etc/link'])
        by = {x['name']: x for x in b}
        self.assertEqual(by['usr/bin/rocknix-splash']['data'], b'#!/bin/sh\nexit 0\n'); self.assertEqual(by['usr/bin/wisp-splash']['data'], b'\x7fELFnew')
        self.assertEqual(by['usr/bin/wisp-splash']['mode'], 0o100755)
        for x in a:                                                           # every other entry is identical
            if x['name'] != 'usr/bin/rocknix-splash': self.assertEqual(by[x['name']], x)
        self.assertEqual(len({x['ino'] for x in b}), len(b))                  # inode numbers stay unique

    def test_patching_twice_or_without_the_splash_entry_is_refused(self):
        out = P.patch(tiny_archive(), b'x', b'y')
        with self.assertRaises(ValueError): P.patch(out, b'x', b'y')
        entries, tail = P.read_cpio(tiny_archive())
        with self.assertRaises(ValueError): P.patch(P.write_cpio([x for x in entries if x['name'] != 'usr/bin/rocknix-splash'], tail), b'x', b'y')

    @unittest.skipIf(REF is None, 'no reference archive')
    def test_the_real_reference_patched_differs_in_two_entries_only(self):
        data = Path(REF).read_bytes()
        wrapper = (HERE / 'splash/rocknix-splash.sh').read_bytes()
        out = P.patch(data, wrapper, b'\x7fELF' + bytes(1000))
        a, _ = P.read_cpio(data); b, _ = P.read_cpio(out)
        self.assertEqual(len(b), len(a) + 1)
        changed = [x['name'] for x in b if x['name'] not in {y['name'] for y in a} or x not in a]
        self.assertEqual(sorted(changed), ['usr/bin/rocknix-splash', 'usr/bin/wisp-splash'])
        names = [x['name'] for x in b]
        self.assertEqual(names.index('usr/bin/wisp-splash'), names.index('usr/bin/rocknix-splash') + 1)
        self.assertEqual(next(x for x in a if x['name'] == 'init'), next(x for x in b if x['name'] == 'init'))          # ROCKNIX's init is untouched


class Wrapper(unittest.TestCase):
    SCRIPT = HERE / 'splash/rocknix-splash.sh'

    def test_wrapper_is_valid_shell_and_detaches_the_real_splash(self):
        subprocess.run(['sh', '-n', str(self.SCRIPT)], check=True)
        text = self.SCRIPT.read_text()
        self.assertIn('--daemon', text); self.assertIn('exec /usr/bin/wisp-splash', text)       # ROCKNIX's init waits for this script: the splash must detach
        self.assertIn('wisp.splash=off', text)                                                    # the off switch for development images

    def test_the_off_switch_exits_before_anything_is_started(self):
        d = Path(tempfile.mkdtemp(prefix='wispos-wrap-', dir=os.environ.get('TMPDIR')))
        text = self.SCRIPT.read_text().replace('/proc/cmdline', str(d / 'cmdline')).replace('/usr/bin/wisp-splash', str(d / 'fake-splash'))
        (d / 'cmdline').write_text('quiet wisp.splash=off\n'); (d / 'fake-splash').write_text('#!/bin/sh\necho started > %s/ran\n' % d); (d / 'fake-splash').chmod(0o755)
        (d / 'wrapper.sh').write_text(text)
        self.assertEqual(subprocess.run(['sh', str(d / 'wrapper.sh')]).returncode, 0); self.assertFalse((d / 'ran').exists())
        (d / 'cmdline').write_text('quiet wisp.dev\n')
        subprocess.run(['sh', str(d / 'wrapper.sh')], check=True)
        self.assertEqual((d / 'ran').read_text().strip(), 'started')


if __name__ == '__main__':
    unittest.main()
