/*
 * Narrow fallback for the LSE instructions present in native Steam.
 *
 * The RG34XXSP's Cortex-A53 lacks FEAT_LSE. Emulate only naturally aligned
 * 32/64-bit LDADD (any ordering), SWP and CAS, halfword LDUMAX, zero-extending
 * LDAPUR loads, and CASPAL doubleword pairs, with baseline, lock-free acquire/release atomics. The set
 * is exactly the shapes a static scan of the hash-pinned Steam ELF found (see
 * lse-opcode.h). This does not implement FEAT_LSE generally, change Steam's
 * instruction stream, or claim compatibility with other instructions or libs.
 *
 * Build the preload for armv8-a with -mno-outline-atomics: an outlined helper
 * could itself select LSE or call code unsuitable for a signal handler.
 * Define WISPOS_LSE_HOST_TEST to test the decoder without installing signals.
 * Encoding/semantics reference: Arm A64 LDADD instruction description,
 * https://documentation-service.arm.com/static/67e40f3398aa3c3b6eea6a85
 * This is an original implementation, not copied from Arm or libgcc.
 *
 * Steam's breakpad installs its own SIGILL handler after this constructor runs
 * and, on the first SIGILL, handles it as a crash and then installs the default
 * action, which killed Steam at the next emulatable instruction (exit 132,
 * recorded 2026-10-06). The preload therefore exports sigaction() and signal()
 * interposers: a program's SIGILL registration is recorded as its "chained"
 * handler and never replaces ours. A fault this fallback cannot emulate is
 * logged (word and PC) and passed to the chained handler; if there is none
 * (default, ignored, or a handler that reset itself to default) the default
 * disposition is restored and the fault repeats, so real crashes stay loud.
 * A synchronous SIGILL cannot be handled by a thread that has SIGILL blocked:
 * the kernel forces the default action and kills the process (observed
 * 2026-10-06: libpulse's threaded mainloop blocks every signal, then runs an
 * LSE instruction). pthread_sigmask() and sigprocmask() are therefore
 * interposed to never block SIGILL, and SIGILL is removed from the sa_mask of
 * other signals' handlers; a program cannot observe the difference except
 * through sigprocmask() queries, which never report SIGILL blocked.
 * The constructor still refuses to replace an existing custom/ignored
 * disposition.
 */
#define _GNU_SOURCE
#include <stdint.h>
#include <stddef.h>
#include "lse-opcode.h"
#include "lse-caspal.h"
#include "lse-atomics.h"

#if !defined(__aarch64__) && !defined(WISPOS_LSE_HOST_TEST)
#error "The preload is AArch64-only; host tests must define WISPOS_LSE_HOST_TEST"
#endif

_Static_assert(__atomic_always_lock_free(sizeof(uint32_t), 0),
               "32-bit handler atomics must be lock-free");
_Static_assert(__atomic_always_lock_free(sizeof(uint64_t), 0),
               "64-bit handler atomics must be lock-free");

/* Return 1 after emulation, 0 for other instructions or unaligned memory.
 * regs contains X0..X30; register 31 means SP for the address, and the zero
 * register for source/destination. Snapshot operands before aliased writes.
 * An invalid aligned address retains its ordinary memory-fault behavior.
 */
__attribute__((visibility("protected")))
int wispos_ldadd(uint32_t instruction, uint64_t regs[31], uint64_t sp)
{
    const uint32_t operation = instruction & WISPOS_AMO_MASK;
    const unsigned source = (instruction >> 16) & 31;
    const unsigned base = (instruction >> 5) & 31;
    const unsigned destination = instruction & 31;
    uint64_t address;
    uint64_t addend;
    uint64_t original;
    size_t width;

    if (operation != WISPOS_LDADD)
        return 0;
    width = (instruction & UINT32_C(0x40000000)) ? sizeof(uint64_t) :
                                                    sizeof(uint32_t);

    address = base == 31 ? sp : regs[base];
    addend = source == 31 ? 0 : regs[source];
    if (address & (width - 1))
        return 0;

    if (width == sizeof(uint32_t))
        original = __atomic_fetch_add((uint32_t *)(uintptr_t)address,
                                      (uint32_t)addend, __ATOMIC_ACQ_REL);
    else
        original = __atomic_fetch_add((uint64_t *)(uintptr_t)address,
                                      addend, __ATOMIC_ACQ_REL);

    if (destination != 31)
        regs[destination] = original;
    return 1;
}

