"""The pieces of the QEMU update test (os/boot/qemu_update_test.py) that can be checked without booting anything: the read-only FAT32 reader (it is what the stand-in for U-Boot and
every "what is on the media" check look at, so it must agree with the real tools), the extlinux parser, the U-Boot stand-in's choice of label (including the fallback to the other
label when the default's kernel cannot be loaded), and the translation of a label's command line for the QEMU machine."""
import hashlib, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE / 'boot'))
import fatwrite
import qemu_update_test as Q

WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))
CONF = '''DEFAULT %s
LABEL a
  LINUX /KERNEL
  FDT /dtb.img
  APPEND boot=LABEL=ROCKNIX disk=LABEL=STORAGE console=ttyS0,115200 console=tty0 systemd.debug_shell=ttyS0 quiet loglevel=3 wisp.slot=a
  FDTOVERLAYS /overlays/x.dtbo
LABEL b
  LINUX /wispos/b/KERNEL
  FDT /dtb.img
  APPEND boot=LABEL=ROCKNIX disk=LABEL=STORAGE console=ttyS0,115200 console=tty0 systemd.debug_shell=ttyS0 quiet loglevel=3 wisp.slot=b
  FDTOVERLAYS /overlays/x.dtbo
'''


@unittest.skipUnless(shutil.which('mkfs.fat'), 'needs mkfs.fat')
class FatReader(unittest.TestCase):
    def setUp(self):
        (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
        self.d = Path(tempfile.mkdtemp(prefix='wispos-fatread-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, self.d, True)

    def card(self, tree, size_mb=64):
        img = self.d / 'card.img'; off = Q.BOOT_OFFSET
        with open(img, 'wb') as f: f.truncate(off + size_mb * (1 << 20))
        subprocess.run(['mkfs.fat', '-F', '32', '-s', '8', '-n', 'ROCKNIX', '--offset=%d' % (off // 512), str(img), str(size_mb * 1024)], check=True, capture_output=True)
        fatwrite.populate(img, off, tree); return Q.Card(img)

    def tree(self, default='a', kernel_a=b'ka', kernel_b=None, dtb=b'dtb', fdt_lines=True):
        t = self.d / 'tree'; shutil.rmtree(t, True); t.mkdir()
        conf = CONF % default
        if not fdt_lines: conf = '\n'.join(l for l in conf.splitlines() if not l.startswith(('  FDT ', '  FDTOVERLAYS '))) + '\n'          # the QEMU card: U-Boot passes its own tree on
        (t / 'extlinux').mkdir(); (t / 'extlinux/extlinux.conf').write_text(conf)
        if kernel_a is not None: (t / 'KERNEL').write_bytes(kernel_a)
        if dtb is not None: (t / 'dtb.img').write_bytes(dtb)
        (t / 'wispos').mkdir()
        if kernel_b is not None: (t / 'wispos/b').mkdir(); (t / 'wispos/b/KERNEL').write_bytes(kernel_b)
        return t

    def test_it_reads_what_the_writer_wrote_files_names_sizes_and_directories(self):
        t = self.tree(kernel_a=os.urandom(3_000_017), kernel_b=os.urandom(70_000)); (t / 'Mixed Case Name.TXT').write_text('hello'); (t / 'empty').write_bytes(b''); (t / 'wispos/README.txt').write_text('r')
        card = self.card(t); fat = card.fat()
        try:
            for rel in ('KERNEL', 'wispos/b/KERNEL', 'extlinux/extlinux.conf', 'Mixed Case Name.TXT', 'dtb.img'):
                self.assertEqual(fat.read_file(rel), (t / rel).read_bytes(), rel); self.assertEqual(fat.sha(rel), hashlib.sha256((t / rel).read_bytes()).hexdigest(), rel)
            self.assertEqual(fat.lookup('EXTLINUX/EXTLINUX.CONF')['size'], len((t / 'extlinux/extlinux.conf').read_bytes()))             # names compare without regard to case
            self.assertEqual(fat.lookup('empty')['size'], 0)
            self.assertIsNone(fat.lookup('nothing')); self.assertIsNone(fat.lookup('wispos/nothing')); self.assertIsNone(fat.lookup('KERNEL/inside'))
            self.assertIsNone(fat.read_file('wispos')); self.assertEqual(sorted(fat.listdir('wispos')), ['README.txt', 'b']); self.assertIsNone(fat.listdir('KERNEL'))
            self.assertEqual(sorted(fat.listdir('')), sorted(['KERNEL', 'dtb.img', 'extlinux', 'wispos', 'Mixed Case Name.TXT', 'empty']))
        finally: fat.close()

    def test_it_sees_a_change_made_in_place_the_way_the_media_holds_it(self):
        card = self.card(self.tree('a', kernel_b=b'kb')); self.assertEqual(card.state()['first_line'], 'DEFAULT a')
        fat = card.fat(); f = fat.lookup('extlinux/extlinux.conf'); pos = fat.off + fat.data_off + (f['start'] - 2) * fat.csize; fat.close()
        with open(card.path, 'r+b') as img: img.seek(pos + 8); img.write(b'b')                                                    # what wisp-update and the initramfs do: one byte
        st = card.state(); self.assertEqual(st['first_line'], 'DEFAULT b'); self.assertEqual(st['conf'].replace('DEFAULT b', 'DEFAULT a', 1), CONF % 'a')

    def test_state_reads_the_marker_the_failed_builds_and_the_part_files(self):
        t = self.tree('b', kernel_b=b'kb'); (t / 'wispos/update').mkdir(); (t / 'wispos/update/PENDING').write_text('slot=b\nbuild_id=20261012.1\ntries=1\n'); (t / 'wispos/update/FAILED-20260101.1').write_text('x')
        (t / 'wispos/b/SYSTEM.part').write_bytes(b'part')
        st = self.card(t).state()
        self.assertEqual((st['pending'], st['failed'], st['parts']), ({'slot': 'b', 'build_id': '20261012.1', 'tries': '1'}, ['FAILED-20260101.1'], ['SYSTEM.part']))
        self.assertEqual((st['sizes']['wispos/b/KERNEL'], st['sizes']['KERNEL'], st['sizes']['wispos/b/SYSTEM']), (2, 2, None))

    def test_the_stand_in_starts_the_default_label_and_falls_back_to_the_other_only_when_the_kernel_cannot_be_loaded(self):
        cases = [(dict(default='a', kernel_b=b'kb'), 'a'), (dict(default='b', kernel_b=b'kb'), 'b'),
                 (dict(default='b', kernel_b=None), 'a'),                      # b's kernel is missing: U-Boot cannot load it and tries the other label
                 (dict(default='b', kernel_b=b''), 'a'),                       # present but empty: the same
                 (dict(default='a', kernel_a=None, kernel_b=b'kb'), 'b'),
                 (dict(default='a', kernel_a=None, kernel_b=None), None),      # nothing to load at all
                 (dict(default='a', dtb=None, kernel_b=b'kb'), None)]          # the shared device tree is missing: neither label can start
        for kw, want in cases:
            pick = Q.uboot_pick(self.card(self.tree(**kw))); self.assertEqual(pick[0] if pick else None, want, kw)

    def test_a_label_without_a_device_tree_line_needs_no_device_tree_file(self):
        for kw, want in ((dict(default='b', kernel_b=b'kb'), 'b'), (dict(default='b', kernel_b=None), 'a'), (dict(default='a', dtb=None, kernel_b=b'kb'), 'a')):
            pick = Q.uboot_pick(self.card(self.tree(fdt_lines=False, **kw))); self.assertEqual(pick[0] if pick else None, want, kw)

    def test_the_command_line_is_translated_for_the_virtual_machine_and_keeps_what_matters(self):
        _, labels = Q.parse_extlinux(CONF % 'a')
        for name in 'ab':
            args = Q.qemu_append(labels[name]).split()
            self.assertIn('wisp.slot=' + name, args); self.assertIn('boot=LABEL=ROCKNIX', args); self.assertIn('disk=LABEL=STORAGE', args)
            self.assertEqual([a for a in args if a.startswith('console=')], ['console=ttyAMA0']); self.assertNotIn('quiet', args); self.assertNotIn('loglevel=3', args)
            self.assertFalse([a for a in args if a.startswith('systemd.debug_shell')])
        self.assertEqual(Q.parse_extlinux(CONF % 'b')[0], 'b'); self.assertEqual(sorted(Q.parse_extlinux(CONF % 'b')[1]), ['a', 'b'])

    def test_it_reads_the_real_image_and_agrees_with_the_build_output(self):
        img = WORK / 'out/wispos-rg34xxsp.img'
        if not img.exists(): self.skipTest('no built image')
        card = Q.Card(img); st = card.state()
        self.assertIn(st['first_line'], ('DEFAULT a', 'DEFAULT b', 'LABEL WispOS')); self.assertIsNone(st['pending'])
        fat = card.fat()
        try:
            self.assertEqual(fat.lookup('KERNEL')['size'], (WORK / 'out/kernel/Image').stat().st_size)
            self.assertEqual(fat.sha('dtb.img'), hashlib.sha256((WORK / 'out/kernel/dtbs/sun50i-h700-anbernic-rg34xx-sp-v2-panel.dtb').read_bytes()).hexdigest())
        finally: fat.close()


if __name__ == '__main__':
    unittest.main()
