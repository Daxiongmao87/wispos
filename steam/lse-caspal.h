/* Original, narrow CASPAL X-register-pair fallback for the instruction
 * measured in Steam. Only little-endian, 16-byte-aligned memory is supported.
 * Arm CASP semantics allow writing the loaded value back on a mismatch.
 * On ARMv8.0 the exclusive-pair load must be validated by a successful
 * exclusive-pair store even when the comparison fails (GCC PR111404).
 * References:
 * https://support.arm.com/documentation/ddi0602/2026-06/Base-Instructions/CASP--CASPA--CASPAL--CASPL--Compare-and-swap-pair-of-words-or-doublewords-in-memory-
 * https://gcc.gnu.org/pipermail/gcc-cvs/2023-November/394322.html
 * The implementation below is original; no upstream code is included.
 */
#ifndef WISPOS_LSE_CASPAL_H
#define WISPOS_LSE_CASPAL_H

#include <stdint.h>
#include "lse-opcode.h"

#if !defined(__BYTE_ORDER__) || __BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__
#error "The narrow CASPAL fallback supports little-endian builds only"
#endif

/* Return 1 after emulation, 0 for other encodings or unsupported alignment.
 * regs contains X0..X30. Rn=31 is SP; the second member of a pair starting
 * at X30 is the zero register (and its result is discarded). All operands
 * are saved before either result register changes. An invalid aligned
 * address retains its ordinary memory-fault behavior.
 */
__attribute__((visibility("protected")))
int wispos_caspal128(uint32_t instruction, uint64_t regs[31], uint64_t sp)
{
    const unsigned expected = (instruction >> 16) & 31;
    const unsigned base = (instruction >> 5) & 31;
    const unsigned desired = instruction & 31;
    uint64_t address, expected_low, expected_high, desired_low, desired_high;
    uint64_t original_low, original_high;

    /* Mask every fixed bit, including size, ordering and reserved fields. */
    if (!wispos_caspal128_opcode(instruction))
        return 0;

    address = base == 31 ? sp : regs[base];
    if ((address & 15) != 0)
        return 0;
    expected_low = regs[expected];
    expected_high = expected == 30 ? 0 : regs[expected + 1];
    desired_low = regs[desired];
    desired_high = desired == 30 ? 0 : regs[desired + 1];

#if defined(WISPOS_LSE_HOST_TEST) && defined(__x86_64__)
    /* Native x86 semantic-test backend only. Compile with -mcx16; this
     * builtin emits CMPXCHG16B rather than libatomic or a lock-based helper.
     * It gives stronger ordering than required by CASPAL.
     */
    typedef unsigned __int128 pair;
    const pair compare = ((pair)expected_high << 64) | expected_low;
    const pair replacement = ((pair)desired_high << 64) | desired_low;
    const pair original = __sync_val_compare_and_swap(
        (pair *)(uintptr_t)address, compare, replacement);
    original_low = (uint64_t)original;
    original_high = (uint64_t)(original >> 64);
#elif defined(__aarch64__) && !defined(WISPOS_LSE_HOST_TEST)
    unsigned status;
    /* Keep the complete exclusive sequence in one asm block. Early-clobber
     * outputs cannot overlap the address, comparison or replacement inputs,
     * and the status register cannot alias the stored pair or its base.
     * The mismatch path stores the original pair to validate the 128-bit
     * read; failed stores retry from the acquire load in both paths.
     */
    __asm__ volatile(
        "1: ldaxp %[lo], %[hi], [%[address]]\n\t"
        "cmp %[lo], %[expected_low]\n\t"
        "b.ne 2f\n\t"
        "cmp %[hi], %[expected_high]\n\t"
        "b.ne 2f\n\t"
        "stlxp %w[status], %[desired_low], %[desired_high], [%[address]]\n\t"
        "cbnz %w[status], 1b\n\t"
        "b 3f\n\t"
        "2: stlxp %w[status], %[lo], %[hi], [%[address]]\n\t"
        "cbnz %w[status], 1b\n\t"
        "3:\n\t"
        : [lo] "=&r" (original_low), [hi] "=&r" (original_high),
          [status] "=&r" (status)
        : [address] "r" (address), [expected_low] "r" (expected_low),
          [expected_high] "r" (expected_high),
          [desired_low] "r" (desired_low), [desired_high] "r" (desired_high)
        : "cc", "memory");
#else
#error "Use the ARMv8.0 backend, or native x86 tests with WISPOS_LSE_HOST_TEST"
#endif

    regs[expected] = original_low;
    if (expected != 30)
        regs[expected + 1] = original_high;
    return 1;
}

#endif