/* Families, in marker order. Returns 0 when the instruction is not supported. */
enum {
    WISPOS_FAMILY_NONE, WISPOS_FAMILY_LDADDAL, WISPOS_FAMILY_CASPAL128,
    WISPOS_FAMILY_SWP, WISPOS_FAMILY_CAS, WISPOS_FAMILY_LDUMAX16,
    WISPOS_FAMILY_LDADD, WISPOS_FAMILY_LDAPUR, WISPOS_FAMILIES
};

__attribute__((visibility("protected")))
int wispos_lse_emulate(uint32_t instruction, uint64_t regs[31], uint64_t sp)
{
    if (wispos_caspal128_opcode(instruction))
        return wispos_caspal128(instruction, regs, sp) ? WISPOS_FAMILY_CASPAL128 : 0;
    if ((instruction & WISPOS_AMO_MASK) == WISPOS_LDADD) {
        if (!wispos_ldadd(instruction, regs, sp))
            return 0;
        return (instruction & WISPOS_LDADDAL_MASK) == WISPOS_LDADDAL_32 ||
               (instruction & WISPOS_LDADDAL_MASK) == WISPOS_LDADDAL_64 ?
               WISPOS_FAMILY_LDADDAL : WISPOS_FAMILY_LDADD;
    }
    if ((instruction & WISPOS_AMO_MASK) == WISPOS_SWP)
        return wispos_swp(instruction, regs, sp) ? WISPOS_FAMILY_SWP : 0;
    if ((instruction & WISPOS_CAS_MASK) == WISPOS_CAS)
        return wispos_cas(instruction, regs, sp) ? WISPOS_FAMILY_CAS : 0;
    if ((instruction & WISPOS_LDUMAX16_MASK) == WISPOS_LDUMAX16)
        return wispos_ldumax16(instruction, regs, sp) ? WISPOS_FAMILY_LDUMAX16 : 0;
    if ((instruction & WISPOS_LDAPUR_MASK) == WISPOS_LDAPUR)
        return wispos_ldapur(instruction, regs, sp) ? WISPOS_FAMILY_LDAPUR : 0;
    return 0;
}

#if defined(__aarch64__) && !defined(WISPOS_LSE_HOST_TEST)
#include <dlfcn.h>
#include <errno.h>
#include <pthread.h>
#include <fcntl.h>
#include <signal.h>
#include <stdlib.h>
#include <unistd.h>
#include <ucontext.h>

/* The arm64 kernel reports an undefined instruction as ILL_ILLOPC (recorded on
 * the handheld as code=1). QEMU user mode reports ILL_ILLOPN; only the QEMU
 * test build (lse-qemu-test.c) defines WISPOS_QEMU_USER_TEST to accept it.
 */
#ifdef WISPOS_QEMU_USER_TEST
#define WISPOS_ILL_CODE_OK(code) ((code) == ILL_ILLOPC || (code) == ILL_ILLOPN)
#else
#define WISPOS_ILL_CODE_OK(code) ((code) == ILL_ILLOPC)
#endif

static struct sigaction previous_sigill;       /* disposition at load: SIG_DFL */
static int (*real_sigaction)(int, const struct sigaction *, struct sigaction *);
#ifndef WISPOS_STATIC_TEST
static sighandler_t (*real_signal)(int, sighandler_t);
static int (*real_pthread_sigmask)(int, const sigset_t *, sigset_t *);
static int (*real_sigprocmask)(int, const sigset_t *, sigset_t *);
#endif
/* The program's own SIGILL disposition as it believes it is installed (the
 * kernel keeps ours). Two slots: a writer fills the idle one and flips the index
 * so the handler, which may interrupt a writer, never reads a torn record.
 */
