"""The FAT backup-boot-sector scar, proved against real dosfstools on scratch volumes.

Mechanism (found on the 20261015.1 card): dosfstools' `fatlabel -i -r`
regenerates the volume serial and, doing so, syncs the whole primary boot
sector — including a set dirty bit at offset 0x41 — into the backup boot sector
(sector 6). The first-boot resizer runs it while /flash is mounted read-write
(dirty by design), tattooing the backup; a later clean unmount clears only the
primary, leaving 00/01 behind for every boot's fsck to report as "differences
between boot sector and its backup".

What is proved, on scratch FAT32 files (never a card):
 - the premise: dirty primary + `fatlabel -i -r` copies the dirty byte into the backup;
 - the prevent: the overlay fs-resize calls the heal helper right after its fatlabel line;
 - the heal: `wisp-fat-backup-heal` clears a scarred backup (00/01) and a fresh
   tattoo with --force (01/01), leaves a dirty primary alone without --force, and
   changes nothing on a clean volume — exactly one byte moves, serial kept;
 - the hook: wisp-firstboot heals a scarred volume through its WISP_* seams.
"""
import os, shutil, subprocess, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
HEAL = HERE / 'rootfs/usr/lib/wispos/wisp-fat-backup-heal'
FORK = HERE / 'rootfs/usr/lib/rocknix/fs-resize'
FIRSTBOOT = HERE / 'rootfs/usr/bin/wisp-firstboot'

PRIM_OFF = 65            # dirty byte in the primary boot sector (offset 0x41)
BACK_OFF = 6 * 512 + 65  # same byte in the backup boot sector (sector 6)


def need(*tools):
    return unittest.skipUnless(all(shutil.which(t) for t in tools),
                               'needs %s on the host (not installed here => NOT verified)' % ', '.join(tools))


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


class FatBackupHealTests(unittest.TestCase):
    def setUp(self):
        if not os.environ.get('TMPDIR', '').startswith('/mnt/hdd/'):
            self.skipTest('set TMPDIR to a directory on the HDD (images never go to /tmp): NOT run')

    def make_volume(self, size_mb=33, label='WISPTEST'):
        work = Path(tempfile.mkdtemp(prefix='wispos-fat-heal-'))
        img = work / 'vol.fat'
        with open(img, 'wb') as f:
            f.truncate(size_mb * (1 << 20))
        r = run(['mkfs.fat', '-F', '32', '-n', label, str(img), str(size_mb * 1024)])
        self.assertEqual(r.returncode, 0, r.stderr)
        return img

    def poke(self, img, prim=None, back=None):
        with open(img, 'r+b') as f:
            if prim is not None:
                f.seek(PRIM_OFF); f.write(bytes([prim]))
            if back is not None:
                f.seek(BACK_OFF); f.write(bytes([back]))

    def state(self, img):
        with open(img, 'rb') as f:
            data = f.read()
        return data[PRIM_OFF], data[BACK_OFF], data

    def heal(self, img, *args):
        return run(['sh', str(HEAL), *args, str(img)])

    @need('mkfs.fat', 'fatlabel')
    def test_serial_regeneration_copies_a_dirty_bit_into_the_backup(self):
        # The defect premise, with stock tools only: this is what the old
        # first boot did. It passes with or without the fix; it pins the cause.
        img = self.make_volume()
        self.poke(img, prim=1, back=0)          # dirty-mounted, pristine backup
        before = self.state(img)[2]
        r = run(['fatlabel', '-i', '-r', str(img)])
        self.assertEqual(r.returncode, 0, r.stderr)
        prim, back, after = self.state(img)
        self.assertEqual((prim, back), (1, 1), 'fatlabel did not sync the dirty bit into the backup')
        self.assertNotEqual(before, after, 'serial regeneration changed nothing at all')

    @need('mkfs.fat')
    def test_heal_clears_a_scarred_backup_and_nothing_else(self):
        img = self.make_volume()
        self.poke(img, prim=0, back=1)          # the field state: 00/01
        _, _, before = self.state(img)
        r = self.heal(img)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        prim, back, after = self.state(img)
        self.assertEqual((prim, back), (0, 0))
        self.assertEqual(after[:BACK_OFF], before[:BACK_OFF])
        self.assertEqual(after[BACK_OFF + 1:], before[BACK_OFF + 1:])

    @need('mkfs.fat')
    def test_heal_force_repairs_a_fresh_tattoo_while_dirty(self):
        img = self.make_volume()
        self.poke(img, prim=1, back=1)          # what fatlabel leaves behind
        serial_before = self.state(img)[2][0x43:0x47]
        r = self.heal(img, '--force')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        prim, back, after = self.state(img)
        self.assertEqual((prim, back), (1, 0), 'must restore the classic dirty-primary state, not a scar')
        self.assertEqual(after[0x43:0x47], serial_before, 'volume serial must survive')

    @need('mkfs.fat')
    def test_heal_leaves_a_dirty_primary_alone_without_force(self):
        img = self.make_volume()
        self.poke(img, prim=1, back=0)          # classic unclean mount: not ours to touch
        _, _, before = self.state(img)
        r = self.heal(img)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.state(img)[2], before, 'safe mode must not write while the primary is dirty')

    @need('mkfs.fat')
    def test_heal_is_a_noop_on_a_clean_volume(self):
        img = self.make_volume()
        _, _, before = self.state(img)
        r = self.heal(img)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.state(img)[2], before)

    @need('mkfs.fat')
    def test_heal_never_fails_loudly_without_a_device(self):
        for argv in ([], ['/nonexistent/wisp-test.img']):
            r = self.heal(*argv) if argv else run(['sh', str(HEAL)])
            self.assertEqual(r.returncode, 0, argv)

    def test_resizer_heals_right_after_the_serial_regeneration(self):
        # Static guard on the overlay fork: the whole point is the ordering —
        # the heal must run after fatlabel synced the dirty bit, not before.
        text = FORK.read_text()
        fat = text.index('fatlabel -i -r ${DISK}p1')
        heal = text.index('wisp-fat-backup-heal --force ${DISK}p1')
        self.assertLess(fat, heal, 'heal call must come after the fatlabel line')
        self.assertIn('|| true', text.splitlines()[text[:heal].count('\n')],
                      'the heal must not break the resize if it ever fails')

    @need('mkfs.fat')
    def test_firstboot_heals_a_scar_through_its_seams(self):
        # End to end through the real wisp-firstboot: fake /flash dir (as the
        # existing FirstbootTests do), real helper, scarred image as the device.
        img = self.make_volume()
        self.poke(img, prim=0, back=1)
        root = Path(tempfile.mkdtemp(prefix='wispos-firstboot-heal-'))
        (root / 'flash/wispos').mkdir(parents=True); (root / 'storage/home/wisp').mkdir(parents=True)
        text = FIRSTBOOT.read_text().replace('/flash', str(root / 'flash')).replace('/storage', str(root / 'storage'))
        script = root / 'run.sh'; script.write_text(text)
        env = {**os.environ, 'WISP_FLASH_DEV': str(img), 'WISP_FAT_BACKUP_HEAL': str(HEAL)}
        r = run(['sh', str(script)], env=env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.state(img)[:2], (0, 0), 'first boot must leave the volume scar-free')


if __name__ == '__main__':
    unittest.main()
