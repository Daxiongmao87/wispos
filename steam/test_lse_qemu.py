"""Run the LSE fallback on emulated ARMv8.0 hardware (QEMU user mode, cortex-a53).

Executes Steam's real LSE instruction words through the real SIGILL handler;
see lse-qemu-test.c. qemu-aarch64-static is invoked by explicit path only: this
test never installs packages and never touches binfmt_misc. It is skipped, with
the reason stated, if the cross compiler or QEMU is unavailable. It does not
prove behavior on the physical Cortex-A53 or inside Steam.
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

QEMU = '/usr/bin/qemu-aarch64-static'


class LseQemuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gcc = shutil.which('aarch64-linux-gnu-gcc')
        if gcc is None or not os.access(QEMU, os.X_OK):
            raise unittest.SkipTest('aarch64 cross compiler or %s unavailable' % QEMU)
        probe = subprocess.run([QEMU, '-cpu', 'help'], capture_output=True, text=True)
        if 'cortex-a53' not in probe.stdout + probe.stderr:
            raise unittest.SkipTest('this QEMU has no cortex-a53 CPU model')
        temporary = tempfile.TemporaryDirectory(prefix='wispos-qemu-test-')
        cls.addClassCleanup(temporary.cleanup)
        cls.binary = Path(temporary.name) / 'lse-qemu-test'
        source = Path(__file__).with_name('lse-qemu-test.c')
        subprocess.run([gcc, '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-static', '-pthread', '-march=armv8-a', '-mtune=cortex-a53',
                        '-mno-outline-atomics', '-DWISPOS_QEMU_USER_TEST',
                        '-DWISPOS_STATIC_TEST', str(source), '-o', str(cls.binary)],
                       check=True, capture_output=True, text=True)

    def test_handler_survives_programs_own_sigill_registration(self):
        """The breakpad scenario: the real shared preload, a dynamic program."""
        gcc = shutil.which('aarch64-linux-gnu-gcc')
        sysroot = Path('/usr/aarch64-linux-gnu')
        if not (sysroot / 'lib/ld-linux-aarch64.so.1').exists():
            self.skipTest('aarch64 cross sysroot unavailable')
        directory = self.binary.parent
        preload, program = directory / 'preload-qemu.so', directory / 'chain-test'
        flags = ['-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror', '-march=armv8-a',
                 '-mtune=cortex-a53', '-mno-outline-atomics']
        here = Path(__file__).parent
        subprocess.run([gcc, *flags, '-shared', '-fPIC', '-Wl,-z,now', '-DWISPOS_QEMU_USER_TEST',
                        str(here / 'lse-ldadd.c'), '-o', str(preload)],
                       check=True, capture_output=True, text=True)
        subprocess.run([gcc, *flags, '-pthread', str(here / 'lse-qemu-chain-test.c'), '-o', str(program)],
                       check=True, capture_output=True, text=True)
        result = subprocess.run([QEMU, '-cpu', 'cortex-a53', '-L', str(sysroot),
                                 '-E', 'LD_PRELOAD=%s' % preload, str(program)],
                                capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('ALL PASSED', result.stdout)
        self.assertIn('unsupported instruction 0xf8e12002', result.stderr)

    def test_every_measured_shape_emulated_on_armv8_0_cpu_model(self):
        result = subprocess.run([QEMU, '-cpu', 'cortex-a53', str(self.binary)],
                                capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('ALL PASSED', result.stdout)
        for family in ('LDADDAL', 'CASPAL128', 'SWP', 'CAS', 'LDUMAX16', 'LDADD', 'LDAPUR'):
            self.assertIn('WispOS %s fallback: first instruction emulated' % family,
                          result.stderr)

    def test_cpu_model_has_no_native_lse(self):
        # The control child inside the program asserts SIGILL without the handler;
        # here the same fact is checked from outside via the full run's output.
        result = subprocess.run([QEMU, '-cpu', 'cortex-a53', str(self.binary)],
                                capture_output=True, text=True, timeout=300)
        self.assertGreaterEqual(result.stderr.count('uncaught target signal 4'), 4)


if __name__ == '__main__':
    unittest.main()