static struct sigaction chain_slot[2];
static unsigned chain_index;
static int chain_lock;
static int handler_installed;
static unsigned unsupported_logged;
static unsigned first_emulated[WISPOS_FAMILIES];
_Static_assert(__atomic_always_lock_free(sizeof(first_emulated[0]), 0),
               "handler marker atomic must be lock-free");

/* Fixed messages: the handler may only call write(). */
#define MESSAGE(text) { text, sizeof(text) - 1 }
static const struct { const char *text; size_t length; } first_message[WISPOS_FAMILIES] = {
    [WISPOS_FAMILY_LDADDAL] = MESSAGE("WispOS LDADDAL fallback: first instruction emulated\n"),
    [WISPOS_FAMILY_CASPAL128] = MESSAGE("WispOS CASPAL128 fallback: first instruction emulated\n"),
    [WISPOS_FAMILY_SWP] = MESSAGE("WispOS SWP fallback: first instruction emulated\n"),
    [WISPOS_FAMILY_CAS] = MESSAGE("WispOS CAS fallback: first instruction emulated\n"),
    [WISPOS_FAMILY_LDUMAX16] = MESSAGE("WispOS LDUMAX16 fallback: first instruction emulated\n"),
    [WISPOS_FAMILY_LDADD] = MESSAGE("WispOS LDADD fallback: first instruction emulated\n"),
    [WISPOS_FAMILY_LDAPUR] = MESSAGE("WispOS LDAPUR fallback: first instruction emulated\n"),
};

/* write() is the only logging operation performed inside the handler. */
static void marker(const char *message, size_t length)
{
    const ssize_t written = write(STDERR_FILENO, message, length);
    (void)written;
}

/* SIGILL is blocked during this handler. A synchronous, unrecognized hardware
 * fault refaults naturally at the unchanged PC after restoring the default
 * disposition. Asynchronous deliveries must be queued again; their original
 * sender metadata is not preserved. This is not general handler chaining.
 */
static void restore_sigill(int signal_number, const siginfo_t *info)
{
    (void)real_sigaction(SIGILL, &previous_sigill, NULL);
    if (info == NULL || info->si_code <= 0)
        (void)raise(signal_number);
}

/* "WispOS LSE fallback: unsupported instruction 0xWWWWWWWW at pc 0xPPPPPPPPPPPPPPPP".
 * Formatted by hand and written once: no stdio in a signal handler. Limited to
 * a few lines so a program that survives repeated faults cannot flood stderr.
 */
static void log_unsupported(uint32_t instruction, uint64_t pc)
{
    static const char prefix[] = "WispOS LSE fallback: unsupported instruction 0x";
    static const char middle[] = " at pc 0x";
    char line[sizeof(prefix) + 8 + sizeof(middle) + 16 + 1];
    char *out = line;
    int shift;

    if (__atomic_fetch_add(&unsupported_logged, 1, __ATOMIC_RELAXED) >= 8)
        return;
    for (size_t i = 0; i < sizeof(prefix) - 1; ++i) *out++ = prefix[i];
    for (shift = 28; shift >= 0; shift -= 4) *out++ = "0123456789abcdef"[(instruction >> shift) & 15];
    for (size_t i = 0; i < sizeof(middle) - 1; ++i) *out++ = middle[i];
    for (shift = 60; shift >= 0; shift -= 4) *out++ = "0123456789abcdef"[(pc >> shift) & 15];
    *out++ = '\n';
    marker(line, (size_t)(out - line));
}

#ifdef WISPOS_STATIC_TEST
#define PTHREAD_SIGMASK pthread_sigmask
#else
#define PTHREAD_SIGMASK real_pthread_sigmask
#endif

static void set_chain(const struct sigaction *action)
{
    while (__atomic_exchange_n(&chain_lock, 1, __ATOMIC_ACQUIRE))
        ;
    chain_slot[1 - chain_index] = *action;
    __atomic_store_n(&chain_index, 1 - chain_index, __ATOMIC_RELEASE);
    __atomic_store_n(&chain_lock, 0, __ATOMIC_RELEASE);
}

