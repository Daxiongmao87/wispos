"""os/build-all.sh and os/repro-check.sh: the orchestration, tested against FAKE steps and FAKE builds (the real chain takes about twenty minutes and is run by hand: docs/WISPOS-RELEASING.md).

build-all.sh, with every step script replaced by a recorder:
 - the steps run in dependency order (bootloader, boot-screen typeface, initramfs, kernel with that initramfs, SYSTEM, card image, checks) and a failing step stops the chain;
 - the RELEASE steps never see a development knob (WISP_DEV, WISP_SSH_KEY, WISP_DEV_ANNOUNCE, a leaked variant), even when the caller's environment has them; the dev steps get all of them, and a
   dev image needs both inputs or none;
 - the typeface the boot screens were drawn with must be the one in the SYSTEM;
 - it lists its artifacts (ARTIFACTS, ARTIFACTS.sha256) and times its steps.
repro-check.sh, with a fake build that is a pure function of the checkout and the seeded inputs:
 - two builds from clean checkouts in different directories agree -> exit 0, work directories removed (kept with --keep); the seed is never written;
 - a build that depends on its own directory (a path embedded in an artifact) is a MISMATCH naming the artifact, with the directories kept;
 - a dirty tree is refused (--dirty builds a snapshot of it, untracked files in, deleted tracked files out, and says it proves nothing); a build that fails, or that makes a different set of artifacts,
   is a failure; --against compares with the candidate's artifacts (different or missing = failure); a seed off the HDD is refused.
Mutants of both scripts (each rule changed in turn) must all be caught."""
import hashlib, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))
BUILD_ALL = Path(os.environ.get('WISP_BUILD_ALL_SCRIPT', HERE / 'build-all.sh'))
REPRO = Path(os.environ.get('WISP_REPRO_SCRIPT', HERE / 'repro-check.sh'))


def why_skipped():
    if not str(WORK).startswith('/mnt/hdd/'): return 'the work directory must be on the HDD (WISP_WORK=%s)' % WORK
    for tool in ('git', 'mksquashfs', 'unsquashfs'):
        if not shutil.which(tool): return 'no ' + tool
    return None


def run(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, **kw)


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith(('WISP_', 'FAKE_'))}
    env.update(extra); return env


# ---------------------------------------------------------------------------------------------- build-all.sh
def recorder(name, body):
    return '#!/bin/bash\nset -e\nW=$1\necho "%s | $* | dev=${WISP_DEV:-} key=${WISP_SSH_KEY:-} ann=${WISP_DEV_ANNOUNCE:-} var=${WISP_ROOTFS_VARIANT:-} wifi=${WISP_WIFI_CONF:-} fonts=${WISP_UI_FONTS:-}" >>"$FAKE_LOG"\n%s\n' % (name, body)


FAKE_STEPS = {
    'boot/build-uboot.sh': recorder('uboot', 'mkdir -p "$W/out"; echo uboot >"$W/out/u-boot-sunxi-with-spl.bin"'),
    'boot/make-initramfs.sh': recorder('initramfs', '[ -f "$WISP_UI_FONTS/LiberationSans-Bold.ttf" ]; mkdir -p "$W/build"; echo cpio >"$W/build/wispos-initramfs.cpio"'),
    'boot/build-kernel.sh': recorder('kernel', '[ -f "$2" ]; [ "${FAKE_FAIL:-}" != kernel ] || exit 3; mkdir -p "$W/out/kernel"; echo image >"$W/out/kernel/Image"'),
    'build-rootfs.sh': recorder('rootfs', '''[ -f "$W/out/kernel/Image" ]
T=$W/rootfs; [ "${WISP_DEV:-}" != 1 ] || T=$W/rootfs-dev
mkdir -p "$T/tree/usr/share/fonts/liberation" "$W/out/variants"
cp "$FAKE_FONTS/"*.ttf "$T/tree/usr/share/fonts/liberation/"
[ -z "${FAKE_BAD_FONT:-}" ] || echo different >"$T/tree/usr/share/fonts/liberation/LiberationSans-Bold.ttf"
if [ "${WISP_DEV:-}" = 1 ]; then echo system-dev >"$W/out/variants/SYSTEM-dev"; else echo system >"$W/out/SYSTEM"; fi'''),
    'build-image.sh': recorder('image', '''if [ "${WISP_DEV:-}" = 1 ]; then [ -f "$W/out/variants/SYSTEM-dev" ]; echo image-dev >"$W/out/wispos-rg34xxsp-dev.img"
else [ -f "$W/out/SYSTEM" ]; echo image >"$W/out/wispos-rg34xxsp.img"; fi'''),
    'verify_image.py': recorder('verify', '[ -f "$1" ]; [ "${FAKE_FAIL:-}" != verify ] || exit 4').replace('W=$1\n', 'W=$2\n'),                   # called as: verify_image.py IMAGE WORK
}


