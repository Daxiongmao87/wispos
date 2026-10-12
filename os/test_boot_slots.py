"""The boot half of the two-slot update scheme (os/boot/wisp-boot.sh, docs/WISPOS-UPDATE-DESIGN.md "Boot slots").

The script is the initramfs's `wisp_slot` boot step. It runs here for real, in three shells (dash, bash --posix, and the initramfs's OWN busybox under qemu-user when the reference
archive is on the HDD), against a fake /flash directory with stand-ins for the four commands that touch the machine (mount, sync, reboot, and /dev/kmsg). What is proved:
  - a new version on trial is counted at every start, mounts its own system image, and is sent back to the previous slot when the count runs out (FAILED-<id> left, PENDING gone, one
    restart), and not before;
  - a marker for the other slot is stale and is removed; an unreadable marker or an unexpected extlinux.conf is left alone and nothing is written;
  - going back never points the card at an incomplete slot;
  - POWER CUTS: the script is stopped at every sync (the durability barrier) of every scenario and the next boot is run from whatever the card says: at no instant is the slot U-Boot would
    start incomplete, and every cut converges to the previous version (or the trial continuing), never to a loop.
Mutants of the script (each rule changed in turn) must all be caught. Run: python3 -I os/test_boot_slots.py   (the busybox variant needs ref-boot/ref-initramfs on the HDD tree)"""
import glob, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = Path(os.environ.get('WISP_BOOT_SH_PATH', HERE / 'boot/wisp-boot.sh'))
WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))
REFROOT = WORK / 'ref-boot/ref-initramfs'
EXTLINUX = '''DEFAULT %s
LABEL a
  LINUX /KERNEL
  FDT /dtb.img
  APPEND boot=LABEL=ROCKNIX disk=LABEL=STORAGE quiet wisp.slot=a
  FDTOVERLAYS /overlays/x.dtbo
LABEL b
  LINUX /wispos/b/KERNEL
  FDT /dtb.img
  APPEND boot=LABEL=ROCKNIX disk=LABEL=STORAGE quiet wisp.slot=b
  FDTOVERLAYS /overlays/x.dtbo
'''
DRIVER = r'''
LOGF="$WT_LOG"; SYNCS=0
mount() { echo "mount $*" >>"$LOGF"; return 0; }
reboot() { echo "reboot $*" >>"$LOGF"; echo REBOOT; exit 0; }
sync() { SYNCS=$((SYNCS + 1)); echo "sync" >>"$LOGF"; if [ "$SYNCS" = "$WT_CRASH_AT" ]; then echo CRASH; exit 77; fi; return 0; }
IMAGE_SYSTEM=SYSTEM
. "$WT_SCRIPT"
if [ -n "$WT_CODE" ]; then eval "$WT_CODE"; echo "CODE_RC=$?"; else wisp_slot; fi
echo "IMAGE_SYSTEM=$IMAGE_SYSTEM"
echo "SYNCS=$SYNCS"
'''


def shells():
    out = []
    for name, argv in (('dash', ['dash', '-c']), ('bash-posix', ['bash', '--posix', '-c'])):
        if shutil.which(argv[0]): out.append((name, argv, None))
    q = shutil.which('qemu-aarch64-static')
    if q and (REFROOT / 'usr/bin/busybox').is_file():
        out.append(('busybox', [q, '-L', str(REFROOT), str(REFROOT / 'usr/bin/busybox'), 'sh', '-c'], 'applets-only'))
    return out