/* Call the program's own SIGILL handler for a fault we cannot emulate. SIGILL
 * is unblocked while it runs so that any emulatable instruction it executes is
 * still handled. Returns 0 when the program has no handler (default or ignored:
 * a synchronous SIGILL cannot be ignored, so ignored is treated as default).
 */
static int chain_to_program(int signal_number, siginfo_t *info, void *context)
{
    struct sigaction program = chain_slot[__atomic_load_n(&chain_index, __ATOMIC_ACQUIRE)];
    sigset_t unblock, saved;

    if (program.sa_handler == SIG_DFL || program.sa_handler == SIG_IGN)
        return 0;
    if (program.sa_flags & SA_RESETHAND) {
        struct sigaction reset = {0};
        set_chain(&reset);
    }
    sigemptyset(&unblock);
    sigaddset(&unblock, SIGILL);
    (void)PTHREAD_SIGMASK(SIG_UNBLOCK, &unblock, &saved);
    if (program.sa_flags & SA_SIGINFO)
        program.sa_sigaction(signal_number, info, context);
    else
        program.sa_handler(signal_number);
    (void)PTHREAD_SIGMASK(SIG_SETMASK, &saved, NULL);
    return 1;
}


#ifdef WISPOS_LSE_STATS
/* Test-only build (-DWISPOS_LSE_STATS): count emulated faults per process and per faulting address, to learn whether the SIGILL path is a real cost (docs/WISPOS-NOTES.md,
 * "SIGILL fallback overhead"). Counts are dumped to $WISPOS_LSE_STATS_DIR/lse-<pid>.txt every 16384 events (a fixed table, no allocation; open/write/pwrite only). */
#define STAT_SITES 512
static struct { uint64_t pc; uint64_t count; } stat_site[STAT_SITES];
static uint64_t stat_events, stat_ticks, stat_overflow;
static int stat_fd = -2;
static uint64_t stat_now(void) { uint64_t v; __asm__ volatile("mrs %0, cntvct_el0" : "=r"(v)); return v; }
static void stat_hex(char **out, uint64_t v, int digits) { for (int sh = (digits - 1) * 4; sh >= 0; sh -= 4) *(*out)++ = "0123456789abcdef"[(v >> sh) & 15]; }
static void stat_dec(char **out, uint64_t v) { char t[24]; int n = 0; do { t[n++] = (char)('0' + v % 10); v /= 10; } while (v); while (n) *(*out)++ = t[--n]; }
static void stat_dump(void)
{
    static char buf[STAT_SITES * 40 + 256];
    char *out = buf;
    if (stat_fd == -2) {
        const char *dir = getenv("WISPOS_LSE_STATS_DIR");
        char path[160]; char *q = path;
        if (dir == NULL) { stat_fd = -1; return; }
        for (; *dir && q < path + 120; ++dir) *q++ = *dir;
        for (const char *a = "/lse-"; *a; ++a) *q++ = *a;
        stat_dec(&q, (uint64_t)getpid());
        for (const char *a = ".txt"; *a; ++a) *q++ = *a;
        *q = 0;
        stat_fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    }
    if (stat_fd < 0) return;
    for (const char *a = "events "; *a; ++a) *out++ = *a;
    stat_dec(&out, stat_events);
    for (const char *a = " handler_ticks "; *a; ++a) *out++ = *a;
    stat_dec(&out, stat_ticks);
    for (const char *a = " table_overflow "; *a; ++a) *out++ = *a;
    stat_dec(&out, stat_overflow);
    *out++ = '\n';
    for (unsigned i = 0; i < STAT_SITES; ++i) {
        if (!stat_site[i].count) continue;
        stat_hex(&out, stat_site[i].pc, 16); *out++ = ' '; stat_dec(&out, stat_site[i].count); *out++ = '\n';
    }
    { const ssize_t w = pwrite(stat_fd, buf, (size_t)(out - buf), 0); (void)w; }
}
static void stat_event(uint64_t pc, uint64_t t0)
{
    unsigned i = (unsigned)((pc >> 2) * 2654435761u) % STAT_SITES, probes = 0;
    while (stat_site[i].count && stat_site[i].pc != pc && probes < STAT_SITES) { i = (i + 1) % STAT_SITES; ++probes; }
    if (probes >= STAT_SITES) ++stat_overflow; else { stat_site[i].pc = pc; ++stat_site[i].count; }
    stat_ticks += stat_now() - t0;
    if ((++stat_events & 16383) == 1) stat_dump();
}
__attribute__((destructor)) static void stat_at_exit(void) { stat_dump(); }
#endif

