"""The bootloader half of the update trial: os/boot/uboot/wisp-trial.txt, run by the REAL U-Boot command interpreter (the sandbox build of the handheld bootloader's own source tree,
os/boot/build-uboot-sandbox.sh) against a real FAT32 volume. What is proved:
  - while the slot named by extlinux.conf is the one the trial marker (PENDING) names, every start adds one to wispos/update/BOOTS, and the third finds it at 2 and starts the previous
    version from extlinux/rollback.conf instead; nothing else on the volume changes and `fsck.fat` still finds a consistent filesystem after the writes;
  - nothing is counted, nothing is written and nothing is started differently when there is no marker, when the marker is for the other slot (a switch that was never made), when the
    marker or extlinux.conf cannot be read, or when BOOTS holds rubbish; the script leaves no variables behind;
  - the generated C header (make_env.py) defines exactly the two variables and redefines boot_extlinux by running the script first; install_env.py refuses a tree it was not written for.
Mutants of the script (each rule changed in turn) must all be caught. Skipped, with the reason, when the sandbox build is absent (os/boot/build-uboot-sandbox.sh, about 5 minutes)."""
import hashlib, os, re, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE / 'boot'), str(HERE / 'boot/uboot')]
import fatwrite, make_env, install_env
from qemu_update_test import Fat32

WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))
SANDBOX = WORK / 'build/uboot-sandbox/u-boot'
TXT = Path(os.environ.get('WISP_TRIAL_TXT', HERE / 'boot/uboot/wisp-trial.txt'))
EXT = 'DEFAULT %s\nLABEL a\n  LINUX /KERNEL\n  APPEND x wisp.slot=a\nLABEL b\n  LINUX /wispos/b/KERNEL\n  APPEND x wisp.slot=b\n'
PENDING = 'slot=%s\nbuild_id=20261012.1\nversion=0.1.1\ntries=0\n'
ANSI = re.compile(r'\x1b\[[0-9;?]*[A-Za-z]')


def why_skipped():
    if not str(WORK).startswith('/mnt/hdd/'): return 'the work directory must be on the HDD'
    if not SANDBOX.is_file(): return 'no sandbox U-Boot at %s (run os/boot/build-uboot-sandbox.sh)' % SANDBOX
    for t in ('mkfs.fat', 'fsck.fat', 'truncate'):
        if not shutil.which(t): return 'no ' + t
    return None