class Card:
    """A fake /flash: the files the script looks at, plus helpers to read the state the way U-Boot and the next boot would."""
    def __init__(self, root, default='a', pending=None, a_complete=True, b_complete=True):
        self.root = Path(root); self.log = self.root.parent / (self.root.name + '.log'); self.log.write_text('')
        (self.root / 'extlinux').mkdir(parents=True); (self.root / 'wispos/b').mkdir(parents=True); (self.root / 'wispos/update').mkdir(parents=True)
        (self.root / 'extlinux/extlinux.conf').write_text(EXTLINUX % default)
        if a_complete: (self.root / 'KERNEL').write_bytes(b'k-a'); (self.root / 'SYSTEM').write_bytes(b's-a')
        if b_complete: (self.root / 'wispos/b/KERNEL').write_bytes(b'k-b'); (self.root / 'wispos/b/SYSTEM').write_bytes(b's-b')
        if pending is not None: self.set_pending(**pending)

    def set_pending(self, slot='b', build_id='20261012.1', version='0.1.1', tries=0):
        (self.root / 'wispos/update/PENDING').write_text('slot=%s\nbuild_id=%s\nversion=%s\ntries=%s\n' % (slot, build_id, version, tries))

    @property
    def default(self): return (self.root / 'extlinux/extlinux.conf').read_text().splitlines()[0]
    @property
    def pending(self):
        p = self.root / 'wispos/update/PENDING'
        return dict(l.split('=', 1) for l in p.read_text().splitlines() if '=' in l) if p.exists() else None
    @property
    def failed(self): return sorted(p.name for p in (self.root / 'wispos/update').glob('FAILED-*'))
    def set_boots(self, n): (self.root / 'wispos/update/BOOTS').write_bytes(b'boot_tries=%d\n' % n + b'\0' * 18)       # what U-Boot writes
    @property
    def boots(self): return (self.root / 'wispos/update/BOOTS').exists()
    def complete(self, slot):
        d = self.root if slot == 'a' else self.root / 'wispos/b'
        return all((d / n).is_file() and (d / n).stat().st_size > 0 for n in ('KERNEL', 'SYSTEM'))
    @property
    def slot_u_boot_starts(self):
        line = self.default; assert line in ('DEFAULT a', 'DEFAULT b'), line
        return line[-1]
    def files(self): return {str(p.relative_to(self.root)): p.read_bytes() for p in sorted(self.root.rglob('*')) if p.is_file()}


class Runner:
    def __init__(self, tmp, shell): self.tmp, self.shell = Path(tmp), shell; self.n = 0

    def applet_path(self):
        """The busybox variant's PATH: the initramfs's shell runs an applet that is not built into the shell by re-executing /proc/self/exe, which under qemu-user is qemu, not busybox,
        so the applets the script needs (the ones that are not NOFORK) are given as small wrappers that run the REAL busybox applet under qemu-user. rm, mv, head, cat, printf run in the shell."""
        d = self.tmp / 'applets'
        if not d.exists():
            d.mkdir()
            for name in ('sed', 'grep', 'dd', 'cat', 'head', 'mv', 'rm', 'ls', 'tr', 'cut', 'wc', 'tail', 'mkdir', 'cp'):
                w = d / name; w.write_text('#!/bin/sh\nexec %s -L %s %s %s "$@"\n' % (shutil.which('qemu-aarch64-static'), REFROOT, REFROOT / 'usr/bin/busybox', name)); w.chmod(0o755)
        return str(d)

    def boot(self, card, slot=None, crash_at=None, tries=None, cmdline=None, code=''):
        """One run of the boot step in a fresh shell process, as the slot U-Boot would start (or the one given)."""
        name, argv, mode = self.shell
        slot = slot or card.slot_u_boot_starts
        self.n += 1; cmd = self.tmp / ('cmdline-%d' % self.n)
        cmd.write_text(cmdline if cmdline is not None else 'boot=LABEL=ROCKNIX quiet wisp.slot=%s disk=LABEL=STORAGE\n' % slot)
        env = {'WT_CODE': code, 'WT_LOG': str(card.log), 'WT_SCRIPT': str(SCRIPT), 'WT_CRASH_AT': str(crash_at or ''), 'WISP_FLASH': str(card.root), 'WISP_CMDLINE': str(cmd),
               'WISP_KMSG': str(self.tmp / 'kmsg'), 'PATH': os.environ['PATH'] if mode is None else self.applet_path()}
        if tries is not None: env['WISP_TRIES'] = str(tries)
        env['WISP_TRIAL_PAUSE'] = '0'                                                                           # no pause before a restart in tests
        r = subprocess.run(argv + [DRIVER], capture_output=True, text=True, env=env, timeout=120)
        out = r.stdout.splitlines()
        res = {'rc': r.returncode, 'out': r.stdout, 'err': r.stderr, 'reboot': 'REBOOT' in out, 'crashed': 'CRASH' in out, 'slot': slot,
               'image': next((l.split('=', 1)[1] for l in out if l.startswith('IMAGE_SYSTEM=')), None), 'syncs': next((int(l.split('=', 1)[1]) for l in out if l.startswith('SYNCS=')), None),
               'code_rc': next((int(l.split('=', 1)[1]) for l in out if l.startswith('CODE_RC=')), None)}
        return res


