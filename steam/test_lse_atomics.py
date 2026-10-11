"""Test the SWP, CAS and LDUMAX-halfword semantic cores using native x86_64 calls.

Like test_lse_ldadd.py these cover decoding, register effects and atomic memory
updates only; they cannot validate ARM SIGILL delivery (see test_lse_qemu.py).
Instruction words are fixtures taken from the hash-pinned Steam ELF scan
(first site of each shape) or hand-assembled variants, not derived from the
implementation's masks.
"""
from concurrent.futures import ThreadPoolExecutor
import ctypes
from pathlib import Path
import platform
import shutil
import subprocess
import tempfile
import unittest

U64, U32, U16 = ctypes.c_uint64, ctypes.c_uint32, ctypes.c_uint16
REGS = U64 * 31
MASK64 = (1 << 64) - 1


@unittest.skipUnless(platform.system() == 'Linux' and platform.machine() == 'x86_64',
                     'semantic tests execute only native Linux x86_64 code')
class LseAtomicsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gcc = shutil.which('gcc')
        if gcc is None:
            raise unittest.SkipTest('native gcc unavailable; no packages installed')
        temporary = tempfile.TemporaryDirectory(prefix='wispos-atomics-test-')
        cls.addClassCleanup(temporary.cleanup)
        library = Path(temporary.name) / 'lse-semantic.so'
        subprocess.run([gcc, '-std=c11', '-O2', '-Wall', '-Wextra', '-fPIC', '-shared',
                        '-mcx16', '-DWISPOS_LSE_HOST_TEST',
                        str(Path(__file__).with_name('lse-ldadd.c')), '-o', str(library)],
                       check=True, capture_output=True, text=True)
        lib = ctypes.CDLL(str(library))
        for name in ('wispos_swp', 'wispos_cas', 'wispos_ldumax16', 'wispos_ldapur',
                     'wispos_lse_emulate'):
            fn = getattr(lib, name)
            fn.argtypes = (ctypes.c_uint32, ctypes.POINTER(U64), U64)
            fn.restype = ctypes.c_int
            setattr(cls, name[len('wispos_'):], fn)

    def registers(self):
        return REGS(*[(0x1020304050607080 + i * 0x100000001) & MASK64 for i in range(31)])

    def only_changed(self, before, after, changes):
        expected = list(before)
        for index, value in changes.items():
            if index != 31:
                expected[index] = value
        self.assertEqual(list(after), expected)

    # ---- SWP -------------------------------------------------------------
    def test_measured_swpa_w1_w1_x0_alias(self):
        # Steam 0xe11b0: swpa w1, w1, [x0]; Rs and Rt alias, x1 is zero-extended.
        memory = U32(0xcafef00d)
        regs = self.registers()
        regs[0] = ctypes.addressof(memory)
        regs[1] = 0xffffffff00000007
        before = list(regs)
        self.assertEqual(self.swp(0xb8a18001, regs, 0), 1)
        self.assertEqual(memory.value, 7)
        self.only_changed(before, regs, {1: 0xcafef00d})

    def test_measured_swpa_64_bit(self):
        # Steam 0xc37ac: 0xf8bf8260 = swpa xzr, x0, [x19]: stores zero, returns old.
        memory = U64(0x0123456789abcdef)
        regs = self.registers()
        regs[19] = ctypes.addressof(memory)
        before = list(regs)
        self.assertEqual(self.swp(0xf8bf8260, regs, 0), 1)
        self.assertEqual(memory.value, 0)
        self.only_changed(before, regs, {0: 0x0123456789abcdef})

    def test_swp_ordering_variants_share_semantics(self):
        for word, label in ((0xb8208041, 'SWP'), (0xb8a08041, 'SWPA'),
                            (0xb8608041, 'SWPL'), (0xb8e08041, 'SWPAL')):
            with self.subTest(label):
                memory = U32(5)
                regs = self.registers()
                regs[2] = ctypes.addressof(memory)
                regs[0] = 9
                before = list(regs)
                self.assertEqual(self.swp((word & ~0x3e0) | (2 << 5), regs, 0), 1)
                self.assertEqual(memory.value, 9)
                self.only_changed(before, regs, {1: 5})

    def test_swp_sp_base_and_zero_destination_discards(self):
        memory = U64(41)
        regs = self.registers()
        regs[3] = 99
        before = list(regs)
        # swp x3, xzr, [sp]
        self.assertEqual(self.swp(0xf82383ff, regs, ctypes.addressof(memory)), 1)
        self.assertEqual(memory.value, 99)
        self.assertEqual(list(regs), before)

    def test_swp_unaligned_declined_before_access(self):
        buf = ctypes.create_string_buffer(b'0123456789abcdef', 32)
        regs = self.registers()
        regs[0] = ctypes.addressof(buf) + 1
        before, raw = list(regs), buf.raw
        self.assertEqual(self.swp(0xb8a18001, regs, 0), 0)
        self.assertEqual(buf.raw, raw)
        self.assertEqual(list(regs), before)

    # ---- CAS -------------------------------------------------------------
    def test_measured_casal_32_match_and_mismatch(self):
        # Steam 0xdda60: 0x88e0fe61 = casal w0, w1, [x19].
        for stored, expect_swap in ((100, True), (101, False)):
            with self.subTest(stored=stored):
                memory = U32(stored)
                regs = self.registers()
                regs[19] = ctypes.addressof(memory)
                regs[0] = 0xdead000000000064   # compare value is the low word: 100
                regs[1] = 0x1111111122222222
                before = list(regs)
                self.assertEqual(self.cas(0x88e0fe61, regs, 0), 1)
                self.assertEqual(memory.value, 0x22222222 if expect_swap else stored)
                self.only_changed(before, regs, {0: stored})

    def test_measured_casal_64_and_casl(self):
        for word, label in ((0xc8e0fe60, 'CASAL x0,x0,[x19]'), (0xc8a0fc41, 'CASL x0,x1,[x2]')):
            with self.subTest(label):
                memory = U64(0x8000000000000001)
                regs = self.registers()
                base = 19 if word == 0xc8e0fe60 else 2
                regs[base] = ctypes.addressof(memory)
                rs = (word >> 16) & 31
                rt = word & 31
                regs[rs] = 0x8000000000000001
                if rt != rs:
                    regs[rt] = 0x55
                before = list(regs)
                self.assertEqual(self.cas(word, regs, 0), 1)
                self.assertEqual(memory.value, regs[rt] if rt != rs else 0x8000000000000001)
                self.assertEqual(regs[rs], 0x8000000000000001)
                self.assertEqual([i for i in range(31) if regs[i] != before[i]], [])

    def test_cas_xzr_compare_and_discard(self):
        memory = U32(0)
        regs = self.registers()
        regs[5] = ctypes.addressof(memory)
        regs[2] = 0xabcd
        before = list(regs)
        # cas wzr, w2, [x5]: compares with zero, swaps, result discarded.
        self.assertEqual(self.cas(0x88bf7ca2, regs, 0), 1)
        self.assertEqual(memory.value, 0xabcd)
        self.assertEqual(list(regs), before)

    def test_cas_byte_halfword_and_pair_forms_declined(self):
        memory = U64(1)
        for word, label in ((0x08e0fe61, 'CASALB'), (0x48e0fe61, 'CASALH'),
                            (0x4860fc06, 'CASPAL (own handler)')):
            with self.subTest(label):
                regs = self.registers()
                regs[19] = ctypes.addressof(memory)
                before = list(regs)
                self.assertEqual(self.cas(word, regs, 0), 0)
                self.assertEqual(memory.value, 1)
                self.assertEqual(list(regs), before)

    def test_cas_concurrent_increment_has_no_lost_updates(self):
        memory = U64(0)
        address = ctypes.addressof(memory)

        def worker():
            for _ in range(1500):
                while True:
                    regs = self.registers()
                    regs[19] = address
                    seen = memory.value
                    regs[0], regs[1] = seen, seen + 1       # casal x0, x1, [x19]
                    self.cas(0xc8e0fe61, regs, 0)
                    if regs[0] == seen:
                        break
        with ThreadPoolExecutor(8) as pool:
            for future in [pool.submit(worker) for _ in range(8)]:
                future.result()
        self.assertEqual(memory.value, 8 * 1500)

    # ---- LDUMAX halfword -------------------------------------------------
    def test_measured_ldumaxah_updates_only_halfword(self):
        # Steam 0x6184e4: 0x78a5636f = ldumaxah w5, w15, [x27].
        buf = (U16 * 4)(0x1111, 0x2222, 0x3333, 0x4444)
        regs = self.registers()
        regs[27] = ctypes.addressof(buf) + 2
        regs[5] = 0xffff00000000_5000 & MASK64   # only low 16 bits participate: 0x5000
        before = list(regs)
        self.assertEqual(self.ldumax16(0x78a5636f, regs, 0), 1)
        self.assertEqual(list(buf), [0x1111, 0x5000, 0x3333, 0x4444])
        self.only_changed(before, regs, {15: 0x2222})

    def test_ldumax_keeps_larger_memory_value_and_is_unsigned(self):
        for stored, operand, final in ((0x9000, 0x0001, 0x9000), (0x0001, 0x9000, 0x9000),
                                       (0x7fff, 0x8000, 0x8000)):
            with self.subTest(stored=hex(stored), operand=hex(operand)):
                memory = U16(stored)
                regs = self.registers()
                regs[27] = ctypes.addressof(memory)
                regs[5] = operand
                self.assertEqual(self.ldumax16(0x78a5636f, regs, 0), 1)
                self.assertEqual(memory.value, final)
                self.assertEqual(regs[15], stored)

    def test_ldumax_other_sizes_and_ops_declined(self):
        memory = U64(0x0102030405060708)
        for word, label in ((0xb8a5636f, 'LDUMAXA w'), (0x38a5636f, 'LDUMAXAB'),
                            (0x78a5236f, 'LDEORAH'), (0x78a5436f, 'LDSMAXAH')):
            with self.subTest(label):
                regs = self.registers()
                regs[27] = ctypes.addressof(memory)
                before = list(regs)
                self.assertEqual(self.ldumax16(word, regs, 0), 0)
                self.assertEqual(memory.value, 0x0102030405060708)
                self.assertEqual(list(regs), before)

    def test_ldumax_odd_address_declined(self):
        buf = ctypes.create_string_buffer(b'01234567', 16)
        regs = self.registers()
        regs[27] = ctypes.addressof(buf) + 1
        raw = buf.raw
        self.assertEqual(self.ldumax16(0x78a5636f, regs, 0), 0)
        self.assertEqual(buf.raw, raw)

    # ---- LDAPUR ----------------------------------------------------------
    def test_measured_ldapurh_negative_offset_zero_extends(self):
        # Steam 0x581f94: 0x59524320 = ldapurh w0, [x25, #-220].
        buf = (U16 * 256)(*range(256))
        target = ctypes.addressof(buf) + 100 * 2
        regs = self.registers()
        regs[25] = target + 220
        buf[100] = 0xbeef
        before = list(regs)
        self.assertEqual(self.ldapur(0x59524320, regs, 0), 1)
        self.only_changed(before, regs, {0: 0xbeef})   # upper bits cleared, not sign-extended

    def test_measured_ldapur_w_positive_unscaled_offset(self):
        # Steam 0x5bb1e8: 0x994f5086 = ldapur w6, [x4, #245]; the target is aligned.
        buf = ctypes.create_string_buffer(512)
        aligned = (ctypes.addressof(buf) + 255) & ~3
        ctypes.c_uint32.from_address(aligned).value = 0x80000001
        regs = self.registers()
        regs[4] = aligned - 245
        before = list(regs)
        self.assertEqual(self.ldapur(0x994f5086, regs, 0), 1)
        self.only_changed(before, regs, {6: 0x80000001})

    def test_ldapur_byte_and_doubleword_forms_and_unaligned_read(self):
        buf = ctypes.create_string_buffer(bytes(range(1, 65)), 64)
        base = ctypes.addressof(buf)
        regs = self.registers(); regs[4] = base - 245 + 3
        self.assertEqual(self.ldapur(0x194f5086, regs, 0), 1)     # ldapurb w6, [x4, #245]
        self.assertEqual(regs[6], 4)
        regs = self.registers(); regs[4] = base + 1 - 245          # unaligned doubleword
        self.assertEqual(self.ldapur(0xd94f5086, regs, 0), 1)     # ldapur x6, [x4, #245]
        self.assertEqual(regs[6], int.from_bytes(bytes(range(2, 10)), 'little'))

    def test_ldapur_xzr_destination_and_sp_base(self):
        memory = U32(0x01020304)
        regs = self.registers()
        before = list(regs)
        # ldapur wzr, [sp, #0] discards; ldapur w1, [sp, #0] loads.
        self.assertEqual(self.ldapur(0x994003ff, regs, ctypes.addressof(memory)), 1)
        self.assertEqual(list(regs), before)
        self.assertEqual(self.ldapur(0x994003e1, regs, ctypes.addressof(memory)), 1)
        self.only_changed(before, regs, {1: 0x01020304})

    def test_ldapur_sign_extending_forms_declined(self):
        memory = U64(0xffffffffffffffff)
        for word, label in ((0x19c00086, 'LDAPURSB w'), (0x19800086, 'LDAPURSB x'),
                            (0x59800086, 'LDAPURSH'), (0x99800086, 'LDAPURSW')):
            with self.subTest(label):
                regs = self.registers(); regs[4] = ctypes.addressof(memory)
                before = list(regs)
                self.assertEqual(self.ldapur(word, regs, 0), 0)
                self.assertEqual(list(regs), before)

    # ---- dispatcher ------------------------------------------------------
    def test_dispatcher_identifies_each_family_once(self):
        # Family ids: 1 LDADDAL, 2 CASPAL128, 3 SWP, 4 CAS, 5 LDUMAX16, 6 LDADD, 7 LDAPUR.
        memory = ctypes.create_string_buffer(64)
        base = ctypes.addressof(memory) & ~15
        if base < ctypes.addressof(memory):
            base += 16
        for word, family in ((0xb8e20062, 1), (0xf8e20000, 1), (0xb8200020, 6),
                             (0xf8200020, 6), (0xb8a18001, 3), (0xf8bf8260, 3),
                             (0x88e0fe61, 4), (0xc8e0fe60, 4), (0xc8a0fc41, 4),
                             (0x78a5636f, 5), (0x4862fc06, 2), (0x59524320, 7),
                             (0x994f5086, 7), (0xd503201f, 0)):
            with self.subTest(word=hex(word)):
                regs = self.registers()
                for i in range(31):
                    regs[i] = base
                self.assertEqual(self.lse_emulate(word, regs, base), family)


if __name__ == '__main__':
    unittest.main()