@unittest.skipIf(why_skipped(), why_skipped() or '')
class TrialScriptTests(unittest.TestCase):
    def setUp(self):
        (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
        self.d = Path(tempfile.mkdtemp(prefix='wispos-utrial-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, self.d, True)
        self.script = make_env.script(TXT.read_text()); self.n = 0

    def card(self, default='b', pending='b', boots=None, ext=True, rollback=True, pending_text=None, extra=None):
        self.n += 1; src = self.d / ('src%d' % self.n); (src / 'extlinux').mkdir(parents=True); (src / 'wispos/update').mkdir(parents=True)
        if ext: (src / 'extlinux/extlinux.conf').write_text(EXT % default if ext is True else ext)
        if rollback: (src / 'extlinux/rollback.conf').write_text(EXT % 'a')
        if pending_text is not None: (src / 'wispos/update/PENDING').write_text(pending_text)
        elif pending: (src / 'wispos/update/PENDING').write_text(PENDING % pending)
        if boots is not None: (src / 'wispos/update/BOOTS').write_bytes(boots)
        (src / 'KERNEL').write_bytes(b'junk' * 64)
        for name, data in (extra or {}).items(): (src / name).write_bytes(data)
        img = self.d / ('disk%d.img' % self.n)
        subprocess.run(['truncate', '-s', '64M', str(img)], check=True); subprocess.run(['mkfs.fat', '-F', '32', str(img)], check=True, capture_output=True)
        fatwrite.populate(str(img), 0, str(src)); return img

    PATHS = ('/extlinux/extlinux.conf', '/extlinux/rollback.conf', '/wispos/update/PENDING', '/wispos/update/BOOTS', '/KERNEL')

    def files(self, img):
        """The contents of the files this test cares about, read from the volume by the independent FAT reader."""
        fs = Fat32(str(img), 0)
        try: return {p: d for p, d in ((p, fs.read_file(p)) for p in self.PATHS) if d is not None}
        finally: fs.close()

    def run_script(self, img, extra='run wisp_trial', script=None):
        pre = ("host bind 0 %s; setenv devtype host; setenv devnum 0; setenv distro_bootpart 0; setenv prefix /; setenv scriptaddr 0x1000000; setenv wisp_trial '%s'; "
               % (img, script or self.script))
        r = subprocess.run([str(SANDBOX), '-D', '-c', pre + extra + '; echo END'], capture_output=True, text=True, cwd=SANDBOX.parent, timeout=120)
        out = ANSI.sub('', r.stdout)
        self.assertIn('END', out, out[-600:]); self.assertEqual(r.returncode, 0, out[-600:])
        return out

    def fsck(self, img):
        r = subprocess.run(['fsck.fat', '-n', str(img)], capture_output=True, text=True)
        bad = [l for l in (r.stdout + r.stderr).splitlines() if re.search(r'Dirty|differ|Orphan|corrupt|Bad|invalid|Not a|Cluster|lost', l) and 'Free cluster summary' not in l]
        return bad

    # ------------------------------------------------------------------------------------------------------------- counting and rollback
    def test_each_start_of_the_version_on_trial_is_counted_and_the_third_goes_back(self):
        img = self.card(default='b', pending='b'); before = self.files(img)
        for want in (b'boot_tries=1\n', b'boot_tries=2\n'):
            out = self.run_script(img); self.assertNotIn('previous version', out, out[-500:])
            got = self.files(img)['/wispos/update/BOOTS']; self.assertTrue(got.startswith(want), got)
            self.assertEqual(got.rstrip(b'\0'), want)                                                      # nothing but the line (and padding)
        out = self.run_script(img); self.assertIn('did not reach its initramfs twice - starting the previous version', out)
        self.assertIn('Retrieving file: /extlinux/rollback.conf', out)
        self.assertTrue(self.files(img)['/wispos/update/BOOTS'].startswith(b'boot_tries=2\n'))              # a rollback writes nothing
        after = self.files(img)
        for k in before: self.assertEqual(after[k], before[k], k)                                           # no other file changed
        self.assertEqual(self.fsck(img), [])

    def test_the_rollback_starts_the_label_a_of_the_fixed_file_not_the_flipped_letter(self):
        img = self.card(default='b', pending='b', boots=b'boot_tries=2\n')
        out = self.run_script(img); self.assertIn('Retrieving file: /KERNEL', out)                         # rollback.conf says DEFAULT a: slot a's kernel first
        self.assertLess(out.index('Retrieving file: /KERNEL'), out.index('Retrieving file: /wispos/b/KERNEL') if 'Retrieving file: /wispos/b/KERNEL' in out else 10**9)

    def test_a_trial_of_slot_a_is_counted_the_same_way(self):
        img = self.card(default='a', pending='a'); self.run_script(img)
        self.assertTrue(self.files(img)['/wispos/update/BOOTS'].startswith(b'boot_tries=1\n'))

    # ------------------------------------------------------------------------------------------------------------- nothing to do
    def check_untouched(self, img, why):
        before = self.files(img); out = self.run_script(img)
        self.assertEqual(self.files(img), before, why); self.assertNotIn('previous version', out, why); self.assertNotIn('bytes written', out, why)
        self.assertEqual(self.fsck(img), [], why); return out

    def test_without_a_marker_nothing_happens(self): self.check_untouched(self.card(default='b', pending=None), 'no marker')

    def test_a_marker_for_the_other_slot_is_not_counted(self):
        self.check_untouched(self.card(default='a', pending='b'), 'switch never made'); self.check_untouched(self.card(default='b', pending='a'), 'marker a, letter b')

    def test_a_count_left_over_without_a_marker_is_ignored(self): self.check_untouched(self.card(default='b', pending=None, boots=b'boot_tries=2\n'), 'stale count')

    def test_unreadable_files_leave_the_boot_alone(self):
        for what, img in (('no extlinux.conf', self.card(ext=False)), ('empty extlinux.conf', self.card(ext='')), ('garbage marker', self.card(pending=None, pending_text='garbage\n')),
                          ('empty marker', self.card(pending=None, pending_text='')), ('marker without a slot', self.card(pending=None, pending_text='tries=1\n')),
                          ('marker with slot c', self.card(pending=None, pending_text='slot=c\n'))):
            self.check_untouched(img, what)

    def test_rubbish_in_the_count_file_counts_as_zero(self):
        for junk in (b'garbage', b'boot_tries=\n', b'boot_tries=zz\n', b'\0\0\0'):
            img = self.card(boots=junk); self.run_script(img)
            self.assertTrue(self.files(img)['/wispos/update/BOOTS'].startswith(b'boot_tries=1\n'), junk)

    def test_leftover_variables_from_earlier_in_the_same_boot_do_not_count(self):
        img = self.card(default='b', pending=None, pending_text='tries=1\n'); before = self.files(img)
        self.run_script(img, 'setenv slot b; run wisp_trial'); self.assertEqual(self.files(img), before)         # a `slot` left by an earlier scan is not a marker
        img = self.card(default='b', pending='b'); self.run_script(img, 'setenv boot_tries 9; run wisp_trial')       # a stale count is not the count
        self.assertTrue(self.files(img)['/wispos/update/BOOTS'].startswith(b'boot_tries=1\n'))

    def test_a_missing_rollback_file_does_not_stop_the_boot(self):
        img = self.card(boots=b'boot_tries=2\n', rollback=False); out = self.run_script(img, 'run wisp_trial; echo AFTER')
        self.assertIn('AFTER', out)                                                                          # the script returned: the normal start of extlinux.conf would follow

    def test_a_later_definition_in_an_imported_environment_replaces_an_earlier_one(self):
        # the default environment is imported by the same function, in order: this is why the generated boot_extlinux, placed after the distro boot's own, wins
        img = self.card(extra={'env.txt': b'dup=first\nother=1\ndup=second\n'})
        out = self.run_script(img, 'load host 0:0 0x2000000 env.txt; env import -t 0x2000000 ${filesize}; printenv dup')
        self.assertIn('dup=second', out); self.assertNotIn('dup=first', out)

    def test_the_script_leaves_no_variables_behind(self):
        for img in (self.card(), self.card(pending=None), self.card(boots=b'boot_tries=2\n')):
            out = self.run_script(img, 'run wisp_trial; printenv slot wisp_a wisp_l wisp_next boot_tries')
            self.assertEqual(out.count('not defined'), 5, out[-400:])

    # ------------------------------------------------------------------------------------------------------------- the generated header
    def test_the_header_defines_the_two_variables_and_hooks_boot_extlinux(self):
        h = make_env.header(TXT.read_text())
        self.assertEqual(len(re.findall(r'"wisp_trial=', h)), 1); self.assertEqual(len(re.findall(r'"boot_extlinux=run wisp_trial; sysboot \$\{devtype\}', h)), 1)
        self.assertNotIn('\\"', h.split('#define WISP_ENV')[1].replace('\\0"', ''))                              # no escaped quote in the script
        self.assertNotIn('#', make_env.script(TXT.read_text()))                                              # comments were dropped

    def test_the_installer_hooks_the_board_header_once_and_refuses_a_tree_it_was_not_written_for(self):
        if not (WORK / 'build/uboot/include/config_distro_bootcmd.h').is_file(): self.skipTest('no U-Boot tree')
        tree = self.d / 'tree'; (tree / 'include/configs').mkdir(parents=True)
        shutil.copy(WORK / 'build/uboot/include/config_distro_bootcmd.h', tree / 'include'); orig = (WORK / 'build/uboot/include/configs/sunxi-common.h').read_text()
        if 'wisp-env.h' in orig: orig = orig.replace('#include "wisp-env.h"\n\n', '').replace('\tBOOTENV \\\n\tWISP_ENV\n', '\tBOOTENV\n')
        (tree / 'include/configs/sunxi-common.h').write_text(orig)
        install_env.install(tree); text = (tree / 'include/configs/sunxi-common.h').read_text()
        self.assertEqual(text.count('WISP_ENV'), 1); self.assertEqual(text.count('#include "wisp-env.h"'), 1); self.assertTrue((tree / 'include/configs/wisp-env.h').is_file())
        with self.assertRaises(SystemExit): install_env.install(tree)                                          # not twice
        t2 = self.d / 'tree2'; shutil.copytree(tree, t2); (t2 / 'include/configs/sunxi-common.h').write_text(orig.replace('\tBOOTENV\n', '\tBOOTENV_X\n'))
        with self.assertRaises(SystemExit): install_env.install(t2)                                            # the anchor moved upstream
        t3 = self.d / 'tree3'; shutil.copytree(tree, t3); (t3 / 'include/configs/sunxi-common.h').write_text(orig)
        d = (t3 / 'include/config_distro_bootcmd.h'); d.write_text(d.read_text().replace('"sysboot ${devtype}', '"sysboot2 ${devtype}'))
        with self.assertRaises(SystemExit): install_env.install(t3)                                            # boot_extlinux is not what it was


MUTANTS = [
    ('the count never moves', 'setexpr boot_tries ${boot_tries} + 1;', 'setexpr boot_tries ${boot_tries} + 0;'),
    ('three early deaths are allowed', 'itest ${boot_tries} -ge 2', 'itest ${boot_tries} -ge 3'),
    ('one early death is enough', 'itest ${boot_tries} -ge 2', 'itest ${boot_tries} -ge 1'),
    ('a marker for the other slot is counted', 'if test ${slot}x = ${wisp_next}x; then', 'if true; then'),
    ('the letter is read one byte too far', 'setexpr wisp_a ${scriptaddr} + 8;', 'setexpr wisp_a ${scriptaddr} + 9;'),
    ('b is read as a', 'if test ${wisp_l} = 62;', 'if test ${wisp_l} = 61;'),
    ('the rollback starts the normal file', 'rollback.conf;', 'extlinux.conf;'),
    ('the count is written under another name', 'wispos/update/BOOTS ${filesize}', 'wispos/update/BOOT ${filesize}'),
    ('the count is never read back', 'then env import -t ${scriptaddr} ${filesize} boot_tries; fi;', 'then true; fi;'),
    ('the count is never written', 'fatwrite ${devtype} ${devnum}:${distro_bootpart} ${scriptaddr} ${prefix}wispos/update/BOOTS ${filesize};', 'true;'),
    ('the marker is not required', 'if load ${devtype} ${devnum}:${distro_bootpart} ${scriptaddr} ${prefix}wispos/update/PENDING 100; then', 'if true; then'),
    ('the slot variable is not cleared first', 'setenv slot;\nif load', 'if load'),
    ('the variables are left behind', 'setenv wisp_a; setenv wisp_l; setenv wisp_next; setenv slot; setenv boot_tries', 'true'),
    ('extlinux.conf is not read', 'if load ${devtype} ${devnum}:${distro_bootpart} ${scriptaddr} ${prefix}extlinux/extlinux.conf 9; then', 'if true; then'),
    ('a count that imports as nothing is not read as zero', 'if test ${boot_tries}x = x; then setenv boot_tries 0; fi;', 'true;'),
    ('the count is not reset before it is read', 'setenv boot_tries 0;\nif load', 'if load'),
]


class Mutants(unittest.TestCase):
    """Each rule of the script changed in turn: the tests above, run against the changed copy, must fail every time."""
    @unittest.skipIf(why_skipped(), why_skipped() or '')
    def test_every_mutant_is_caught(self):
        if os.environ.get('WISP_TRIAL_TXT'): self.skipTest('running a mutant already')
        import concurrent.futures
        tmp = Path(tempfile.mkdtemp(prefix='wispos-utrial-mut-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, tmp, True)
        text = TXT.read_text(); jobs = []
        for i, (what, old, new) in enumerate(MUTANTS):
            self.assertEqual(text.count(old), 1, 'mutant %r: the pattern must occur exactly once' % what)
            m = tmp / ('m%d.txt' % i); m.write_text(text.replace(old, new)); jobs.append((what, m))
        def go(job):
            what, m = job
            r = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), 'TrialScriptTests'], env={**os.environ, 'WISP_TRIAL_TXT': str(m)}, capture_output=True, text=True, timeout=1800)
            return what, r.returncode
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex: results = list(ex.map(go, jobs))
        self.assertEqual([w for w, rc in results if rc == 0], [], 'mutants that no test noticed')


if __name__ == '__main__':
    unittest.main()