@unittest.skipIf(why_skipped(), why_skipped() or '')
class BuildAllTests(unittest.TestCase):
    def setUp(self):
        (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
        self.d = Path(tempfile.mkdtemp(prefix='wispos-buildall-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, self.d, True)
        self.os = self.d / 'os'; (self.os / 'boot').mkdir(parents=True)
        shutil.copy(BUILD_ALL, self.os / 'build-all.sh')
        for rel, text in FAKE_STEPS.items(): p = self.os / rel; p.write_text(text); p.chmod(0o755)
        # `python3 -I verify_image.py ...` runs the fake, a shell script, through a python3 stand-in that hands it to bash
        self.bin = self.d / 'bin'; self.bin.mkdir(); (self.bin / 'python3').write_text('#!/bin/bash\n[ "$1" = -I ] && shift\nexec bash "$@"\n'); (self.bin / 'python3').chmod(0o755)
        self.fonts = self.d / 'fonts'; self.fonts.mkdir()
        for n in ('LiberationSans-Bold.ttf', 'LiberationSans-Regular.ttf'): (self.fonts / n).write_bytes(os.urandom(64))
        tree = self.d / 'pinned/usr/share/fonts/liberation'; tree.mkdir(parents=True)
        for n in self.fonts.iterdir(): shutil.copy(n, tree / n.name)
        r = run(['mksquashfs', str(self.d / 'pinned'), str(self.d / 'pinned.squashfs'), '-noappend', '-quiet', '-no-xattrs']); assert r.returncode == 0, r.stderr
        self.work = WORK / 'tmp' / ('buildall-work-%d' % os.getpid()); shutil.rmtree(self.work, True); self.addCleanup(shutil.rmtree, self.work, True)
        self.log = self.d / 'calls.log'

    def build(self, *, env=None, work=None):
        e = clean_env(PATH='%s:%s' % (self.bin, os.environ['PATH']), FAKE_LOG=str(self.log), FAKE_FONTS=str(self.fonts), ROCKNIX_SYSTEM=str(self.d / 'pinned.squashfs'), **(env or {}))
        return run(['bash', str(self.os / 'build-all.sh'), str(work or self.work)], env=e, cwd=self.d)

    def calls(self): return [l.split(' | ') for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_a_release_only_build_runs_the_steps_in_dependency_order(self):
        r = self.build(); self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual([c[0] for c in self.calls()], ['uboot', 'initramfs', 'kernel', 'rootfs', 'image', 'verify'])
        out = self.work / 'out'; names = (out / 'ARTIFACTS').read_text().split()
        self.assertEqual(names, ['u-boot-sunxi-with-spl.bin', 'kernel/Image', 'SYSTEM', 'wispos-rg34xxsp.img'])
        for line in (out / 'ARTIFACTS.sha256').read_text().splitlines():
            h, n = line.split(); self.assertEqual(h, hashlib.sha256((out / n).read_bytes()).hexdigest())
        self.assertEqual(len((out / 'build-all.timings').read_text().splitlines()), 7)                          # a line per step (the typeface step too)

    def test_the_kernel_gets_the_initramfs_and_the_initramfs_the_pinned_typeface(self):
        self.build(); calls = {c[0]: c for c in self.calls()}
        self.assertIn('%s/build/wispos-initramfs.cpio' % self.work, calls['kernel'][1])
        fonts = calls['initramfs'][2].split('fonts=')[1] if 'fonts=' in calls['initramfs'][2] else ''
        self.assertTrue(fonts.startswith(str(self.work / 'build/fonts-pinned')), calls['initramfs'])

    def test_the_dev_inputs_make_a_dev_system_and_image_and_only_those_see_them(self):
        key = self.d / 'k.pub'; key.write_text('ssh-ed25519 AAAA test\n')
        r = self.build(env={'WISP_SSH_KEY': str(key), 'WISP_DEV_ANNOUNCE': '10.0.0.1:47653', 'WISP_ROOTFS_VARIANT': 'modules-only', 'WISP_DEV': '1', 'WISP_WIFI_CONF': '/builders/wifi.conf'}); self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        calls = self.calls(); self.assertEqual([c[0] for c in calls], ['uboot', 'initramfs', 'kernel', 'rootfs', 'rootfs', 'image', 'image', 'verify', 'verify'])
        for i, c in enumerate(calls):
            dev = 'dev=1' in c[2]
            self.assertEqual(dev, i in (4, 6, 8), (i, c))                                                       # exactly the second rootfs, the second image and the second check
            self.assertEqual('key=%s' % key in c[2], dev, c); self.assertEqual('ann=10.0.0.1:47653' in c[2], dev, c)
            self.assertIn('var= wifi= ', c[2], c)                                                               # no variant and no Wi-Fi credential leaks into any step
        self.assertEqual((self.work / 'out/ARTIFACTS').read_text().split(), ['u-boot-sunxi-with-spl.bin', 'kernel/Image', 'SYSTEM', 'variants/SYSTEM-dev', 'wispos-rg34xxsp.img', 'wispos-rg34xxsp-dev.img'])

    def test_the_release_steps_never_see_development_knobs_in_the_callers_environment(self):
        r = self.build(env={'WISP_DEV': '1', 'WISP_WIFI_CONF': '/x', 'WISP_SYSTEM': '/y', 'WISP_KERNEL_IMAGE': '/z'}); self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for c in self.calls(): self.assertIn('dev= key= ann= var= wifi= ', c[2], c)

    def test_a_dev_image_needs_both_inputs_or_none(self):
        key = self.d / 'k.pub'; key.write_text('x')
        for env in ({'WISP_SSH_KEY': str(key)}, {'WISP_DEV_ANNOUNCE': '10.0.0.1:1'}, {'WISP_SSH_KEY': str(self.d / 'missing.pub'), 'WISP_DEV_ANNOUNCE': '10.0.0.1:1'}):
            r = self.build(env=env); self.assertEqual(r.returncode, 2, env); self.assertEqual(self.calls(), [], env)

    def test_a_work_directory_off_the_hdd_is_refused(self):
        self.addCleanup(shutil.rmtree, '/dev/shm/wispos-buildall-test', True)
        r = self.build(work=Path('/dev/shm/wispos-buildall-test')); self.assertEqual(r.returncode, 2); self.assertIn('HDD', r.stderr); self.assertEqual(self.calls(), [])

    def test_a_failing_step_stops_the_chain(self):
        r = self.build(env={'FAKE_FAIL': 'kernel'}); self.assertNotEqual(r.returncode, 0)
        self.assertEqual([c[0] for c in self.calls()], ['uboot', 'initramfs', 'kernel'])
        self.assertFalse((self.work / 'out/ARTIFACTS').exists())

    def test_a_failing_check_fails_the_build_and_lists_no_artifacts(self):
        r = self.build(env={'FAKE_FAIL': 'verify'}); self.assertNotEqual(r.returncode, 0); self.assertFalse((self.work / 'out/ARTIFACTS').exists())

    def test_a_system_with_another_typeface_than_the_boot_screens_is_refused(self):
        r = self.build(env={'FAKE_BAD_FONT': '1'}); self.assertEqual(r.returncode, 1, r.stdout + r.stderr); self.assertIn('boot screens were drawn with', r.stderr)
        self.assertNotIn('image', [c[0] for c in self.calls()])


BUILD_ALL_MUTANTS = [
    ('the release steps keep the development knobs', 'release() { env -u WISP_DEV -u WISP_SSH_KEY -u WISP_WIFI_CONF -u WISP_DEV_ANNOUNCE -u WISP_QEMU_CARD -u WISP_ROOTFS_VARIANT -u WISP_IMAGE_NAME -u WISP_KERNEL_IMAGE -u WISP_SYSTEM -u WISP_SYSTEM_MD5 "$@"; }', 'release() { "$@"; }'),
    ('the dev steps keep the caller\'s variant and credentials', 'dev() { env -u WISP_WIFI_CONF -u WISP_QEMU_CARD -u WISP_ROOTFS_VARIANT -u WISP_IMAGE_NAME -u WISP_KERNEL_IMAGE -u WISP_SYSTEM -u WISP_SYSTEM_MD5 WISP_DEV=1 "$@"; }', 'dev() { env WISP_DEV=1 "$@"; }'),
    ('the dev check runs without WISP_DEV', 'dev python3 -I "$HERE/verify_image.py"', 'release python3 -I "$HERE/verify_image.py"'),
    ('the kernel gets no initramfs', 'release bash "$HERE/boot/build-kernel.sh" "$WORK" "$WORK/build/wispos-initramfs.cpio"', 'release bash "$HERE/boot/build-kernel.sh" "$WORK"'),
    ('the typeface check is gone', 'cmp -s "$FONTS/$f" "$WORK/rootfs/tree/usr/share/fonts/liberation/$f" ||', 'true ||'),
    ('half of the dev inputs build a dev image', '[ -n "${WISP_SSH_KEY:-}" ] && [ -n "${WISP_DEV_ANNOUNCE:-}" ] ||', 'true ||'),
    ('the release image is not checked', 'release python3 -I "$HERE/verify_image.py" "$WORK/out/wispos-rg34xxsp.img" "$WORK"', ':'),
    ('the dev image is not listed', ' [ -z "$DEV" ] || echo wispos-rg34xxsp-dev.img;', ''),
    ('the dev key may be missing', '[ -f "$WISP_SSH_KEY" ] ||', 'true ||'),
    ('the initramfs is made without the pinned typeface', 'release env WISP_UI_FONTS="$FONTS" bash "$HERE/boot/make-initramfs.sh" "$WORK"', 'release bash "$HERE/boot/make-initramfs.sh" "$WORK"'),
    ('the work directory may be anywhere', 'case $(realpath -m "$WORK") in /mnt/hdd/*) ;;', 'case $(realpath -m "$WORK") in /*) ;;'),
]


# ---------------------------------------------------------------------------------------------- repro-check.sh
FAKE_REPRO_BUILD = r'''#!/bin/bash
set -e
W=$1
who=$(basename "$(dirname "$W")")
[ "${FAKE_FAIL_IN:-}" != "$who" ] || { echo "fake build: failing on purpose" >&2; exit 3; }
mkdir -p "$W/out/kernel"
echo built >"$W/dl/touched-by-the-build"                       # a build may write into its OWN copies of the inputs
{ cat "$W/prefix/z"; echo uboot; } >"$W/out/u-boot-sunxi-with-spl.bin"
{ cat "$W/ref-boot/w"; echo kernel; } >"$W/out/kernel/Image"
{ cat os/payload.txt; ls os; [ -z "${FAKE_PATH_LEAK:-}" ] || pwd; } >"$W/out/SYSTEM"
cat "$W/out/kernel/Image" "$W/out/SYSTEM" >"$W/out/wispos-rg34xxsp.img"
cd "$W/out"
printf '%s\n' u-boot-sunxi-with-spl.bin kernel/Image SYSTEM wispos-rg34xxsp.img >ARTIFACTS
[ "${FAKE_EXTRA_IN:-}" != "$who" ] || { echo extra >extra.bin; echo extra.bin >>ARTIFACTS; }
sha256sum $(cat ARTIFACTS) >ARTIFACTS.sha256
'''


def tree_hash(root, skip=()):
    """A hash of the names and contents under root (except the top-level entries named in skip)."""
    h = hashlib.sha256(); root = Path(root)
    for p in sorted(root.rglob('*')):
        rel = p.relative_to(root)
        if rel.parts[0] in skip: continue
        h.update(str(rel).encode()); h.update(b'L' if p.is_symlink() else p.read_bytes() if p.is_file() else b'D')
    return h.hexdigest()


@unittest.skipIf(why_skipped(), why_skipped() or '')
class ReproCheckTests(unittest.TestCase):
    def setUp(self):
        (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
        self.d = Path(tempfile.mkdtemp(prefix='wispos-repro-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, self.d, True)
        self.repo = self.d / 'repo'; (self.repo / 'os').mkdir(parents=True)
        shutil.copy(REPRO, self.repo / 'os/repro-check.sh'); (self.repo / 'os/build-all.sh').write_text(FAKE_REPRO_BUILD); (self.repo / 'os/payload.txt').write_text('payload one\n')
        (self.repo / 'os/obsolete.txt').write_text('to be deleted\n')
        self.git('init', '-q'); self.git('add', '-A'); self.git('commit', '-q', '-m', 'one')
        self.seed = self.d / 'seed'
        for rel, text in (('dl/x', 'dl'), ('rocknix-src/y', 'src'), ('prefix/z', 'prefix\n'), ('ref-boot/w', 'ref\n'), ('sdbus-inc/s', 'inc')):
            p = self.seed / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text)
        self.seed_hash = tree_hash(self.seed)

    def git(self, *a):
        r = subprocess.run(['git', '-c', 'user.name=t', '-c', 'user.email=t@example.org', '-c', 'commit.gpgsign=false', *a], cwd=self.repo, capture_output=True, text=True); assert r.returncode == 0, r.stderr

    def check(self, *args, env=None, seed=None):
        e = clean_env(**(env or {}))
        return run(['bash', str(self.repo / 'os/repro-check.sh'), '--seed', str(seed or self.seed), *args], env=e)

    def runs(self): return sorted((self.seed / 'repro').glob('run-*')) if (self.seed / 'repro').exists() else []

    def test_two_clean_builds_that_agree_are_reproducible_and_leave_nothing_behind(self):
        r = self.check(); self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertIn('REPRODUCIBLE', r.stdout)
        self.assertEqual(self.runs(), [])
        self.assertEqual(tree_hash(self.seed, skip=('repro',)), self.seed_hash)                              # the seed is read, never written (its repro/ is where the runs were)

    def test_the_builds_use_copies_of_the_seed_and_never_the_seed_itself(self):
        r = self.check('--keep'); self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        (run_dir,) = self.runs()
        self.assertEqual((run_dir / 'a/work/dl/touched-by-the-build').read_text(), 'built\n'); self.assertFalse((self.seed / 'dl/touched-by-the-build').exists())
        self.assertFalse((run_dir / 'a/work/dl').is_symlink())
        self.assertIn('prefix', (run_dir / 'a/work/out/u-boot-sunxi-with-spl.bin').read_text())                  # and the inputs did reach the builds
        self.assertEqual((run_dir / 'a/work/out/SYSTEM').read_bytes(), (run_dir / 'b/work/out/SYSTEM').read_bytes())

    def test_a_build_that_depends_on_its_directory_is_a_mismatch_naming_the_artifact(self):
        r = self.check(env={'FAKE_PATH_LEAK': '1'}); self.assertEqual(r.returncode, 1, r.stdout + r.stderr); self.assertIn('MISMATCH', r.stderr)
        line = [l for l in r.stdout.splitlines() if l.startswith('SYSTEM ')]; self.assertTrue(line and 'DIFFERENT' in line[0], r.stdout)
        self.assertEqual([l for l in r.stdout.splitlines() if l.startswith('u-boot') and 'DIFFERENT' in l], [])
        self.assertEqual(len(self.runs()), 1)                                                                    # kept, to look at

    def test_a_dirty_tree_is_refused_before_anything_is_built(self):
        (self.repo / 'os/payload.txt').write_text('changed\n')
        r = self.check(); self.assertEqual(r.returncode, 2); self.assertIn('not clean', r.stderr); self.assertEqual(self.runs(), [])
        (self.repo / 'os/payload.txt').write_text('payload one\n'); (self.repo / 'os/new.txt').write_text('x')       # an untracked file counts too
        r = self.check(); self.assertEqual(r.returncode, 2); self.assertEqual(self.runs(), [])

    def test_a_staged_but_uncommitted_change_makes_the_tree_dirty_too(self):
        self.git('mv', 'os/payload.txt', 'os/payload.moved'); (self.repo / 'os/payload.moved').write_text('x')       # a staged change is not clean either
        r = self.check(); self.assertEqual(r.returncode, 2)

    def test_dirty_builds_a_snapshot_of_the_working_tree_and_says_it_proves_nothing(self):
        (self.repo / 'os/payload.txt').write_text('payload TWO\n'); (self.repo / 'os/new.txt').write_text('untracked'); (self.repo / 'os/obsolete.txt').unlink()
        r = self.check('--dirty', '--keep'); self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertIn('SNAPSHOT OF A DIRTY TREE', r.stdout); self.assertIn('not that any commit is', r.stdout)
        (run_dir,) = self.runs(); text = (run_dir / 'a/work/out/SYSTEM').read_text()
        self.assertIn('payload TWO', text); self.assertIn('new.txt', text); self.assertNotIn('obsolete.txt', text)

    def test_a_failing_build_is_a_failure_with_its_log(self):
        r = self.check(env={'FAKE_FAIL_IN': 'b'}); self.assertEqual(r.returncode, 1); self.assertIn('build b FAILED', r.stderr); self.assertIn('failing on purpose', r.stderr); self.assertNotIn('different sets', r.stderr)

    def test_builds_that_make_different_sets_of_artifacts_are_a_failure(self):
        r = self.check(env={'FAKE_EXTRA_IN': 'b'}); self.assertEqual(r.returncode, 1); self.assertIn('different sets of artifacts', r.stderr)

    def test_the_candidates_artifacts_are_compared_when_given(self):
        r = self.check('--keep'); (run_dir,) = self.runs(); cand = self.d / 'cand'; shutil.copytree(run_dir / 'a/work/out', cand / 'out', symlinks=True); shutil.rmtree(run_dir, True)
        r = self.check('--against', str(cand)); self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertIn('and %s' % cand, r.stdout)
        (cand / 'out/SYSTEM').write_text('another candidate\n')
        r = self.check('--against', str(cand)); self.assertEqual(r.returncode, 1); self.assertIn('NOT the candidate', r.stdout)
        (cand / 'out/SYSTEM').unlink()
        r = self.check('--against', str(cand)); self.assertEqual(r.returncode, 1); self.assertIn('missing', r.stdout)

    def test_a_seed_off_the_hdd_is_refused(self):
        seed = Path('/dev/shm/wispos-repro-test-%d' % os.getpid()); (seed / 'dl').mkdir(parents=True); self.addCleanup(shutil.rmtree, seed, True)
        r = self.check(seed=seed); self.assertEqual(r.returncode, 2); self.assertIn('HDD', r.stderr); self.assertFalse((seed / 'repro').exists())

    def test_an_unknown_option_is_refused(self):
        self.assertEqual(self.check('--frobnicate').returncode, 2)


REPRO_MUTANTS = [
    ('a dirty tree is accepted', 'if [ -z "$DIRTY" ] && [ -n "$(git -C "$ROOT" status --porcelain)" ]; then', 'if false; then'),
    ('a mismatch passes', '[ "$ha" = "$hb" ] || { verdict=DIFFERENT; BAD=1; }', '[ "$ha" = "$hb" ] || { verdict=DIFFERENT; }'),
    ('the candidate is not compared', '[ "$hc" = "$ha" ] || { verdict="$verdict, NOT the candidate"; BAD=1; }', 'true'),
    ('the work directories are removed after a mismatch', 'if [ "$BAD" != 0 ]; then', 'if [ "$BAD" != 0 ]; then rm -rf -- "$RUN";'),
    ('a failed build is not a failure', 'tail -5 "$RUN/$n.log" >&2; FAILED=1; }', 'tail -5 "$RUN/$n.log" >&2; :; }'),
    ('the sets of artifacts are not compared', 'cmp -s "$RUN/a/work/out/ARTIFACTS" "$RUN/b/work/out/ARTIFACTS" ||', 'true ||'),
    ('the inputs are linked instead of copied', 'cp -a --reflink=auto "$SEED/$input" "$d/work/$input"', 'ln -s "$SEED/$input" "$d/work/$input"'),
    ('a seed may be anywhere', 'case $(realpath -m "$p") in /mnt/hdd/*) ;;', 'case $(realpath -m "$p") in *) ;;'),
    ('the snapshot keeps deleted files', "while IFS= read -r -d '' f; do [ -e \"$f\" ] && printf '%s\\0' \"$f\"; done", "while IFS= read -r -d '' f; do printf '%s\\0' \"$f\"; done"),
    ('--keep removes anyway', 'if [ -z "$KEEP" ]; then rm -rf -- "$RUN"; else', 'if true; then rm -rf -- "$RUN"; else'),
    ('--dirty says nothing', 'echo "repro-check: (this was a snapshot of a dirty tree: it says the BUILD is reproducible, not that any commit is)"', ':'),
]


class Mutants(unittest.TestCase):
    """Each rule of the two scripts changed in turn: the tests above, run against the changed copy, must fail every time."""
    @unittest.skipIf(why_skipped(), why_skipped() or '')
    def test_every_mutant_is_caught(self):
        if os.environ.get('WISP_BUILD_ALL_SCRIPT') or os.environ.get('WISP_REPRO_SCRIPT'): self.skipTest('running a mutant already')
        import concurrent.futures
        tmp = Path(tempfile.mkdtemp(prefix='wispos-bscripts-mut-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, tmp, True)
        jobs = []
        for i, (what, old, new) in enumerate(BUILD_ALL_MUTANTS + REPRO_MUTANTS):
            src = BUILD_ALL if i < len(BUILD_ALL_MUTANTS) else REPRO; text = src.read_text()
            self.assertEqual(text.count(old), 1, 'mutant %r: the pattern must occur exactly once' % what)
            m = tmp / ('mutant%d.sh' % i); m.write_text(text.replace(old, new)); m.chmod(0o755)
            jobs.append((what, 'WISP_BUILD_ALL_SCRIPT' if i < len(BUILD_ALL_MUTANTS) else 'WISP_REPRO_SCRIPT', m, 'BuildAllTests' if i < len(BUILD_ALL_MUTANTS) else 'ReproCheckTests'))
        def go(job):
            what, var, m, cls = job
            r = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), cls], env={**os.environ, var: str(m)}, capture_output=True, text=True, timeout=1800)
            return what, r.returncode
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex: results = list(ex.map(go, jobs))
        self.assertEqual([w for w, rc in results if rc == 0], [], 'mutants that no test noticed')


if __name__ == '__main__':
    unittest.main()
