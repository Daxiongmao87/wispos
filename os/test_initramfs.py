"""The initramfs patch for the boot splash and the boot slots (os/boot/patch_initramfs.py): the writer is exact, and a patched archive differs from ROCKNIX's reference in four entries
only (the splash wrapper replaced, the splash binary and /wisp-boot added, /init with two lines added).
Run: python3 -I os/test_initramfs.py   (the real-reference tests need ref-boot/ref.cpio.* on the HDD staging tree and are skipped without it)"""
import glob, os, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'boot'))
import patch_initramfs as P

REF = (sorted(glob.glob('/mnt/hdd/wispos-staging/wispos-os-20261007/ref-boot/ref.cpio.*')) or [None])[0]


TINY_INIT = b'#!/bin/sh\n. /functions 2>/dev/null\n. /device.init 2>/dev/null\nfor BOOT_STEP in \\\n    mount_flash \\\n    cleanup_flash \\\n    update_bootmenu \\\n    mount_sysroot\ndo\n  ${BOOT_STEP}\ndone\n'


def entry(ino, name, mode, data): return {'ino': ino, 'mode': mode, 'uid': 0, 'gid': 0, 'nlink': 1, 'mtime': 1700000000, 'devmajor': 0, 'devminor': 0, 'rdevmajor': 0, 'rdevminor': 0,
                                          'check': 0, 'name': name, 'data': data}


def tiny_archive(init=TINY_INIT):
    return P.write_cpio([entry(1, 'init', 0o100755, init), entry(2, 'usr/bin/rocknix-splash', 0o100755, b'\x7fELF-old' * 5), entry(5, 'device.init', 0o100644, b'# device\n'),
                         entry(3, 'etc', 0o040755, b''), entry(4, 'etc/link', 0o120777, b'../init')], b'\0' * 100)


class Writer(unittest.TestCase):
    def test_round_trip_is_exact_on_a_small_archive(self):
        data = tiny_archive()
        entries, tail = P.read_cpio(data)
        self.assertEqual(P.write_cpio(entries, tail), data)
        self.assertEqual([x['name'] for x in entries], ['init', 'usr/bin/rocknix-splash', 'device.init', 'etc', 'etc/link'])

    @unittest.skipIf(REF is None, 'no reference archive')
    def test_round_trip_is_exact_on_the_real_reference(self):
        data = Path(REF).read_bytes()
        entries, tail = P.read_cpio(data)
        self.assertEqual(P.write_cpio(entries, tail), data)                   # not one byte changes just by reading and writing it back
        self.assertEqual(len(entries), 68)

    def test_the_host_cpio_reads_what_we_write(self):
        out = subprocess.run(['cpio', '-it', '--quiet'], input=tiny_archive(), capture_output=True)
        if out.returncode != 0 and not out.stdout: self.skipTest('no usable cpio on the host')
        self.assertEqual(out.stdout.decode().split(), ['init', 'usr/bin/rocknix-splash', 'device.init', 'etc', 'etc/link'])


