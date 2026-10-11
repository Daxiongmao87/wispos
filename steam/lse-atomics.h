/* Original SWP, CAS and LDUMAX-halfword fallbacks for instructions found in
 * Steam's own code (see lse-opcode.h). Each is emulated with baseline
 * acquire-release atomics; with -march=armv8-a -mno-outline-atomics these
 * compile to LDAXR/STLXR loops, never to LSE instructions or helper calls.
 * Semantics: Arm A64 SWP, CAS and LDUMAX descriptions,
 * https://developer.arm.com/documentation/ddi0602/latest/Base-Instructions/
 * The implementation is original; no upstream code is included.
 *
 * Every function returns 1 after emulation and 0 for other encodings or
 * unaligned memory. regs holds X0..X30; Rn=31 is SP; register 31 as a source
 * is zero and as a destination discards. Operands are read before any result
 * register is written, so aliases such as `swpa w1, w1, [x0]` are exact. An
 * invalid aligned address retains its ordinary memory-fault behavior.
 */
#ifndef WISPOS_LSE_ATOMICS_H
#define WISPOS_LSE_ATOMICS_H

#include <stdint.h>
#include <stddef.h>
#include "lse-opcode.h"

_Static_assert(__atomic_always_lock_free(sizeof(uint16_t), 0),
               "16-bit handler atomics must be lock-free");

static inline uint64_t wispos_source(const uint64_t regs[31], unsigned n)
{
    return n == 31 ? 0 : regs[n];
}

/* SWP{A,L,AL} Ws|Xs, Wt|Xt, [Xn]: Rt receives the old value, memory gets Rs. */
__attribute__((visibility("protected")))
int wispos_swp(uint32_t instruction, uint64_t regs[31], uint64_t sp)
{
    const unsigned source = (instruction >> 16) & 31;
    const unsigned base = (instruction >> 5) & 31;
    const unsigned destination = instruction & 31;
    const size_t width = (instruction & UINT32_C(0x40000000)) ? 8 : 4;
    uint64_t address, value, original;

    if ((instruction & WISPOS_AMO_MASK) != WISPOS_SWP)
        return 0;
    address = base == 31 ? sp : regs[base];
    if (address & (width - 1))
        return 0;
    value = wispos_source(regs, source);
    if (width == 4)
        original = __atomic_exchange_n((uint32_t *)(uintptr_t)address,
                                       (uint32_t)value, __ATOMIC_ACQ_REL);
    else
        original = __atomic_exchange_n((uint64_t *)(uintptr_t)address,
                                       value, __ATOMIC_ACQ_REL);
    if (destination != 31)
        regs[destination] = original;
    return 1;
}

/* CAS{A,L,AL} Ws|Xs, Wt|Xt, [Xn]: compare memory with Rs, store Rt on match;
 * Rs always receives the old memory value.
 */
__attribute__((visibility("protected")))
int wispos_cas(uint32_t instruction, uint64_t regs[31], uint64_t sp)
{
    const unsigned compare = (instruction >> 16) & 31;
    const unsigned base = (instruction >> 5) & 31;
    const unsigned replacement = instruction & 31;
    const unsigned size = instruction >> 30;
    uint64_t address, expected, desired, original;

    if ((instruction & WISPOS_CAS_MASK) != WISPOS_CAS || size < 2)
        return 0;
    address = base == 31 ? sp : regs[base];
    if (address & ((size == 3 ? 8 : 4) - 1))
        return 0;
    expected = wispos_source(regs, compare);
    desired = wispos_source(regs, replacement);
    if (size == 2) {
        uint32_t seen = (uint32_t)expected;
        (void)__atomic_compare_exchange_n((uint32_t *)(uintptr_t)address, &seen,
                                          (uint32_t)desired, 0,
                                          __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE);
        original = seen;
    } else {
        uint64_t seen = expected;
        (void)__atomic_compare_exchange_n((uint64_t *)(uintptr_t)address, &seen,
                                          desired, 0,
                                          __ATOMIC_ACQ_REL, __ATOMIC_ACQUIRE);
        original = seen;
    }
    if (compare != 31)
        regs[compare] = original;
    return 1;
}

/* LDUMAX{A,L,AL}H Ws, Wt, [Xn]: memory = max(memory, Ws[15:0]) unsigned;
 * Wt receives the old halfword, zero-extended.
 */
__attribute__((visibility("protected")))
int wispos_ldumax16(uint32_t instruction, uint64_t regs[31], uint64_t sp)
{
    const unsigned source = (instruction >> 16) & 31;
    const unsigned base = (instruction >> 5) & 31;
    const unsigned destination = instruction & 31;
    uint16_t *memory;
    uint16_t operand, seen;

    if ((instruction & WISPOS_LDUMAX16_MASK) != WISPOS_LDUMAX16)
        return 0;
    memory = (uint16_t *)(uintptr_t)(base == 31 ? sp : regs[base]);
    if ((uintptr_t)memory & 1)
        return 0;
    operand = (uint16_t)wispos_source(regs, source);
    seen = __atomic_load_n(memory, __ATOMIC_RELAXED);
    while (!__atomic_compare_exchange_n(memory, &seen,
                                        seen > operand ? seen : operand, 0,
                                        __ATOMIC_ACQ_REL, __ATOMIC_RELAXED))
        ;
    if (destination != 31)
        regs[destination] = seen;
    return 1;
}

/* LDAPUR{B,H,}/LDAPUR Wt|Xt, [Xn|SP, #simm9]: zero-extending load-acquire.
 * Loaded with ACQUIRE ordering (a load-acquire instruction), which is at least
 * as strong as the RCpc ordering the original provides. An unaligned address
 * is read bytewise, as the original permits; no atomicity is claimed then.
 */
__attribute__((visibility("protected")))
int wispos_ldapur(uint32_t instruction, uint64_t regs[31], uint64_t sp)
{
    const unsigned base = (instruction >> 5) & 31;
    const unsigned destination = instruction & 31;
    const unsigned size = instruction >> 30;
    const int64_t offset = (int64_t)((instruction >> 12) & 0x1ff) - (int64_t)(((instruction >> 12) & 0x100) << 1);
    const uint64_t address = (base == 31 ? sp : regs[base]) + (uint64_t)offset;
    const size_t width = (size_t)1 << size;
    uint64_t value = 0;

    if ((instruction & WISPOS_LDAPUR_MASK) != WISPOS_LDAPUR)
        return 0;
    if (address & (width - 1)) {
        __builtin_memcpy(&value, (const void *)(uintptr_t)address, width);
        __atomic_thread_fence(__ATOMIC_ACQUIRE);
    } else if (size == 0) {
        value = __atomic_load_n((const uint8_t *)(uintptr_t)address, __ATOMIC_ACQUIRE);
    } else if (size == 1) {
        value = __atomic_load_n((const uint16_t *)(uintptr_t)address, __ATOMIC_ACQUIRE);
    } else if (size == 2) {
        value = __atomic_load_n((const uint32_t *)(uintptr_t)address, __ATOMIC_ACQUIRE);
    } else {
        value = __atomic_load_n((const uint64_t *)(uintptr_t)address, __ATOMIC_ACQUIRE);
    }
    if (destination != 31)
        regs[destination] = value;
    return 1;
}

#endif
