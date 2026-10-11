"""Regression test: the SIGILL fallback must be installed before ANY library constructor can reach Steam's overlay hooks.

Defect (2026-10-10, docs/WISPOS-NOTES.md "Half-Life runs natively under FEX"): Steam preloads gameoverlayrenderer.so, which hooks libc close()/open()/read()/dlopen()/dlsym()
with LSE atomics. A program whose LIBRARY constructors call close() (Python through libGL/libX11/libpython) ran that hook before the preload's own constructor existed
(dependencies initialise first), so it died with SIGILL before running a line of code, and Valve's Python launcher for native Linux games never started. The fix is linking the
fallback with -z initfirst (its constructor runs before every other object's).

The test double overlay (lse-initfirst-overlay.c) hooks close() with a hand-encoded casal; lse-initfirst-early.c calls close() from a constructor of a dependency. On the QEMU
cortex-a53 model (no LSE) the preload built WITHOUT initfirst must die with SIGILL and the one built WITH it must emulate the casal and reach main. Skipped, with the reason,
when the cross compiler, qemu-aarch64-static or the aarch64 sysroot is missing. Never installs packages, never touches binfmt_misc.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

QEMU = '/usr/bin/qemu-aarch64-static'
SYSROOT = Path('/usr/aarch64-linux-gnu')
HERE = Path(__file__).parent


class InitFirstTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gcc = shutil.which('aarch64-linux-gnu-gcc')
        if gcc is None or not os.access(QEMU, os.X_OK) or not (SYSROOT / 'lib/ld-linux-aarch64.so.1').exists():
            raise unittest.SkipTest('aarch64 cross compiler, %s or the aarch64 sysroot unavailable' % QEMU)
        probe = subprocess.run([QEMU, '-cpu', 'help'], capture_output=True, text=True)
        if 'cortex-a53' not in probe.stdout + probe.stderr:
            raise unittest.SkipTest('this QEMU has no cortex-a53 CPU model')
        temporary = tempfile.TemporaryDirectory(prefix='wispos-initfirst-')
        cls.addClassCleanup(temporary.cleanup)
        d = cls.directory = Path(temporary.name)
        flags = ['-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror', '-march=armv8-a', '-mtune=cortex-a53', '-mno-outline-atomics']
        run = lambda *a: subprocess.run([gcc, *a], check=True, capture_output=True, text=True)
        run(*flags, '-shared', '-fPIC', str(HERE / 'lse-initfirst-overlay.c'), '-o', str(d / 'overlay.so'), '-ldl')
        run(*flags, '-shared', '-fPIC', str(HERE / 'lse-initfirst-early.c'), '-o', str(d / 'libearly.so'))
        run(*flags, str(HERE / 'lse-initfirst-main.c'), '-o', str(d / 'main'), '-L%s' % d, '-learly', '-Wl,-rpath,%s' % d)
        for name, extra in (('plain', []), ('initfirst', ['-Wl,-z,initfirst'])):
            run(*flags, '-shared', '-fPIC', '-Wl,-z,now', *extra, '-DWISPOS_QEMU_USER_TEST', str(HERE / 'lse-ldadd.c'), '-o', str(d / ('preload-%s.so' % name)))

    def run_program(self, preload):
        # The preload is listed first, the overlay after it: Steam's own order for a game launch.
        return subprocess.run([QEMU, '-cpu', 'cortex-a53', '-L', str(SYSROOT), '-E', 'LD_PRELOAD=%s:%s' % (self.directory / preload, self.directory / 'overlay.so'),
                               str(self.directory / 'main')], capture_output=True, text=True, timeout=120)

    def test_without_initfirst_the_overlay_hook_kills_the_program_before_main(self):
        result = self.run_program('preload-plain.so')
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('main reached', result.stdout)

    def test_with_initfirst_the_hook_is_emulated_and_main_runs(self):
        result = self.run_program('preload-initfirst.so')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('main reached', result.stdout)
        self.assertIn('WispOS CAS fallback: first instruction emulated', result.stderr)

    def test_the_shipped_build_command_links_initfirst(self):
        text = (HERE.parent / 'os/build-rootfs.sh').read_text()
        self.assertIn('-Wl,-z,initfirst', text)


if __name__ == '__main__':
    unittest.main()
