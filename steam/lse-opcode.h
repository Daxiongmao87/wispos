/* Exact measured-operation encodings shared by the fallback and optional
 * recorder deduplication. LDADDAL supports word/doubleword; CASPAL supports
 * only doubleword pairs (128-bit memory). Other LSE operations are excluded.
 */
#ifndef WISPOS_LSE_OPCODE_H
#define WISPOS_LSE_OPCODE_H
#include <stdint.h>
#define WISPOS_LDADDAL_MASK UINT32_C(0xffe0fc00)
#define WISPOS_LDADDAL_32 UINT32_C(0xb8e00000)
#define WISPOS_LDADDAL_64 UINT32_C(0xf8e00000)
#define WISPOS_CASPAL128_MASK UINT32_C(0xffe0fc00)
#define WISPOS_CASPAL128 UINT32_C(0x4860fc00)

/* Encodings found by scanning the hash-pinned Steam ELF (340 LSE sites in 11
 * shapes: LDADDAL w/x, CASPAL pair, SWPA w/x, CASAL w/x, CASL x, LDADD w/x,
 * LDUMAXAH). The acquire (bit 23) and release (bit 22) ordering bits of the
 * atomic-memory-operation and CAS families are accepted and every variant is
 * implemented with the stronger acquire-release ordering. Byte and halfword
 * forms other than LDUMAX halfword are not supported.
 */
#define WISPOS_AMO_MASK UINT32_C(0xbf20fc00)   /* 32/64-bit, any A/R */
#define WISPOS_LDADD UINT32_C(0xb8200000)
#define WISPOS_SWP UINT32_C(0xb8208000)
#define WISPOS_LDUMAX16_MASK UINT32_C(0xff20fc00)  /* halfword, any A/R */
#define WISPOS_LDUMAX16 UINT32_C(0x78206000)
#define WISPOS_CAS_MASK UINT32_C(0x3fa07c00)   /* size/L/Rs/o0/Rn/Rt free */
#define WISPOS_CAS UINT32_C(0x08a07c00)

/* LDAPUR/LDAPURH/LDAPURB (FEAT_LRCPC2 unscaled load-acquire, zero-extending).
 * Present inline in ~10 Steam binaries (`ldapurh w0,[x25,#-220]`, `ldapur
 * w6,[x4,#245]`). Sign-extending forms were not found and are not supported.
 */
#define WISPOS_LDAPUR_MASK UINT32_C(0x3fe00c00)  /* size free, opc=01 */
#define WISPOS_LDAPUR UINT32_C(0x19400000)

/* CASP pair starts must be even. Pair register 31 is zero/discard. */
static inline int wispos_caspal128_opcode(uint32_t instruction)
{
    return (instruction & WISPOS_CASPAL128_MASK) == WISPOS_CASPAL128 &&
           !(instruction & UINT32_C(0x00010001));
}
#endif
