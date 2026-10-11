/*
 * ARMv8.0 execution test for the LSE fallback, run under QEMU user mode with
 * `-cpu cortex-a53` (no FEAT_LSE). It includes the real preload source, so the
 * real constructor installs the real SIGILL handler, and then executes the
 * instruction words found in Steam via `.inst` (no LSE assembler support or
 * outlined helpers are needed to build it).
 *
 * Unlike the native x86 semantic tests this exercises actual ARM instructions,
 * the exclusive-monitor loops, and signal-frame register/PC modification.
 * It does not prove behavior on the physical Cortex-A53 or inside Steam.
 *
 * Build (see test_lse_qemu.py): aarch64-linux-gnu-gcc -static -pthread
 *   -DWISPOS_QEMU_USER_TEST -DWISPOS_STATIC_TEST (no sigaction interposers or
 *   dlsym in a static binary; chaining is tested by lse-qemu-chain-test.c)
 *   -march=armv8-a -mtune=cortex-a53 -mno-outline-atomics
 */
#define _GNU_SOURCE
#include "lse-ldadd.c"

#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>

static int failures;
#define CHECK(condition, ...) do { if (!(condition)) { ++failures; \
    printf("FAIL %s:%d: ", __func__, __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

/* Word builders for fixed registers: Rn = x0 (address), Rs = x1, Rt = x2. */
#define AMO(size, a, r, o3, opc) \
    (((uint32_t)(size) << 30) | 0x38200000u | ((uint32_t)(a) << 23) | \
     ((uint32_t)(r) << 22) | (1u << 16) | ((uint32_t)(o3) << 15) | \
     ((uint32_t)(opc) << 12) | (0u << 5) | 2u)
#define LDADD(size, a, r) AMO(size, a, r, 0, 0)
#define LDUMAX16(a, r) AMO(1, a, r, 0, 6)
#define SWP(size, a, r) AMO(size, a, r, 1, 0)
#define CAS(size, l, o0) (((uint32_t)(size) << 30) | 0x08a07c00u | \
    ((uint32_t)(l) << 22) | (1u << 16) | ((uint32_t)(o0) << 15) | (0u << 5) | 2u)

/* Execute one word with x0=address, x1=Rs, x2=Rt (all in/out). */
#define RUN(word, address, rs, rt) do { \
    register uint64_t r0 __asm__("x0") = (uint64_t)(uintptr_t)(address); \
    register uint64_t r1 __asm__("x1") = (rs); \
    register uint64_t r2 __asm__("x2") = (rt); \
    __asm__ volatile(".inst %c[w]" : "+r"(r1), "+r"(r2) \
                     : [w] "i"(word), "r"(r0) : "memory"); \
    (rs) = r1; (rt) = r2; } while (0)

#define LDADD_CASE(A, R) do { \
    uint32_t m32 = 100; uint64_t m64 = 0x100000000ull; \
    uint64_t rs = 5, rt = 0xdeadbeefdeadbeefull; \
    RUN(LDADD(2, A, R), &m32, rs, rt); \
    CHECK(m32 == 105 && rt == 100 && rs == 5, "ldadd w a=%d r=%d m=%u", A, R, m32); \
    rs = 7; rt = ~0ull; \
    RUN(LDADD(3, A, R), &m64, rs, rt); \
    CHECK(m64 == 0x100000007ull && rt == 0x100000000ull, "ldadd x a=%d r=%d", A, R); \
} while (0)

static void test_ldadd_variants(void)
{
    LDADD_CASE(0, 0);
    LDADD_CASE(1, 0);
    LDADD_CASE(0, 1);
    LDADD_CASE(1, 1);
}

static void test_swp(void)
{
    uint32_t m32 = 0xcafef00d; uint64_t m64 = 0x0123456789abcdefull;
    uint64_t rs = 0xffffffff00000007ull, rt = ~0ull;
    RUN(SWP(2, 1, 0), &m32, rs, rt);               /* swpa w1, w2 */
    CHECK(m32 == 7 && rt == 0xcafef00d, "swpa w m=%x rt=%llx", m32, (unsigned long long)rt);
    rs = 0x1122334455667788ull; rt = 0;
    RUN(SWP(3, 0, 0), &m64, rs, rt);               /* swp x1, x2 */
    CHECK(m64 == 0x1122334455667788ull && rt == 0x0123456789abcdefull, "swp x");
    rs = 1; RUN(SWP(3, 0, 1), &m64, rs, rt);       /* swpl */
    CHECK(m64 == 1 && rt == 0x1122334455667788ull, "swpl x");
    rs = 2; RUN(SWP(2, 1, 1), &m32, rs, rt);       /* swpal w */
    CHECK(m32 == 2 && rt == 7, "swpal w");
}