static void sigill_handler(int signal_number, siginfo_t *info, void *context)
{
#ifdef WISPOS_LSE_STATS
    const uint64_t stat_t0 = stat_now();
#endif
    const int saved_errno = errno;
    ucontext_t *cpu = context;

    if (info != NULL && WISPOS_ILL_CODE_OK(info->si_code) && cpu != NULL &&
        (cpu->uc_mcontext.pc & 3) == 0) {
        const uint32_t instruction =
            *(const uint32_t *)(uintptr_t)cpu->uc_mcontext.pc;
        uint64_t regs[31];
        unsigned index;
        /* glibc defines context registers as unsigned long long, while
         * uint64_t may be unsigned long: copy rather than aliasing types.
         */
        for (index = 0; index < 31; ++index)
            regs[index] = cpu->uc_mcontext.regs[index];
        const int family = wispos_lse_emulate(instruction, regs,
                                              cpu->uc_mcontext.sp);
        if (family) {
            for (index = 0; index < 31; ++index)
                cpu->uc_mcontext.regs[index] = regs[index];
#ifdef WISPOS_LSE_STATS
            stat_event(cpu->uc_mcontext.pc, stat_t0);
#endif
            cpu->uc_mcontext.pc += 4;
            if (__atomic_exchange_n(&first_emulated[family], 1,
                                    __ATOMIC_RELAXED) == 0)
                marker(first_message[family].text, first_message[family].length);
            errno = saved_errno;
            return;
        }
    }

    if (info != NULL && cpu != NULL && info->si_code > 0 && (cpu->uc_mcontext.pc & 3) == 0)
        log_unsupported(*(const uint32_t *)(uintptr_t)cpu->uc_mcontext.pc,
                        cpu->uc_mcontext.pc);
    if (!chain_to_program(signal_number, info, context))
        restore_sigill(signal_number, info);
    errno = saved_errno;
}

#ifndef WISPOS_STATIC_TEST
/* Interposers. They must keep default visibility so the dynamic loader binds
 * the program's calls to them. After the constructor has installed the handler,
 * SIGILL registrations are recorded instead of applied; everything else passes
 * through unchanged.
 */
int sigaction(int signal_number, const struct sigaction *action,
              struct sigaction *old)
{
    if (real_sigaction == NULL) {
        errno = ENOSYS;
        return -1;
    }
    if (signal_number != SIGILL || !__atomic_load_n(&handler_installed, __ATOMIC_ACQUIRE)) {
        struct sigaction adjusted;
        if (action != NULL && __atomic_load_n(&handler_installed, __ATOMIC_ACQUIRE) &&
            sigismember(&action->sa_mask, SIGILL) == 1) {
            adjusted = *action;
            sigdelset(&adjusted.sa_mask, SIGILL);
            action = &adjusted;
        }
        return real_sigaction(signal_number, action, old);
    }
    if (old != NULL)
        *old = chain_slot[__atomic_load_n(&chain_index, __ATOMIC_ACQUIRE)];
    if (action != NULL)
        set_chain(action);
    return 0;
}

/* Never let a thread block SIGILL while the fallback is installed. SIG_UNBLOCK
 * needs no adjustment; otherwise a copy of the set without SIGILL is applied.
 */
