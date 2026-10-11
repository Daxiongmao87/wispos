"""Exercise the narrow CASPAL128 core with native x86_64 code only.

Instruction literals were checked with GNU's AArch64 assembler/disassembler.
These tests check atomic pair semantics and architectural register effects;
they do not execute ARM code or validate ARM signal delivery and return.
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
class LseCaspalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gcc = shutil.which('gcc')
        if gcc is None:
            raise unittest.SkipTest('native gcc unavailable; no packages installed')
        temporary = tempfile.TemporaryDirectory(prefix='wispos-caspal-test-')
        cls.addClassCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        source = directory / 'wrapper.c'
        source.write_text('#include "lse-caspal.h"\n'
                          'int run_caspal(uint32_t instruction, uint64_t regs[31], '
                          'uint64_t sp) {\n'
                          '    return wispos_caspal128(instruction, regs, sp);\n}\n')
        library = directory / 'caspal-semantic.so'
        subprocess.run([gcc, '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-fPIC', '-shared', '-mcx16', '-DWISPOS_LSE_HOST_TEST',
                        '-I', str(Path(__file__).parent), str(source),
                        '-o', str(library)], check=True, capture_output=True, text=True)
        # CDLL releases the GIL around C calls, so the concurrency check really
        # exercises competing native atomic operations.
        cls.library = ctypes.CDLL(str(library))
        cls.emulate = cls.library.run_caspal
        cls.emulate.argtypes = (ctypes.c_uint32, ctypes.POINTER(U64), U64)
        cls.emulate.restype = ctypes.c_int

    def registers(self):
        return REGS(*[(0x1020304050607080 + i * 0x100000001) & MASK64
                      for i in range(31)])

    def pair(self, low, high):
        # ctypes' uint64 arrays guarantee only eight-byte alignment. Retain
        # the backing buffer explicitly and choose a sixteen-byte boundary.
        buffer = ctypes.create_string_buffer(b'\xa5' * 80, 80)
        pointer = (ctypes.addressof(buffer) + 31) & ~15
        memory = (U64 * 2).from_address(pointer)
        memory[:] = low, high
        return buffer, memory, pointer

    def assert_comparison_result(self, before, regs, compare, low, high):
        expected = list(before)
        expected[compare] = low
        if compare + 1 != 31:
            expected[compare + 1] = high
        self.assertEqual(list(regs), expected)

    def test_measured_opcode_replaces_matching_pair_and_returns_both_old_halves(self):
        buffer, memory, pointer = self.pair(0xfedcba9876543210, 0x0123456789abcdef)
        regs = self.registers()
        regs[0] = pointer
        regs[2], regs[3] = memory
        regs[6], regs[7] = 0x8877665544332211, 0xff00ee00dd00cc00
        before = list(regs)
        guards = ctypes.string_at(pointer - 16, 16), ctypes.string_at(pointer + 16, 16)
        # Physical capture: CASPAL x2, x3, x6, x7, [x0].
        self.assertEqual(self.emulate(0x4862fc06, regs, 0), 1)
        self.assertEqual(list(memory), [0x8877665544332211, 0xff00ee00dd00cc00])
        self.assert_comparison_result(before, regs, 2, 0xfedcba9876543210,
                                      0x0123456789abcdef)
        self.assertEqual((ctypes.string_at(pointer - 16, 16),
                          ctypes.string_at(pointer + 16, 16)), guards)
        self.assertEqual(len(buffer), 80)

    def test_mismatch_in_either_half_returns_both_old_halves_without_replacement(self):
        for low, high in ((17, 29), (18, 28), (18, 29)):
            with self.subTest(compare=(low, high)):
                buffer, memory, pointer = self.pair(17, 28)
                regs = self.registers()
                regs[0], regs[2], regs[3] = pointer, low, high
                regs[6], regs[7] = 101, 202
                before = list(regs)
                self.assertEqual(self.emulate(0x4862fc06, regs, 0), 1)
                self.assertEqual(list(memory), [17, 28])
                self.assert_comparison_result(before, regs, 2, 17, 28)

    def test_arbitrary_even_pairs_preserve_all_other_registers(self):
        buffer, memory, pointer = self.pair(MASK64, 0)
        regs = self.registers()
        regs[10], regs[8], regs[9] = pointer, MASK64, 0
        regs[14], regs[15] = 0, MASK64
        before = list(regs)
        # CASPAL x8, x9, x14, x15, [x10].
        self.assertEqual(self.emulate(0x4868fd4e, regs, 0), 1)
        self.assertEqual(list(memory), [0, MASK64])
        self.assert_comparison_result(before, regs, 8, MASK64, 0)

    def test_address_aliases_snapshot_comparison_and_replacement_operands(self):
        for opcode, base in ((0x4862fc46, 2), (0x4862fc66, 3),
                             (0x4862fcc6, 6), (0x4862fce6, 7)):
            with self.subTest(base=base):
                buffer, memory, pointer = self.pair(17, 28)
                regs = self.registers()
                regs[2], regs[3], regs[6], regs[7] = 17, 28, 101, 202
                regs[base] = pointer
                if base in (2, 3):
                    memory[base - 2] = pointer
                before = list(regs)
                old = list(memory)
                replacement = [regs[6], regs[7]]
                self.assertEqual(self.emulate(opcode, regs, 0), 1)
                self.assertEqual(list(memory), replacement)
                self.assert_comparison_result(before, regs, 2, *old)

    def test_identical_comparison_and_replacement_pairs_use_original_values(self):
        for match in (True, False):
            with self.subTest(match=match):
                buffer, memory, pointer = self.pair(17, 28)
                regs = self.registers()
                regs[0], regs[2], regs[3] = pointer, 17 if match else 99, 28
                before = list(regs)
                # CASPAL x2, x3, x2, x3, [x0].
                self.assertEqual(self.emulate(0x4862fc02, regs, 0), 1)
                self.assertEqual(list(memory), [17, 28])
                self.assert_comparison_result(before, regs, 2, 17, 28)

    def test_pair_start_30_uses_zero_and_discards_register_31(self):
        cases = ((0x487efc06, 30, 6, 0),
                 (0x4862fc1e, 2, 30, 0),
                 (0x487efffe, 30, 30, 31))
        for opcode, compare, replacement, base in cases:
            for high in (0, 37):
                with self.subTest(compare=compare, replacement=replacement,
                                  base=base, memory_high=high):
                    buffer, memory, pointer = self.pair(17, high)
                    regs = self.registers()
                    regs[compare] = 17
                    if compare != 30:
                        regs[compare + 1] = high
                    if replacement != compare:
                        regs[replacement] = 101
                        if replacement != 30:
                            regs[replacement + 1] = 202
                    if base != 31:
                        regs[base] = pointer
                    before = list(regs)
                    matched = compare != 30 or high == 0
                    new = [regs[replacement], 0 if replacement == 30
                           else regs[replacement + 1]]
                    self.assertEqual(self.emulate(opcode, regs,
                                                 pointer if base == 31 else 0), 1)
                    self.assertEqual(list(memory), new if matched else [17, high])
                    self.assert_comparison_result(before, regs, compare, 17, high)

    def test_other_opcodes_orderings_widths_reserved_fields_and_odd_pairs_declined(self):
        cases = ((0x0862fc06, '32-bit pair'), (0x48227c06, 'CASP'),
                 (0x48627c06, 'CASPA'), (0x4822fc06, 'CASPL'),
                 (0xc8e2fc06, 'CASAL scalar'), (0xf8e10000, 'LDADDAL'),
                 (0x48e2fc06, 'reserved bit 23'), (0x4962fc06, 'wrong bit 24'),
                 (0x4842fc06, 'wrong bit 21'), (0x4862f806, 'reserved field'),
                 (0x4863fc06, 'odd comparison pair'),
                 (0x4862fc07, 'odd replacement pair'),
                 (0x487ffc06, 'comparison starts at 31'),
                 (0x4862fc1f, 'replacement starts at 31'),
                 (0xd503201f, 'NOP'), (0xffffffff, 'invalid'))
        for opcode, label in cases:
            with self.subTest(opcode=label):
                buffer, memory, pointer = self.pair(17, 28)
                regs = self.registers()
                regs[0], regs[2], regs[3], regs[6], regs[7] = pointer, 17, 28, 101, 202
                before, bytes_before = list(regs), buffer.raw
                self.assertEqual(self.emulate(opcode, regs, 0), 0)
                self.assertEqual(list(regs), before)
                self.assertEqual(buffer.raw, bytes_before)

    def test_every_non_16_byte_alignment_declined_before_memory_access(self):
        for displacement in range(1, 16):
            with self.subTest(displacement=displacement):
                buffer, memory, pointer = self.pair(17, 28)
                regs = self.registers()
                regs[0], regs[2], regs[3], regs[6], regs[7] = (
                    pointer + displacement, 17, 28, 101, 202)
                before, bytes_before = list(regs), buffer.raw
                self.assertEqual(self.emulate(0x4862fc06, regs, 0), 0)
                self.assertEqual(list(regs), before)
                self.assertEqual(buffer.raw, bytes_before)

    def test_concurrent_pair_increments_have_no_torn_halves_or_duplicate_successes(self):
        workers, increments = 8, 1000
        buffer, memory, pointer = self.pair(0, MASK64)
        start = threading.Barrier(workers)

        def increment_many():
            regs = self.registers()
            regs[0] = pointer
            expected, successes, retries = 0, [], 0
            start.wait(timeout=10)
            while len(successes) < increments:
                regs[2], regs[3] = expected, expected ^ MASK64
                regs[6], regs[7] = expected + 1, (expected + 1) ^ MASK64
                if self.emulate(0x4862fc06, regs, 0) != 1:
                    raise AssertionError('concurrent CASPAL declined')
                old_low, old_high = regs[2], regs[3]
                if old_high != (old_low ^ MASK64):
                    raise AssertionError(f'torn pair: {old_low:#x}, {old_high:#x}')
                if old_low == expected:
                    successes.append(old_low)
                    expected += 1
                else:
                    expected = old_low
                retries += 1
                if retries > 1000000:
                    raise AssertionError('CAS loop made insufficient progress')
            return successes

        with ThreadPoolExecutor(max_workers=workers) as executor:
            groups = list(executor.map(lambda _: increment_many(), range(workers)))
        successes = [value for group in groups for value in group]
        total = workers * increments
        self.assertEqual(list(memory), [total, total ^ MASK64])
        self.assertEqual(sorted(successes), list(range(total)))
        self.assertEqual(len(buffer), 80)


if __name__ == '__main__':
    unittest.main()