/* Steam's exact aliasing word: swpa w1, w1, [x0]. */
static void test_steam_swpa_alias(void)
{
    uint32_t memory = 0xcafef00d;
    register uint64_t r0 __asm__("x0") = (uint64_t)(uintptr_t)&memory;
    register uint64_t r1 __asm__("x1") = 0xffffffff00000007ull;
    __asm__ volatile(".inst 0xb8a18001" : "+r"(r1) : "r"(r0) : "memory");
    CHECK(memory == 7 && r1 == 0xcafef00d, "steam swpa alias memory=%x x1=%llx",
          memory, (unsigned long long)r1);
}

static void test_cas(void)
{
    /* casal w1, w2, [x0]: Rs=x1 is compare and old-value result, Rt=x2 new. */
    uint32_t m32 = 100; uint64_t rs = 0xdead000000000064ull, rt = 0x22222222;
    RUN(CAS(2, 1, 1), &m32, rs, rt);
    CHECK(m32 == 0x22222222 && rs == 100, "casal w match");
    m32 = 101; rs = 100; rt = 0x33333333;
    RUN(CAS(2, 1, 1), &m32, rs, rt);
    CHECK(m32 == 101 && rs == 101, "casal w mismatch");
    uint64_t m64 = 0x8000000000000001ull; rs = m64; rt = 0x55;
    RUN(CAS(3, 1, 1), &m64, rs, rt);               /* casal x */
    CHECK(m64 == 0x55 && rs == 0x8000000000000001ull, "casal x match");
    m64 = 9; rs = 9; rt = 10;
    RUN(CAS(3, 0, 1), &m64, rs, rt);               /* casl x */
    CHECK(m64 == 10 && rs == 9, "casl x");
    m32 = 3; rs = 3; rt = 4;
    RUN(CAS(2, 0, 0), &m32, rs, rt);               /* cas w */
    CHECK(m32 == 4 && rs == 3, "cas w");
    m32 = 3; rs = 3; rt = 4;
    RUN(CAS(2, 1, 0), &m32, rs, rt);               /* casa w */
    CHECK(m32 == 4 && rs == 3, "casa w");
}

static void test_ldumax16(void)
{
    uint16_t buf[4] = {0x1111, 0x2222, 0x3333, 0x4444};
    uint64_t rs = 0xffff000000005000ull, rt = ~0ull;
    RUN(LDUMAX16(1, 0), &buf[1], rs, rt);          /* ldumaxah w1, w2, [x0] */
    CHECK(buf[0] == 0x1111 && buf[1] == 0x5000 && buf[2] == 0x3333 && rt == 0x2222,
          "ldumaxah %x %x %llx", buf[1], buf[2], (unsigned long long)rt);
    rs = 0x0001; RUN(LDUMAX16(0, 0), &buf[1], rs, rt);   /* ldumaxh keeps larger */
    CHECK(buf[1] == 0x5000 && rt == 0x5000, "ldumaxh keeps max");
    uint16_t signed_edge = 0x7fff; rs = 0x8000;
    RUN(LDUMAX16(1, 1), &signed_edge, rs, rt);     /* unsigned compare */
    CHECK(signed_edge == 0x8000 && rt == 0x7fff, "ldumax unsigned");
}

/* Steam's own LDAPUR words: ldapurh w0,[x25,#-220] and ldapur w6,[x4,#245]. */
static void test_ldapur(void)
{
    static uint16_t half[256];
    static uint32_t word[2] __attribute__((aligned(8)));
    half[100] = 0xbeef; word[0] = 0x80000001;
    register uint64_t r25 __asm__("x25") = (uint64_t)(uintptr_t)&half[100] + 220;
    register uint64_t r0 __asm__("x0") = ~0ull;
    __asm__ volatile(".inst 0x59524320" : "+r"(r0) : "r"(r25) : "memory");
    CHECK(r0 == 0xbeef, "ldapurh zero-extend: %llx", (unsigned long long)r0);
    register uint64_t r4 __asm__("x4") = (uint64_t)(uintptr_t)&word[0] - 245;
    register uint64_t r6 __asm__("x6") = ~0ull;
    __asm__ volatile(".inst 0x994f5086" : "+r"(r6) : "r"(r4) : "memory");
    CHECK(r6 == 0x80000001, "ldapur w: %llx", (unsigned long long)r6);
}