static const sigset_t *without_sigill(int how, const sigset_t *set, sigset_t *copy)
{
    if (set == NULL || how == SIG_UNBLOCK ||
        !__atomic_load_n(&handler_installed, __ATOMIC_ACQUIRE) ||
        sigismember(set, SIGILL) != 1)
        return set;
    *copy = *set;
    sigdelset(copy, SIGILL);
    return copy;
}

int pthread_sigmask(int how, const sigset_t *set, sigset_t *old)
{
    sigset_t copy;
    if (real_pthread_sigmask == NULL)
        return ENOSYS;
    return real_pthread_sigmask(how, without_sigill(how, set, &copy), old);
}

int sigprocmask(int how, const sigset_t *set, sigset_t *old)
{
    sigset_t copy;
    if (real_sigprocmask == NULL) {
        errno = ENOSYS;
        return -1;
    }
    return real_sigprocmask(how, without_sigill(how, set, &copy), old);
}

sighandler_t signal(int signal_number, sighandler_t handler)
{
    struct sigaction action = {0}, old;

    if (signal_number != SIGILL || !__atomic_load_n(&handler_installed, __ATOMIC_ACQUIRE))
        return real_signal != NULL ? real_signal(signal_number, handler) : SIG_ERR;
    old = chain_slot[__atomic_load_n(&chain_index, __ATOMIC_ACQUIRE)];
    action.sa_handler = handler;
    action.sa_flags = SA_RESTART;
    sigemptyset(&action.sa_mask);
    set_chain(&action);
    return old.sa_handler;
}
#endif

__attribute__((constructor)) static void install_sigill_handler(void)
{
    const int saved_errno = errno;
    struct sigaction action = {0};
    static const char ready[] = "WispOS LDADDAL fallback: handler ready\n";
    static const char caspal_ready[] = "WispOS CASPAL128 fallback: handler ready\n";
    static const char more_ready[] =
        "WispOS SWP/CAS/LDADD/LDUMAX16/LDAPUR fallbacks: handler ready\n";
    static const char failure[] = "WispOS LDADDAL fallback: installation failed\n";
    static const char inactive[] =
        "WispOS LDADDAL fallback: inactive; existing SIGILL disposition\n";

#ifdef WISPOS_STATIC_TEST
    real_sigaction = sigaction;
#else
    real_sigaction = (int (*)(int, const struct sigaction *, struct sigaction *))
        dlsym(RTLD_NEXT, "sigaction");
    real_signal = (sighandler_t (*)(int, sighandler_t))dlsym(RTLD_NEXT, "signal");
    real_pthread_sigmask = (int (*)(int, const sigset_t *, sigset_t *))
        dlsym(RTLD_NEXT, "pthread_sigmask");
    real_sigprocmask = (int (*)(int, const sigset_t *, sigset_t *))
        dlsym(RTLD_NEXT, "sigprocmask");
    if (real_sigaction == NULL || real_signal == NULL ||
        real_pthread_sigmask == NULL || real_sigprocmask == NULL) {
        marker(failure, sizeof(failure) - 1);
        errno = saved_errno;
        return;
    }
#endif
    if (real_sigaction(SIGILL, NULL, &previous_sigill) != 0) {
        marker(failure, sizeof(failure) - 1);
        errno = saved_errno;
        return;
    }
    if (previous_sigill.sa_handler != SIG_DFL) {
        marker(inactive, sizeof(inactive) - 1);
        errno = saved_errno;
        return;
    }
    action.sa_sigaction = sigill_handler;
    action.sa_mask = previous_sigill.sa_mask;
    action.sa_flags = SA_SIGINFO |
        (previous_sigill.sa_flags & (SA_ONSTACK | SA_RESTART));
    /* SA_NODEFER is intentionally absent: this fallback must not recurse. */
    if (real_sigaction(SIGILL, &action, NULL) != 0)
        marker(failure, sizeof(failure) - 1);
    else {
        __atomic_store_n(&handler_installed, 1, __ATOMIC_RELEASE);
        marker(ready, sizeof(ready) - 1);
        marker(caspal_ready, sizeof(caspal_ready) - 1);
        marker(more_ready, sizeof(more_ready) - 1);
    }
    errno = saved_errno;
}
#endif