class ScenarioBase(unittest.TestCase):
    shell = None

    def setUp(self):
        if not str(WORK).startswith('/mnt/hdd/'): self.skipTest('the work directory must be on the HDD')
        (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix='wispos-bootslots-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, self.tmp, True)
        self.run_ = Runner(self.tmp, self.shell)
        self.n = 0

    def card(self, **kw):
        self.n += 1; return Card(self.tmp / ('flash%d' % self.n), **kw)


class Scenarios:
    """The behaviour, written once and run in every shell."""

    def test_no_marker_means_nothing_happens_and_slot_b_mounts_its_own_system(self):
        c = self.card(default='b'); before = c.files()
        r = self.run_.boot(c); self.assertEqual((r['rc'], r['reboot'], r['image']), (0, False, 'wispos/b/SYSTEM'), r)
        self.assertEqual(c.files(), before); self.assertEqual(r['syncs'], 0)                          # not one write, not even a remount
        c = self.card(default='a'); r = self.run_.boot(c); self.assertEqual((r['reboot'], r['image']), (False, 'SYSTEM'))     # slot a keeps the init's own image name

    def test_a_card_without_a_slot_on_its_command_line_is_slot_a(self):
        c = self.card(default='a', pending={'slot': 'b', 'tries': 0})
        r = self.run_.boot(c, cmdline='boot=LABEL=ROCKNIX quiet\n'); self.assertEqual((r['image'], c.pending), ('SYSTEM', None))      # slot a running: the marker for b is stale

    def test_each_start_of_a_new_version_is_counted_and_it_keeps_its_own_system(self):
        c = self.card(default='b', pending={'slot': 'b', 'tries': 0})
        r = self.run_.boot(c); self.assertEqual((r['rc'], r['reboot'], r['image']), (0, False, 'wispos/b/SYSTEM'), r)
        self.assertEqual(c.pending, {'slot': 'b', 'build_id': '20261012.1', 'version': '0.1.1', 'tries': '1'})
        r = self.run_.boot(c); self.assertFalse(r['reboot']); self.assertEqual(c.pending['tries'], '2')
        self.assertEqual(c.default, 'DEFAULT b'); self.assertEqual(c.failed, [])

    def test_the_third_start_without_a_confirmation_goes_back_and_restarts_once(self):
        c = self.card(default='b', pending={'slot': 'b', 'tries': 2})
        r = self.run_.boot(c); self.assertTrue(r['reboot'], r); self.assertIsNone(r['image'])           # the restart came before the script finished
        self.assertEqual((c.default, c.pending, c.failed), ('DEFAULT a', None, ['FAILED-20261012.1']))
        self.assertEqual((c.root / 'wispos/update/FAILED-20261012.1').read_text(), 'slot=b\n')
        self.assertEqual(c.log.read_text().count('reboot -f'), 1)
        r = self.run_.boot(c); self.assertEqual((r['reboot'], r['image']), (False, 'SYSTEM'))            # the previous version starts clean, with its own system
        self.assertTrue(c.complete('a') and c.complete('b'))                                             # neither slot was touched

    def test_the_whole_story_over_five_starts(self):
        c = self.card(default='b', pending={'slot': 'b', 'tries': 0}); seen = []
        for _ in range(5):
            r = self.run_.boot(c); seen.append((r['slot'], c.default, r['reboot'], (c.pending or {}).get('tries')))
        self.assertEqual(seen, [('b', 'DEFAULT b', False, '1'), ('b', 'DEFAULT b', False, '2'), ('b', 'DEFAULT a', True, None), ('a', 'DEFAULT a', False, None), ('a', 'DEFAULT a', False, None)])

    def test_a_confirmed_version_is_never_sent_back(self):
        c = self.card(default='b', pending={'slot': 'b', 'tries': 0})
        self.run_.boot(c); (c.root / 'wispos/update/PENDING').unlink()                                   # what `wisp-update confirm` does
        for _ in range(4): r = self.run_.boot(c); self.assertFalse(r['reboot'])
        self.assertEqual((c.default, c.failed), ('DEFAULT b', []))

    def test_the_number_of_unconfirmed_starts_allowed_is_the_configured_one(self):
        c = self.card(default='b', pending={'slot': 'b', 'tries': 0})
        for i in (1, 2, 3, 4): self.assertFalse(self.run_.boot(c, tries=5)['reboot'], i)
        self.assertEqual(c.pending['tries'], '4')
        self.assertFalse(self.run_.boot(c, tries=5)['reboot']); self.assertTrue(self.run_.boot(c, tries=5)['reboot'])
        c = self.card(default='b', pending={'slot': 'b', 'tries': 0})
        self.assertFalse(self.run_.boot(c, tries=1)['reboot']); self.assertTrue(self.run_.boot(c, tries=1)['reboot'])

    def test_a_marker_for_the_other_slot_is_stale_and_removed(self):
        for default, marker in (('a', 'b'), ('b', 'a')):
            c = self.card(default=default, pending={'slot': marker, 'tries': 1})
            r = self.run_.boot(c); self.assertEqual((r['reboot'], c.pending, c.default, c.failed), (False, None, 'DEFAULT ' + default, []))

    def test_a_marker_that_cannot_be_read_is_left_alone_and_nothing_is_written(self):
        for text in ('', 'garbage\n', 'slot=c\ntries=1\n', 'tries=9\n'):
            c = self.card(default='b'); (c.root / 'wispos/update/PENDING').write_text(text); before = c.files()
            r = self.run_.boot(c); self.assertEqual((r['rc'], r['reboot'], r['syncs']), (0, False, 0), (text, r)); self.assertEqual(c.files(), before, text)

    def test_a_marker_with_a_broken_count_counts_from_zero_and_is_repaired(self):
        for broken in ('tries=x\n', 'tries=\n', '', '\n'):
            c = self.card(default='b'); (c.root / 'wispos/update/PENDING').write_text('slot=b\nbuild_id=20261012.1\n' + broken)
            r = self.run_.boot(c); self.assertFalse(r['reboot'], broken); self.assertEqual(c.pending['tries'], '1', broken); self.assertEqual(c.pending['build_id'], '20261012.1')

    def test_going_back_never_points_the_card_at_an_incomplete_slot(self):
        for broken in ('a-no-system', 'a-empty-kernel', 'a-no-kernel'):
            c = self.card(default='b', pending={'slot': 'b', 'tries': 2})
            if broken == 'a-no-system': (c.root / 'SYSTEM').unlink()
            elif broken == 'a-empty-kernel': (c.root / 'KERNEL').write_bytes(b'')
            else: (c.root / 'KERNEL').unlink()
            r = self.run_.boot(c)
            self.assertEqual((r['rc'], r['reboot'], c.default, c.failed), (0, False, 'DEFAULT b', []), (broken, r))                # carried on booting what is there
            self.assertEqual(c.pending['tries'], '2', broken)                                                                          # and wrote nothing about it
            self.assertEqual(r['image'], 'wispos/b/SYSTEM')

    def test_an_extlinux_conf_in_an_unexpected_form_is_never_edited(self):
        for text in ('LABEL a\nDEFAULT a\n', 'DEFAULT  a\n', 'default a\n', 'DEFAULT c\n', '# DEFAULT a\n', ''):
            c = self.card(default='b', pending={'slot': 'b', 'tries': 2}); (c.root / 'extlinux/extlinux.conf').write_text(text); before = (c.root / 'extlinux/extlinux.conf').read_bytes()
            r = self.run_.boot(c, slot='b'); self.assertEqual((r['rc'], r['reboot']), (0, False), text)
            self.assertEqual((c.root / 'extlinux/extlinux.conf').read_bytes(), before, text)

    def test_the_failed_marker_is_named_only_with_digits_and_dots(self):
        c = self.card(default='b', pending={'slot': 'b', 'tries': 2, 'build_id': '../../evil name'}); top = sorted(p.name for p in c.root.iterdir())
        r = self.run_.boot(c); self.assertTrue(r['reboot']); self.assertEqual(c.failed, ['FAILED-unknown'])
        self.assertEqual(sorted(p.name for p in c.root.iterdir()), top)                                      # nothing appeared next to the card's own files
        self.assertFalse((c.root.parent / 'evil name').exists() or (c.root / 'evil name').exists())

    def test_the_flip_itself_refuses_a_file_that_is_not_in_the_expected_form(self):
        for text in ('LABEL a\nDEFAULT a\n', 'DEFAULT  a\n', 'default a\n', 'DEFAULT c\n', '# DEFAULT a\n', ''):
            c = self.card(default='b'); (c.root / 'extlinux/extlinux.conf').write_text(text)
            r = self.run_.boot(c, slot='b', code='wisp_set_default a'); self.assertNotEqual(r['code_rc'], 0, text); self.assertEqual((c.root / 'extlinux/extlinux.conf').read_text(), text)
        c = self.card(default='b'); r = self.run_.boot(c, code='wisp_set_default a'); self.assertEqual((r['code_rc'], c.default), (0, 'DEFAULT a'))
        r = self.run_.boot(c, code='wisp_set_default b'); self.assertEqual((r['code_rc'], c.default), (0, 'DEFAULT b'))

    def test_only_the_letter_changes_in_extlinux_conf(self):
        c = self.card(default='b', pending={'slot': 'b', 'tries': 2}); before = (c.root / 'extlinux/extlinux.conf').read_text()
        self.run_.boot(c); after = (c.root / 'extlinux/extlinux.conf').read_text()
        self.assertEqual(after, before.replace('DEFAULT b', 'DEFAULT a', 1)); self.assertEqual(len(after), len(before))

    def test_going_back_syncs_after_the_failure_marker_after_the_flip_and_before_read_only(self):
        c = self.card(default='b', pending={'slot': 'b', 'tries': 2}); self.run_.boot(c)
        self.assertEqual(c.log.read_text().splitlines(), ['mount -o remount,rw %s' % c.root, 'sync', 'sync', 'sync', 'mount -o remount,ro %s' % c.root, 'reboot -f'])

    def test_the_boot_partition_is_writable_only_while_a_marker_is_changed(self):
        c = self.card(default='b', pending={'slot': 'b', 'tries': 0}); self.run_.boot(c)
        self.assertEqual(c.log.read_text().splitlines(), ['mount -o remount,rw %s' % c.root, 'sync', 'mount -o remount,ro %s' % c.root])

    # The reference init's `error` ends in a debug shell and its `poweroff` needs a finger on Power. These stand-ins say which one ran.
    REFERENCE = 'error() { echo REFERENCE_ERROR; return 1; }; poweroff() { echo REFERENCE_POWEROFF; }; '

    def test_a_failing_step_on_trial_restarts_instead_of_stopping_in_the_debug_shell(self):
        c = self.card(default='b', pending={'slot': 'b', 'tries': 0})
        r = self.run_.boot(c, code=self.REFERENCE + 'wisp_slot; error final_check "Could not find systemd!"; echo NOT_REACHED')
        self.assertTrue(r['reboot'], r); self.assertNotIn('REFERENCE_ERROR', r['out']); self.assertNotIn('NOT_REACHED', r['out'])
        self.assertIn('reboot -f', c.log.read_text()); self.assertEqual(c.pending['tries'], '1')            # the start was counted before it failed
        self.assertIn('Could not find systemd', r['err'])                                                     # and the reason is still said, on stderr as the reference does

    def test_a_power_off_on_trial_is_a_restart_too(self):
        c = self.card(default='b', pending={'slot': 'b', 'tries': 1})
        r = self.run_.boot(c, code=self.REFERENCE + 'wisp_slot; poweroff; echo NOT_REACHED')
        self.assertTrue(r['reboot'], r); self.assertNotIn('REFERENCE_POWEROFF', r['out']); self.assertNotIn('NOT_REACHED', r['out'])

    def test_outside_a_trial_the_reference_behaviour_is_untouched(self):
        for name, c in (('no marker', self.card(default='a')), ('stale marker', self.card(default='a', pending={'slot': 'b', 'tries': 1})),
                        ('confirmed system, marker gone', self.card(default='b'))):
            r = self.run_.boot(c, code=self.REFERENCE + 'wisp_slot; error x y; poweroff')
            self.assertFalse(r['reboot'], name); self.assertEqual(r['out'].count('REFERENCE_ERROR'), 1, name); self.assertEqual(r['out'].count('REFERENCE_POWEROFF'), 1, name)

    def test_without_a_durable_count_or_a_way_back_there_is_no_guard(self):
        c = self.card(default='b', pending={'slot': 'b', 'tries': 2}, a_complete=False)                       # nothing to go back to: carry on, and let the reference init report
        r = self.run_.boot(c, code=self.REFERENCE + 'wisp_slot; error x y')
        self.assertFalse(r['reboot']); self.assertIn('REFERENCE_ERROR', r['out'])
        c = self.card(default='b', pending={'slot': 'b', 'tries': 0})                                         # the boot partition will not become writable: the start cannot be counted
        r = self.run_.boot(c, code=self.REFERENCE + 'mount() { return 1; }; wisp_slot; error x y')
        self.assertFalse(r['reboot']); self.assertIn('REFERENCE_ERROR', r['out']); self.assertEqual(c.pending['tries'], '0')
        c = self.card(default='b', pending={'slot': 'b', 'tries': 0})                                         # the count was computed but could not be put in place
        r = self.run_.boot(c, code=self.REFERENCE + 'mv() { return 1; }; wisp_slot; error x y')
        self.assertFalse(r['reboot']); self.assertIn('REFERENCE_ERROR', r['out']); self.assertEqual(c.pending['tries'], '0')

    def test_a_version_that_fails_every_start_is_back_on_the_previous_one_with_no_hands(self):
        c = self.card(default='b', pending={'slot': 'b', 'tries': 0}); seen = []
        for _ in range(6):
            slot = c.slot_u_boot_starts
            r = self.run_.boot(c, code=self.REFERENCE + 'wisp_slot; error final_check "Could not find systemd!"; echo STARTED')
            seen.append((slot, r['reboot'], 'STARTED' in r['out'] or 'REFERENCE_ERROR' in r['out']))
        # b fails twice (each failure restarts), the third start of b goes back, then a runs its own system (its error, if any, is the reference's)
        self.assertEqual(seen[:3], [('b', True, False), ('b', True, False), ('b', True, False)])
        self.assertEqual(seen[3], ('a', False, True)); self.assertEqual((c.default, c.pending, c.failed), ('DEFAULT a', None, ['FAILED-20261012.1']))

    # The bootloader (os/boot/uboot/wisp-trial.txt) counts the starts of the slot on trial in wispos/update/BOOTS and, after two that never reached the initramfs, starts the
    # previous version from extlinux/rollback.conf. These are the initramfs's halves of that.
    def test_a_trial_slot_that_gets_here_clears_the_bootloaders_count(self):
        c = self.card(default='b', pending={'slot': 'b', 'tries': 0}); c.set_boots(1)
        r = self.run_.boot(c); self.assertFalse(r['reboot']); self.assertFalse(c.boots); self.assertEqual(c.pending['tries'], '1')

    def test_a_stale_marker_takes_the_count_with_it(self):
        c = self.card(default='a', pending={'slot': 'b', 'tries': 0}); c.set_boots(1)
        r = self.run_.boot(c); self.assertEqual((c.pending, c.boots, c.failed, c.default), (None, False, [], 'DEFAULT a'))

    def test_a_version_the_bootloader_could_not_start_is_marked_failed_and_the_letter_goes_back(self):
        for boots in (None, 1, 2):                                                                     # U-Boot fell back at once (no kernel to load), or counted it out
            c = self.card(default='b', pending={'slot': 'b', 'tries': 0})
            if boots: c.set_boots(boots)
            r = self.run_.boot(c, slot='a')                                                            # slot a is running although the letter says b
            self.assertEqual((r['rc'], r['reboot'], r['image']), (0, False, 'SYSTEM'), (boots, r))     # no restart: this system is the one to keep
            self.assertEqual((c.default, c.pending, c.boots, c.failed), ('DEFAULT a', None, False, ['FAILED-20261012.1']), boots)
            self.assertEqual(c.log.read_text().splitlines(), ['mount -o remount,rw %s' % c.root, 'sync', 'sync', 'sync', 'mount -o remount,ro %s' % c.root])
            r = self.run_.boot(c); self.assertEqual((r['reboot'], c.default), (False, 'DEFAULT a'))   # and the next start is plain slot a

    def test_the_same_story_from_the_other_side(self):
        c = self.card(default='a', pending={'slot': 'a', 'tries': 0}, a_complete=True)                 # a trial of slot a, started as slot b by the bootloader's fallback
        r = self.run_.boot(c, slot='b'); self.assertEqual((r['image'], c.default, c.failed, c.pending), ('wispos/b/SYSTEM', 'DEFAULT b', ['FAILED-20261012.1'], None))

    def test_a_marker_the_letter_does_not_name_is_never_a_failure(self):
        c = self.card(default='a', pending={'slot': 'b', 'tries': 0}); r = self.run_.boot(c, slot='a')                 # the switch was never made: stale, not failed
        self.assertEqual((c.failed, c.pending), ([], None))
        c = self.card(default='b', pending={'slot': 'a', 'tries': 0}); r = self.run_.boot(c, slot='b'); self.assertEqual((c.failed, c.pending), ([], None))

    def test_an_unreadable_extlinux_conf_makes_it_a_plain_stale_marker(self):
        c = self.card(default='b', pending={'slot': 'b', 'tries': 0}); (c.root / 'extlinux/extlinux.conf').write_text('garbage\n'); before = (c.root / 'extlinux/extlinux.conf').read_bytes()
        self.run_.boot(c, slot='a'); self.assertEqual(((c.root / 'extlinux/extlinux.conf').read_bytes(), c.failed), (before, []))

    def test_the_initramfs_rollback_clears_the_count_too(self):
        c = self.card(default='b', pending={'slot': 'b', 'tries': 2}); c.set_boots(1)
        self.run_.boot(c); self.assertFalse(c.boots)

    def test_a_power_cut_at_any_sync_leaves_a_card_that_starts_and_converges(self):
        # Every scenario, stopped at each of its sync barriers in turn, then booted as U-Boot would (the slot the card names) until nothing changes any more.
        # `count`: a new version's first start and nothing ever confirms it, so the story must end in the previous version, whatever instant the power went at.
        for name, build in (('rollback', lambda: self.card(default='b', pending={'slot': 'b', 'tries': 2})),
                            ('count', lambda: self.card(default='b', pending={'slot': 'b', 'tries': 0})),
                            ('stale', lambda: self.card(default='a', pending={'slot': 'b', 'tries': 1})),
                            ('fallback', lambda: self.card(default='b', pending={'slot': 'b', 'tries': 0}))):
            probe = build(); self.run_.boot(probe, slot='a' if name == 'fallback' else None); points = probe.log.read_text().splitlines().count('sync')
            self.assertGreaterEqual(points, 1, name)
            for k in range(1, points + 1):
                c = build(); r = self.run_.boot(c, crash_at=k, slot='a' if name == 'fallback' else None); self.assertTrue(r['crashed'], (name, k, r))
                self.assertTrue(c.complete(c.slot_u_boot_starts), (name, k, 'right after the cut'))                       # U-Boot would find a kernel and a system
                for i in range(8):                                                                                      # then the following starts, as the card says
                    r = self.run_.boot(c)
                    self.assertTrue(c.complete(c.slot_u_boot_starts), (name, k, i))
                    if not r['reboot'] and c.pending is None: break
                else: self.fail('no convergence: %s cut at sync %d' % (name, k))
                self.assertEqual((c.default, c.pending, c.failed == [] ), ('DEFAULT a', None, name == 'stale'), (name, k, c.failed))
                if name != 'stale': self.assertEqual(c.failed, ['FAILED-20261012.1'], (name, k))

    def test_the_cut_during_the_count_never_loses_or_corrupts_the_marker(self):
        c = self.card(default='b', pending={'slot': 'b', 'tries': 0})
        r = self.run_.boot(c, crash_at=1); self.assertTrue(r['crashed'])
        self.assertIn(c.pending['tries'], ('0', '1')); self.assertEqual(c.pending['build_id'], '20261012.1')          # the count is the old or the new one, never half of one


def make_case(shell):
    name = 'Boot_' + shell[0].replace('-', '_')
    return type(name, (Scenarios, ScenarioBase), {'shell': shell})


for _shell in shells():
    globals()['Boot_' + _shell[0].replace('-', '_')] = make_case(_shell)
del ScenarioBase.shell


class Wiring(unittest.TestCase):
    def test_the_script_is_posix_shell_and_defines_the_step_and_nothing_else_runs_on_load(self):
        text = SCRIPT.read_text()
        for sh in ('dash', 'bash'):
            if shutil.which(sh): subprocess.run([sh, '-n', str(SCRIPT)], check=True)
        self.assertIn('wisp_slot()', text)
        r = subprocess.run(['dash', '-c', '. "$1"; echo loaded', 'x', str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), 'loaded')                                                              # sourcing it prints and writes nothing

    def test_no_bashisms(self):
        text = '\n'.join(l.split('#', 1)[0] if not l.lstrip().startswith('#') else '' for l in SCRIPT.read_text().splitlines())
        for bad in ('[[', ']]', '<<<', '${!', '=~', 'function ', 'declare ', 'source ', '(( '):
            self.assertNotIn(bad, text, bad)


MUTANTS = [
    ('a version gets one start too many before going back', '[ "$p_tries" -ge "$WISP_TRIES" ]', '[ "$p_tries" -gt "$WISP_TRIES" ]'),
    ('the count never moves', 'n=$((p_tries + 1))', 'n=$((p_tries + 0))'),
    ('a marker for THIS slot counts as stale', 'if [ "$p_slot" != "$slot" ]; then', 'if [ "$p_slot" = "$slot" ]; then'),
    ('no failure marker is left', 'printf \'slot=%s\\n\' "$bad" >"$WISP_FLASH/wispos/update/FAILED-$build" 2>/dev/null', ':'),
    ('the marker survives going back', 'rm -f "$WISP_FLASH/wispos/update/PENDING"', ':'),
    ('going back flips to the slot that failed', 'if ! wisp_set_default "$good"; then', 'if ! wisp_set_default "$bad"; then'),
    ('slot b mounts the wrong image', '[ "$slot" = b ] && IMAGE_SYSTEM=wispos/b/SYSTEM', '[ "$slot" = a ] && IMAGE_SYSTEM=wispos/b/SYSTEM'),
    ('the letter is written one byte too far', 'seek=8', 'seek=9'),
    ('going back to an incomplete slot is allowed', 'if ! wisp_complete "$good"; then', 'if false; then'),
    ('the flip truncates the file', 'conv=notrunc ', ''),
    ('no slot on the command line means b', '    echo a\n}', '    echo b\n}'),
    ('a stale marker is kept', 'wisp_rw && rm -f "$pend"', 'wisp_rw'),
    ('the flip is not synced', "    sync\n    [ \"$(wisp_default)\" = \"$1\" ]", "    [ \"$(wisp_default)\" = \"$1\" ]"),
    ('read-only again without a sync', 'wisp_ro() { sync; mount', 'wisp_ro() { mount'),
    ('the build id may carry anything into a file name', "case $build in ''|*[!0-9.]*) build=unknown ;; esac", ':'),
    ('the count is never written back', 'mv -f "$pend.tmp" "$pend"', 'rm -f "$pend.tmp"'),
    ('three starts are allowed instead of two', 'WISP_TRIES=${WISP_TRIES:-2}', 'WISP_TRIES=${WISP_TRIES:-3}'),
    ('extlinux.conf is edited whatever it looks like', "    wisp_default >/dev/null || return 1\n    printf", '    printf'),
    ('the restart is forgotten', '    reboot -f\n', '    :\n'),
    ('a failing step on trial is left to the debug shell', '    wisp_trial_guard "$n"\n', '    :\n'),
    ('a power-off on trial is left alone', 'poweroff() { wisp_trial_failed "power off requested"; }', ':'),
    ('the trial failure forgets to restart', '; reboot -f; }', '; :; }'),
    ('the guard is installed when the count could not be written', 'mv -f "$pend.tmp" "$pend" || { wisp_ro; return 0; }', 'mv -f "$pend.tmp" "$pend" || { wisp_ro; wisp_trial_guard "$n"; return 0; }'),
    ('the letter is not compared with the marker before a failure is declared', 'if [ "$(wisp_default)" = "$p_slot" ]; then', 'if true; then'),
    ('a version that never started is not marked failed', 'wisp_switch_back "$p_slot" "$slot" "$p_build"\n            return 0', 'wisp_say "ignored"\n            return 0'),
    ('a version that never started flips to itself', 'wisp_switch_back "$p_slot" "$slot" "$p_build"', 'wisp_switch_back "$slot" "$p_slot" "$p_build"'),
    ('the bootloaders count survives going back', '    rm -f "$WISP_FLASH/wispos/update/BOOTS"\n    wisp_ro\n    return 0', '    wisp_ro\n    return 0'),
    ('the bootloaders count survives a stale marker', 'rm -f "$pend" "$WISP_FLASH/wispos/update/BOOTS"', 'rm -f "$pend"'),
    ('the bootloaders count survives reaching the trial slot', next(l for l in SCRIPT.read_text().splitlines() if 'this kernel got here' in l), '    :'),
    ('a broken count is not repaired', "case $p_tries in ''|*[!0-9]*) p_tries=0 ;; esac", ':'),
]


class Mutants(unittest.TestCase):
    """Each rule of the script changed in turn: the scenarios (dash only, for speed) must fail every time."""
    def test_every_mutant_is_caught(self):
        if not shutil.which('dash'): self.skipTest('no dash')
        if 'WISP_BOOT_SH_PATH' in os.environ: self.skipTest('running a mutant already')
        (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix='wispos-bootmut-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, tmp, True)
        text = SCRIPT.read_text(); survivors = []
        for i, (what, old, new) in enumerate(MUTANTS):
            self.assertEqual(text.count(old), 1, 'mutant %r: the pattern must occur exactly once' % what)
            m = tmp / ('mutant%d.sh' % i); m.write_text(text.replace(old, new))
            r = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), 'Boot_dash'], env={**os.environ, 'WISP_BOOT_SH_PATH': str(m)}, capture_output=True, text=True, timeout=600)
            if r.returncode == 0: survivors.append(what)
        self.assertEqual(survivors, [], 'mutants that no test noticed')


if __name__ == '__main__':
    unittest.main()
