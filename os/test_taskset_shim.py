"""The taskset shim (os/rootfs/usr/lib/wispos/bin/taskset), first on the Steam session's PATH.

Valve's steamwebhelper.sh ends in `exec taskset 0x7c $(pwd)/steamwebhelper "$@"`; editing that file makes Steam re-extract its package (CRC check, measured 2026-10-14), so WispOS leaves it alone and
answers the `taskset` call instead. What is proved, with the REAL util-linux taskset of this host behind the shim and a stand-in web helper that records what it was started with: the helper runs on
cores 0-3 (not Valve's 2-6 mask) with the three Chromium switches in front of Steam's own arguments, which arrive unchanged and in order; every other taskset use passes straight through (a game's own
launch option, `taskset -p`); the session puts the shim first on PATH and does not edit Valve's file; the unit/rootfs wiring is in place. Mutants (each rule changed in turn) must all be caught.
The stand-in wrapper below keeps Valve's `cd`, DISPLAY and exec lines verbatim; the real file is checked on the device (docs/WISPOS-NOTES.md 2026-10-14)."""
import os, shutil, stat, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHIM = Path(os.environ.get('WISP_TASKSET_SHIM', HERE / 'rootfs/usr/lib/wispos/bin/taskset'))
WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))
VALVE_WRAPPER = '''#!/bin/bash

SCRIPTPATH="$(dirname -- "${BASH_SOURCE[0]}")"
cd $SCRIPTPATH

if [ -z "$DISPLAY" ]; then
 echo "steamwebhelper: DISPLAY not set -- defaulting to :0"
 export DISPLAY=:0
fi

echo "setting LIBGL_KOPPER_DISABLE=true for steamwebhelper to get store working properly"

export LIBGL_KOPPER_DISABLE=true

exec taskset 0x7c $(pwd)/steamwebhelper "$@" &> "$WISP_HELPER_LOG"
'''
NEED_CORES = 4