static void test_caspal(void)
{
    /* caspal x2, x3, x4, x5, [x0]: the word measured at Steam 0xe1290 uses
     * other registers; this fixed form exercises the same family. */
    static uint64_t pair[2] __attribute__((aligned(16))) = {0, 0};
    register uint64_t r0 __asm__("x0") = (uint64_t)(uintptr_t)pair;
    register uint64_t r2 __asm__("x2") = 0, r3 __asm__("x3") = 0;
    register uint64_t r4 __asm__("x4") = 0xaaaaf95a8080ull, r5 __asm__("x5") = 0x10001;
    __asm__ volatile(".inst 0x4862fc04" : "+r"(r2), "+r"(r3) : "r"(r0), "r"(r4), "r"(r5) : "memory");
    CHECK(pair[0] == 0xaaaaf95a8080ull && pair[1] == 0x10001 && r2 == 0 && r3 == 0, "caspal match");
}

/* The instruction must really fault on this CPU model: run it in a child with
 * the default SIGILL disposition. A pass here cannot be native LSE execution. */
static int child_terminal_signal(void (*body)(void))
{
    const pid_t pid = fork();
    int status = 0;
    if (pid == 0) {
        signal(SIGILL, SIG_DFL);
        body();
        _exit(0);
    }
    waitpid(pid, &status, 0);
    return WIFSIGNALED(status) ? WTERMSIG(status) : 0;
}
static uint64_t scratch[4] __attribute__((aligned(16)));
static void body_native_lse(void) { uint64_t rs = 1, rt = 0; RUN(LDADD(3, 1, 1), scratch, rs, rt); }
static void body_native_ldapur(void) { register uint64_t r25 __asm__("x25") = (uint64_t)(uintptr_t)scratch + 220; register uint64_t r0 __asm__("x0"); __asm__ volatile(".inst 0x59524320" : "=r"(r0) : "r"(r25) : "memory"); }
static void body_unsupported_ldeoral(void) { uint64_t rs = 1, rt = 0; RUN(0xf8e12002u, scratch, rs, rt); }
static void body_unaligned_swp(void) { uint64_t rs = 1, rt = 0; RUN(SWP(3, 1, 1), (char *)scratch + 1, rs, rt); }

static void test_controls(void)
{
    /* With the handler removed, LSE must raise SIGILL: proves no native LSE. */
    CHECK(child_terminal_signal(body_native_lse) == SIGILL, "cortex-a53 model unexpectedly executed LSE");
    CHECK(child_terminal_signal(body_native_ldapur) == SIGILL, "cortex-a53 model unexpectedly executed LDAPUR");
    /* With the handler installed, unsupported and unaligned forms still die with SIGILL
     * (restore-and-refault) instead of being hidden. */
    const pid_t pid = fork();
    if (pid == 0) { body_unsupported_ldeoral(); _exit(0); }
    int status; waitpid(pid, &status, 0);
    CHECK(WIFSIGNALED(status) && WTERMSIG(status) == SIGILL, "unsupported ldeoral status=%d", status);
    const pid_t pid2 = fork();
    if (pid2 == 0) { body_unaligned_swp(); _exit(0); }
    waitpid(pid2, &status, 0);
    CHECK(WIFSIGNALED(status) && WTERMSIG(status) == SIGILL, "unaligned swp status=%d", status);
}

#define THREADS 4
#define ITERATIONS 2000
static uint64_t counter, swap_cell, cas_counter;
static void *worker(void *unused)
{
    (void)unused;
    for (unsigned i = 0; i < ITERATIONS; ++i) {
        uint64_t rs = 1, rt = 0;
        RUN(LDADD(3, 1, 1), &counter, rs, rt);
        for (;;) {                                  /* cas-loop increment */
            uint64_t seen = __atomic_load_n(&cas_counter, __ATOMIC_RELAXED);
            uint64_t expected = seen, desired = seen + 1;
            RUN(CAS(3, 1, 1), &cas_counter, expected, desired);
            if (expected == seen) break;
        }
    }
    return NULL;
}

static void test_concurrency(void)
{
    pthread_t threads[THREADS];
    for (unsigned i = 0; i < THREADS; ++i) pthread_create(&threads[i], NULL, worker, NULL);
    for (unsigned i = 0; i < THREADS; ++i) pthread_join(threads[i], NULL);
    CHECK(counter == (uint64_t)THREADS * ITERATIONS, "ldaddal lost updates: %llu", (unsigned long long)counter);
    CHECK(cas_counter == (uint64_t)THREADS * ITERATIONS, "casal lost updates: %llu", (unsigned long long)cas_counter);
    (void)swap_cell;
}

int main(void)
{
    test_ldadd_variants();
    test_swp();
    test_steam_swpa_alias();
    test_cas();
    test_ldumax16();
    test_ldapur();
    test_caspal();
    test_controls();
    test_concurrency();
    printf(failures ? "FAILED: %d check(s)\n" : "ALL PASSED\n", failures);
    return failures != 0;
}