class Patch(unittest.TestCase):
    BOOT = b'wisp_slot() { :; }\n'

    def test_exactly_one_entry_replaced_one_edited_and_two_added(self):
        data = tiny_archive()
        out = P.patch(data, b'#!/bin/sh\nexit 0\n', b'\x7fELFnew', self.BOOT)
        a, ta = P.read_cpio(data); b, tb = P.read_cpio(out)
        self.assertEqual(ta, tb)                                              # the trailing padding is untouched
        self.assertEqual([x['name'] for x in b], ['init', 'usr/bin/rocknix-splash', 'usr/bin/wisp-splash', 'device.init', 'wisp-boot', 'etc', 'etc/link'])
        by = {x['name']: x for x in b}
        self.assertEqual(by['usr/bin/rocknix-splash']['data'], b'#!/bin/sh\nexit 0\n'); self.assertEqual(by['usr/bin/wisp-splash']['data'], b'\x7fELFnew')
        self.assertEqual(by['usr/bin/wisp-splash']['mode'], 0o100755); self.assertEqual(by['wisp-boot']['data'], self.BOOT); self.assertEqual(by['wisp-boot']['mode'], 0o100644)
        changed = {'usr/bin/rocknix-splash', 'init'}
        for x in a:                                                           # every other entry is identical
            if x['name'] not in changed: self.assertEqual(by[x['name']], x)
        self.assertEqual({k: v for k, v in by['init'].items() if k != 'data'}, {k: v for k, v in a[0].items() if k != 'data'})     # the init keeps its mode, owner and time
        self.assertEqual(len({x['ino'] for x in b}), len(b))                  # inode numbers stay unique

    def test_the_init_gets_exactly_two_lines(self):
        out = P.patch_init(TINY_INIT)
        old, new = TINY_INIT.decode().splitlines(), out.decode().splitlines()
        self.assertEqual(len(new), len(old) + 2)
        added = [l for l in new if l not in old]; self.assertEqual(added, ['. /wisp-boot 2>/dev/null', '    wisp_slot \\'])
        self.assertEqual([l for l in new if l in old], old)                   # the rest, in order
        self.assertEqual(new.index('    wisp_slot \\'), new.index('    cleanup_flash \\') + 1)       # right behind cleanup_flash: after the boot partition is mounted, before the system image
        self.assertLess(new.index('    wisp_slot \\'), new.index('    mount_sysroot'))
        self.assertEqual(new.index('. /wisp-boot 2>/dev/null'), new.index('. /device.init 2>/dev/null') + 1)

    def test_an_init_without_exactly_one_of_each_anchor_is_refused(self):
        for init in (TINY_INIT.replace(b'    cleanup_flash \\\n', b''), TINY_INIT.replace(b'. /device.init 2>/dev/null\n', b''),
                     TINY_INIT + b'    cleanup_flash \\\n', TINY_INIT + b'. /device.init 2>/dev/null\n', b'', TINY_INIT.replace(b'    cleanup_flash', b'  cleanup_flash')):
            with self.assertRaises(ValueError): P.patch_init(init)
        with self.assertRaises(ValueError): P.patch(tiny_archive(b'#!/bin/sh\n'), b'x', b'y', self.BOOT)

    def test_patching_twice_or_without_the_entries_it_needs_is_refused(self):
        out = P.patch(tiny_archive(), b'x', b'y', self.BOOT)
        with self.assertRaises(ValueError): P.patch(out, b'x', b'y', self.BOOT)
        entries, tail = P.read_cpio(tiny_archive())
        for missing in ('usr/bin/rocknix-splash', 'init', 'device.init'):
            with self.assertRaises(ValueError): P.patch(P.write_cpio([x for x in entries if x['name'] != missing], tail), b'x', b'y', self.BOOT)

    @unittest.skipIf(REF is None, 'no reference archive')
    def test_the_real_reference_patched_differs_in_four_entries_only(self):
        data = Path(REF).read_bytes()
        wrapper = (HERE / 'splash/rocknix-splash.sh').read_bytes(); boot = (HERE / 'boot/wisp-boot.sh').read_bytes()
        out = P.patch(data, wrapper, b'\x7fELF' + bytes(1000), boot)
        a, _ = P.read_cpio(data); b, _ = P.read_cpio(out)
        self.assertEqual(len(b), len(a) + 2)
        changed = [x['name'] for x in b if x['name'] not in {y['name'] for y in a} or x not in a]
        self.assertEqual(sorted(changed), ['init', 'usr/bin/rocknix-splash', 'usr/bin/wisp-splash', 'wisp-boot'])
        names = [x['name'] for x in b]
        self.assertEqual(names.index('usr/bin/wisp-splash'), names.index('usr/bin/rocknix-splash') + 1)
        self.assertEqual(names.index('wisp-boot'), names.index('device.init') + 1)
        old_init = next(x for x in a if x['name'] == 'init')['data']; new_init = next(x for x in b if x['name'] == 'init')['data']
        self.assertEqual(new_init, P.patch_init(old_init))
        self.assertEqual(len(new_init.splitlines()), len(old_init.splitlines()) + 2)                                         # ROCKNIX's init is otherwise untouched

    @unittest.skipIf(REF is None, 'no reference archive')
    def test_the_patched_init_is_valid_shell_and_runs_the_step_between_the_right_neighbours(self):
        a, _ = P.read_cpio(Path(REF).read_bytes())
        init = P.patch_init(next(x for x in a if x['name'] == 'init')['data']).decode()
        r = subprocess.run(['sh', '-n'], input=init, capture_output=True, text=True); self.assertEqual(r.returncode, 0, r.stderr)
        steps = init[init.index('for BOOT_STEP in'):init.index('do\n', init.index('for BOOT_STEP in'))].replace('\\\n', ' ').split()[4:]
        self.assertEqual(steps[steps.index('cleanup_flash'):steps.index('cleanup_flash') + 4], ['cleanup_flash', 'wisp_slot', 'update_bootmenu', 'mount_sysroot'])
        self.assertLess(steps.index('mount_flash'), steps.index('wisp_slot'))


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