@unittest.skipIf(os.cpu_count() < 7, 'needs at least 7 cores to tell Valve\'s 2-6 mask from 0-3')
class Behaviour(unittest.TestCase):
    def setUp(self):
        (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
        self.d = Path(tempfile.mkdtemp(prefix='wispos-tasksetshim-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, self.d, True)
        self.bin = self.d / 'bin'; self.bin.mkdir(); shutil.copy(SHIM, self.bin / 'taskset'); (self.bin / 'taskset').chmod(0o755)
        # the shim finds the real taskset at /usr/bin/taskset (the image's util-linux); this host has it there too
        self.assertTrue(os.access('/usr/bin/taskset', os.X_OK), 'the real taskset is needed')
        self.helper_dir = self.d / 'steam' / 'steamrtarm64'; self.helper_dir.mkdir(parents=True)
        self.log = self.d / 'helper.log'
        helper = self.helper_dir / 'steamwebhelper'
        helper.write_text('#!/bin/bash\nprintf "ARGS:"; for a in "$@"; do printf " [%s]" "$a"; done; echo\ngrep Cpus_allowed_list /proc/self/status\n'); helper.chmod(0o755)
        wrapper = self.helper_dir / 'steamwebhelper.sh'; wrapper.write_text(VALVE_WRAPPER); wrapper.chmod(0o755)
        self.wrapper = wrapper

    def run_wrapper(self, *args, path_first=True):
        path = ('%s:' % self.bin if path_first else '') + os.environ['PATH']
        subprocess.run([str(self.wrapper), *args], env={**os.environ, 'PATH': path, 'DISPLAY': ':0', 'WISP_HELPER_LOG': str(self.log), 'HOME': str(self.d)}, check=True, timeout=30)
        return self.log.read_text()

    def test_the_helper_runs_on_cores_0_to_3_with_the_switches_in_front_of_steams_own_arguments(self):
        out = self.run_wrapper('-nocrashdialog', '-lang=en_US', '--type=zygote')
        self.assertIn('ARGS: [--enable-gpu-rasterization] [--enable-zero-copy] [--enable-low-end-device-mode] [-nocrashdialog] [-lang=en_US] [--type=zygote]', out)
        self.assertIn('Cpus_allowed_list:\t0-3', out)

    def test_without_the_shim_valves_wrapper_pins_the_helper_to_cores_2_to_6_and_adds_no_switches(self):
        out = self.run_wrapper('-nocrashdialog', path_first=False)
        self.assertIn('ARGS: [-nocrashdialog]', out); self.assertIn('Cpus_allowed_list:\t2-6', out)      # the baseline this fixes

    def test_arguments_with_spaces_and_empty_ones_arrive_unchanged(self):
        out = self.run_wrapper('--user-data-dir=/a b/c', '', '--x=1')
        self.assertIn('[--enable-low-end-device-mode] [--user-data-dir=/a b/c] [] [--x=1]', out)

    def test_every_other_use_of_taskset_passes_straight_through(self):
        env = {**os.environ, 'PATH': '%s:%s' % (self.bin, os.environ['PATH'])}
        r = subprocess.run(['taskset', '-c', '1', 'bash', '-c', 'grep Cpus_allowed_list /proc/self/status'], env=env, capture_output=True, text=True, timeout=30)
        self.assertIn('Cpus_allowed_list:\t1', r.stdout, r.stderr)                                              # a game's own launch option keeps its own mask
        r = subprocess.run(['taskset', '-p', str(os.getpid())], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr); self.assertIn('current affinity', r.stdout)                # one argument or flags only: untouched
        other = self.d / 'someprogram'; other.write_text('#!/bin/bash\necho "ARGS: $*"\n'); other.chmod(0o755)
        r = subprocess.run(['taskset', '0x3', str(other), 'a', 'b'], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.stdout.strip(), 'ARGS: a b', r.stderr)                                               # a different program gets no switches

    def test_a_program_merely_containing_the_name_is_not_the_helper(self):
        env = {**os.environ, 'PATH': '%s:%s' % (self.bin, os.environ['PATH'])}
        for name in ('my-steamwebhelper-test', 'old-steamwebhelper', 'steamwebhelper2'):
            other = self.d / name; other.write_text('#!/bin/bash\necho "ARGS: $*"\n'); other.chmod(0o755)
            r = subprocess.run(['taskset', '0x7c', str(other), 'x'], env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(r.stdout.strip(), 'ARGS: x', (name, r.stderr))


class Wiring(unittest.TestCase):
    def test_the_session_puts_the_shim_first_on_path_and_never_edits_valves_wrapper(self):
        session = (HERE / 'rootfs/usr/bin/wisp-steam-session').read_text()
        lines = [l.strip() for l in session.splitlines() if l.strip().startswith('export PATH=')]
        self.assertEqual(lines, ['export PATH="/usr/lib/wispos/bin:$PATH"'])
        self.assertLess(session.index('export PATH="/usr/lib/wispos/bin:$PATH"'), session.index('steamrtarm64/steam" -gamepadui'))
        self.assertNotIn('steamwebhelper.sh', session)

    def test_the_shim_is_in_the_image_overlay_and_executable(self):
        self.assertTrue(os.access(SHIM, os.X_OK)); self.assertEqual(SHIM.read_text().splitlines()[0], '#!/bin/sh')

    def test_the_installer_leaves_valves_wrapper_alone(self):
        installer = (HERE / 'rootfs/usr/lib/wispos/steam-install').read_text()
        self.assertNotIn('apply_affinity_patch', installer); self.assertNotIn('NEW_AFFINITY', installer)


class Mutants(unittest.TestCase):
    MUTANTS = [
        ("exec \"$real\" 0x0f \"$helper\"", "exec \"$real\" 0x7c \"$helper\""),
        (" --enable-gpu-rasterization", ""),
        (" --enable-zero-copy", ""),
        (" --enable-low-end-device-mode", ""),
        ('[ "${2##*/}" = steamwebhelper ]', '[ "${2##*steamwebhelper}" = "" ]'),
        ('exec "$real" "$@"\n', 'exec "$real" 0x0f "$@"\n'),
        ('"$helper" --enable-gpu-rasterization --enable-zero-copy --enable-low-end-device-mode "$@"', '"$helper" "$@" --enable-gpu-rasterization --enable-zero-copy --enable-low-end-device-mode'),
        ('helper=$2; shift 2', 'helper=$2; shift 1'),
    ]

    @unittest.skipIf(os.cpu_count() < 7, 'needs at least 7 cores')
    def test_every_mutant_is_caught(self):
        src = SHIM.read_text(); survivors = []
        (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
        d = Path(tempfile.mkdtemp(prefix='wispos-tasksetshim-mut-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, d, True)
        for old, new in self.MUTANTS:
            self.assertEqual(src.count(old), 1, 'mutation anchor not found exactly once: %r' % old)
            m = d / 'taskset'; m.write_text(src.replace(old, new)); m.chmod(0o755)
            r = subprocess.run([sys.executable, '-m', 'unittest', '-q', 'test_taskset_shim.Behaviour'], cwd=HERE, capture_output=True, text=True, timeout=300, env={**os.environ, 'WISP_TASKSET_SHIM': str(m)})
            if r.returncode == 0: survivors.append(old)
        self.assertEqual(survivors, [], 'mutants that no test noticed')


if __name__ == '__main__':
    unittest.main()
