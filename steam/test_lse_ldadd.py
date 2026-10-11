"""Test the narrow LDADDAL semantic core using native x86_64 calls only.

These checks cover decoding, architectural register effects and atomic memory
updates. They cannot validate ARM SIGILL delivery or signal-return behavior.
Instruction literals describe independent fixture cases rather than deriving
an expected decoder from the implementation's instruction mask.
"""
from concurrent.futures import ThreadPoolExecutor
import ctypes
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import threading
import unittest


U64 = ctypes.c_uint64
REGS = U64 * 31
MASK64 = (1 << 64) - 1


@unittest.skipUnless(platform.system() == 'Linux' and platform.machine() == 'x86_64',
                     'semantic tests execute only native Linux x86_64 code')
class LseLdaddTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gcc = shutil.which('gcc')
        if gcc is None:
            raise unittest.SkipTest('native gcc unavailable; no packages installed')
        temporary = tempfile.TemporaryDirectory(prefix='wispos-ldadd-test-')
        cls.addClassCleanup(temporary.cleanup)
        library = Path(temporary.name) / 'lse-semantic.so'
        source = Path(__file__).with_name('lse-ldadd.c')
        subprocess.run([gcc, '-std=c11', '-O2', '-Wall', '-Wextra', '-fPIC',
                        '-shared', '-mcx16', '-DWISPOS_LSE_HOST_TEST', str(source),
                        '-o', str(library)], check=True, capture_output=True, text=True)
        # CDLL releases the GIL around each C call: concurrent tests exercise
        # actual simultaneous atomic updates rather than Python serialization.
        cls.library = ctypes.CDLL(str(library))
        cls.emulate = cls.library.wispos_ldadd
        cls.emulate.argtypes = (ctypes.c_uint32, ctypes.POINTER(U64), U64)
        cls.emulate.restype = ctypes.c_int

    def registers(self):
        return REGS(*[(0x1020304050607080 + i * 0x100000001) & MASK64
                      for i in range(31)])

    def assert_only_destination_changed(self, before, after, destination, value):
        expected = list(before)
        if destination != 31:
            expected[destination] = value
        self.assertEqual(list(after), expected)

    def test_measured_x0_base_destination_alias(self):
        memory = U64(0x123456789abcdef0)
        regs = self.registers()
        regs[0] = ctypes.addressof(memory)
        regs[1] = 13
        before = list(regs)
        # Captured instruction: LDADDAL x1, x0, [x0].
        self.assertEqual(self.emulate(0xf8e10000, regs, 0), 1)
        self.assertEqual(memory.value, 0x123456789abcdefd)
        self.assert_only_destination_changed(before, regs, 0, 0x123456789abcdef0)

    def test_arbitrary_64_bit_operands_and_register_preservation(self):
        memory = U64(0x0102030405060708)
        regs = self.registers()
        regs[5] = ctypes.addressof(memory)
        regs[7] = 0x8877665544332211
        before = list(regs)
        # LDADDAL x7, x9, [x5].
        self.assertEqual(self.emulate(0xf8e700a9, regs, 0), 1)
        self.assertEqual(memory.value, 0x8979695949392919)
        self.assert_only_destination_changed(before, regs, 9, 0x0102030405060708)

    def test_32_bit_result_zero_extends_and_preserves_adjacent_memory(self):
        class Words(ctypes.Structure):
            _fields_ = [('value', ctypes.c_uint32), ('guard', ctypes.c_uint32)]
        memory = Words(0x80000001, 0xfeedface)
        regs = self.registers()
        regs[5] = ctypes.addressof(memory)
        regs[7] = 0x1122334400000002
        before = list(regs)
        # LDADDAL w7, w9, [x5]: high bits of x7 are not added.
        self.assertEqual(self.emulate(0xb8e700a9, regs, 0), 1)
        self.assertEqual(memory.value, 0x80000003)
        self.assertEqual(memory.guard, 0xfeedface)
        self.assert_only_destination_changed(before, regs, 9, 0x80000001)

    def test_unsigned_wrap_for_both_widths(self):
        for opcode, cell_type, width in ((0xf8e700a9, U64, 64),
                                         (0xb8e700a9, ctypes.c_uint32, 32)):
            with self.subTest(width=width):
                maximum = (1 << width) - 1
                memory = cell_type(maximum - 1)
                regs = self.registers()
                regs[5] = ctypes.addressof(memory)
                regs[7] = 5
                before = list(regs)
                self.assertEqual(self.emulate(opcode, regs, 0), 1)
                self.assertEqual(memory.value, 3)
                self.assert_only_destination_changed(before, regs, 9, maximum - 1)

    def test_register_aliases_use_original_source_and_address(self):
        cases = ((0xf8e700a7, 7, 7, 'source equals destination'),
                 (0xf8e500a9, 5, 9, 'source equals base'),
                 (0xf8e500a5, 5, 5, 'all registers overlap'))
        for opcode, source, destination, label in cases:
            with self.subTest(alias=label):
                memory = U64(123)
                regs = self.registers()
                regs[5] = ctypes.addressof(memory)
                if source != 5:
                    regs[source] = 17
                before = list(regs)
                expected = (memory.value + regs[source]) & MASK64
                self.assertEqual(self.emulate(opcode, regs, 0), 1)
                self.assertEqual(memory.value, expected)
                self.assert_only_destination_changed(before, regs, destination, 123)

    def test_source_zero_destination_discard_and_stack_pointer(self):
        cases = ((0xf8ff00a9, 31, 5, 9, 'xzr source'),
                 (0xf8e700bf, 7, 5, 31, 'xzr destination'),
                 (0xf8e703e9, 7, 31, 9, 'sp base'),
                 (0xf8ff03ff, 31, 31, 31, 'all register fields 31'))
        for opcode, source, base, destination, label in cases:
            with self.subTest(case=label):
                memory = U64(701)
                regs = self.registers()
                regs[7] = 9
                stack_pointer = ctypes.addressof(memory) if base == 31 else 0
                if base != 31:
                    regs[base] = ctypes.addressof(memory)
                before = list(regs)
                self.assertEqual(self.emulate(opcode, regs, stack_pointer), 1)
                self.assertEqual(memory.value, 701 + (0 if source == 31 else 9))
                self.assert_only_destination_changed(before, regs, destination, 701)

    def test_non_acquire_release_ldadd_variants_supported(self):
        # Plain LDADD appears twice in the pinned Steam ELF (0xb8200020 at
        # 0x731ae0, 0xf8200020 at 0x731b10); A and L variants share its encoding.
        cases = ((0xf82700a9, 'LDADD x7,x9,[x5]', 8), (0xf8a700a9, 'LDADDA', 8),
                 (0xf86700a9, 'LDADDL', 8), (0xb82700a9, 'LDADD w', 4),
                 (0xb8a700a9, 'LDADDA w', 4), (0xb86700a9, 'LDADDL w', 4))
        for opcode, label, width in cases:
            with self.subTest(opcode=label):
                memory = U64(0x1111222233334444)
                regs = self.registers()
                regs[5] = ctypes.addressof(memory)
                regs[7] = 0x10
                before = list(regs)
                original = memory.value & (MASK64 if width == 8 else 0xffffffff)
                self.assertEqual(self.emulate(opcode, regs, 0), 1)
                expected = (original + 0x10) & (MASK64 if width == 8 else 0xffffffff)
                self.assertEqual(memory.value & (MASK64 if width == 8 else 0xffffffff), expected)
                self.assert_only_destination_changed(before, regs, 9, original)

    def test_unknown_opcodes_declined_without_memory_or_register_changes(self):
        cases = ((0xc8e7fca9, 'CASAL'),
                 (0xf8e720a9, 'LDEORAL'), (0xf8e780a9, 'SWPAL'),
                 (0x38e700a9, 'LDADDALB'), (0x78e700a9, 'LDADDALH'),
                 (0xf94000a9, 'LDR'), (0xf90000a9, 'STR'),
                 (0xd503201f, 'NOP'), (0xffffffff, 'invalid'))
        for opcode, label in cases:
            with self.subTest(opcode=label):
                memory = U64(701)
                regs = self.registers()
                regs[5] = ctypes.addressof(memory)
                regs[7] = 9
                before = list(regs)
                self.assertEqual(self.emulate(opcode, regs, 0), 0)
                self.assertEqual(memory.value, 701)
                self.assertEqual(list(regs), before)

    def test_unaligned_pointer_declined_before_access(self):
        for opcode, alignment in ((0xf8e700a9, 8), (0xb8e700a9, 4)):
            with self.subTest(alignment=alignment):
                memory = ctypes.create_string_buffer(b'0123456789abcdef', 32)
                pointer = ctypes.addressof(memory) + 1
                self.assertNotEqual(pointer % alignment, 0)
                regs = self.registers()
                regs[5] = pointer
                regs[7] = 9
                before = list(regs)
                memory_before = memory.raw
                self.assertEqual(self.emulate(opcode, regs, 0), 0)
                self.assertEqual(memory.raw, memory_before)
                self.assertEqual(list(regs), before)

    def test_concurrent_updates_return_distinct_old_values(self):
        workers, increments = 8, 1500
        memory = U64(0)
        pointer = ctypes.addressof(memory)
        self.assertEqual(pointer % 8, 0)
        start = threading.Barrier(workers)

        def add_many():
            regs = self.registers()
            regs[5], regs[7] = pointer, 1
            start.wait(timeout=10)
            results = []
            for _ in range(increments):
                status = self.emulate(0xf8e700a9, regs, 0)
                if status != 1:
                    raise AssertionError(f'concurrent emulation returned {status}')
                results.append(regs[9])
            return results

        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(lambda _: add_many(), range(workers)))
        old_values = [value for group in results for value in group]
        self.assertEqual(memory.value, workers * increments)
        self.assertEqual(sorted(old_values), list(range(workers * increments)))


if __name__ == '__main__':
    unittest.main()
