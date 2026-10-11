/*
 * Dynamic QEMU test of the preload's SIGILL handler ownership and chaining.
 * Run with LD_PRELOAD=<the real shared preload built with
 * -DWISPOS_QEMU_USER_TEST> under qemu-aarch64-static -cpu cortex-a53.
 * It plays the part Steam's breakpad played on the handheld: register its own
 * SIGILL handler after the preload's constructor, and behave like a crash
 * handler that resets the disposition to default.
 */
#define _GNU_SOURCE
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <pthread.h>
#include <stdlib.h>
#include <sys/wait.h>
#include <ucontext.h>
#include <unistd.h>

static int failures;
#define CHECK(condition, ...) do { if (!(condition)) { ++failures; \
    printf("FAIL line %d: ", __LINE__); printf(__VA_ARGS__); printf("\n"); } } while (0)

static volatile int program_handler_calls;
static volatile int inside_emulations;
static volatile siginfo_t last_info;

/* ldaddal x1, x2, [x0]  (Steam-shaped, emulated by the preload) */
static inline uint64_t ldaddal(uint64_t *address, uint64_t value)
{
    register uint64_t r0 __asm__("x0") = (uint64_t)(uintptr_t)address;
    register uint64_t r1 __asm__("x1") = value;
    register uint64_t r2 __asm__("x2");
    __asm__ volatile(".inst 0xf8e10002" : "=r"(r2) : "r"(r0), "r"(r1) : "memory");
    return r2;
}
/* ldeoral x1, x2, [x0]: LDEOR is NOT emulated, so it stays a genuine SIGILL. */
static inline void unsupported(uint64_t *address)
{
    register uint64_t r0 __asm__("x0") = (uint64_t)(uintptr_t)address;
    register uint64_t r1 __asm__("x1") = 1;
    register uint64_t r2 __asm__("x2");
    __asm__ volatile(".inst 0xf8e12002" : "=r"(r2) : "r"(r0), "r"(r1) : "memory");
}

static uint64_t cell;

/* Behaves like a program crash handler that tolerates the fault: records it,
 * executes an emulatable instruction itself, then skips the instruction. */
static void program_skip_handler(int sig, siginfo_t *info, void *context)
{
    ucontext_t *cpu = context;
    (void)sig;
    ++program_handler_calls;
    last_info = *info;
    uint64_t before = cell;
    (void)ldaddal(&cell, 1);                  /* SIGILL must be unblocked here */
    if (cell == before + 1) ++inside_emulations;
    cpu->uc_mcontext.pc += 4;
}

/* Behaves like breakpad after a handled "crash": install the default action. */
static void program_default_handler(int sig, siginfo_t *info, void *context)
{
    (void)info; (void)context;
    signal(sig, SIG_DFL);
}

static void test_registration_does_not_displace_the_fallback(void)
{
    struct sigaction mine = {0}, old, current;
    mine.sa_sigaction = program_skip_handler;
    mine.sa_flags = SA_SIGINFO;
    sigemptyset(&mine.sa_mask);
    CHECK(sigaction(SIGILL, &mine, &old) == 0, "sigaction failed");
    CHECK(old.sa_handler == SIG_DFL, "program should see the default as previous");
    CHECK(sigaction(SIGILL, NULL, &current) == 0 && current.sa_sigaction == program_skip_handler,
          "program must read back its own handler");

    cell = 10;
    CHECK(ldaddal(&cell, 5) == 10 && cell == 15, "emulation lost after the program registered a handler");
    CHECK(program_handler_calls == 0, "program handler must not see emulatable instructions");
    for (int i = 0; i < 100; ++i) (void)ldaddal(&cell, 1);
    CHECK(cell == 115 && program_handler_calls == 0, "repeated emulation after registration");
}

static void test_unsupported_fault_reaches_the_program_handler(void)
{
    cell = 100;
    unsupported(&cell);                        /* handler skips it */
    CHECK(program_handler_calls == 1, "program handler calls=%d", program_handler_calls);
    CHECK(last_info.si_signo == SIGILL && last_info.si_code > 0, "siginfo not forwarded");
    CHECK(inside_emulations == 1 && cell == 101, "emulatable instruction inside the program handler (cell=%llu)",
          (unsigned long long)cell);
    cell = 0; (void)ldaddal(&cell, 1);
    CHECK(cell == 1, "emulation still works after a chained fault");
}

static void test_signal_returns_previous_and_resets(void)
{
    sighandler_t old = signal(SIGILL, SIG_DFL);
    CHECK((uintptr_t)old == (uintptr_t)program_skip_handler, "signal() old handler wrong");
    cell = 7;
    CHECK(ldaddal(&cell, 1) == 7 && cell == 8, "emulation with program disposition default");
}

static int run_child(void (*body)(void))
{
    const pid_t pid = fork();
    int status = 0;
    if (pid == 0) { body(); _exit(0); }
    waitpid(pid, &status, 0);
    if (WIFSIGNALED(status)) return WTERMSIG(status);
    return WEXITSTATUS(status) ? -WEXITSTATUS(status) : 0;   /* 0 = survived and passed */
}
static void child_unsupported_with_default(void) { unsupported(&cell); }
static void child_breakpad_like(void)
{
    struct sigaction crash = {0};
    crash.sa_sigaction = program_default_handler; crash.sa_flags = SA_SIGINFO;
    sigaction(SIGILL, &crash, NULL);
    /* The first emulatable instruction after registration must NOT be hijacked. */
    cell = 1; (void)ldaddal(&cell, 1);
    if (cell != 2) _exit(3);
    unsupported(&cell);                         /* real crash: handler resets to default */
    _exit(4);                                   /* only reached if the fault was swallowed */
}
static void child_emulation_after_default_handler(void)
{
    struct sigaction crash = {0};
    crash.sa_sigaction = program_default_handler; crash.sa_flags = SA_SIGINFO;
    sigaction(SIGILL, &crash, NULL);
    for (int i = 0; i < 1000; ++i) (void)ldaddal(&cell, 1);
}

static void test_real_crashes_stay_loud_and_breakpad_scenario(void)
{
    CHECK(run_child(child_unsupported_with_default) == SIGILL, "unsupported fault with no program handler must kill with SIGILL");
    CHECK(run_child(child_breakpad_like) == SIGILL, "crash-handler-resets-to-default scenario must end in SIGILL");
    CHECK(run_child(child_emulation_after_default_handler) == 0, "emulation must survive a program handler that resets to default");
}

/* Blocking SIGILL must be impossible: a synchronous SIGILL in a thread with it
 * blocked kills the process even though a handler is installed. libpulse's
 * threaded mainloop blocks every signal in its thread (handheld boot 9b8decea). */
static void *pulse_like_thread(void *unused)
{
    sigset_t all; (void)unused;
    sigfillset(&all);
    pthread_sigmask(SIG_BLOCK, &all, NULL);
    cell = 0;
    (void)ldaddal(&cell, 1);
    return cell == 1 ? NULL : (void *)1;
}
static void child_pulse_like_thread(void)
{
    pthread_t thread; void *result = (void *)2;
    pthread_create(&thread, NULL, pulse_like_thread, NULL);
    pthread_join(thread, &result);
    if (result != NULL) _exit(3);
}
static void child_sigprocmask_block(void)
{
    sigset_t block, current;
    sigemptyset(&block); sigaddset(&block, SIGILL);
    sigprocmask(SIG_BLOCK, &block, NULL);
    sigfillset(&block);
    sigprocmask(SIG_SETMASK, &block, NULL);
    sigprocmask(SIG_BLOCK, NULL, &current);
    if (sigismember(&current, SIGILL) == 1) _exit(4);      /* mask must not report SIGILL blocked */
    cell = 0; (void)ldaddal(&cell, 1);
    if (cell != 1) _exit(5);
}
static void usr1_handler(int sig) { (void)sig; cell = 0; (void)ldaddal(&cell, 1); }
static void child_handler_mask_blocks_sigill(void)
{
    struct sigaction action = {0};
    action.sa_handler = usr1_handler;
    sigfillset(&action.sa_mask);                            /* "block everything while handling" */
    sigaction(SIGUSR1, &action, NULL);
    raise(SIGUSR1);
    if (cell != 1) _exit(6);
}
static void test_signal_masks_cannot_hide_sigill_from_the_fallback(void)
{
    CHECK(run_child(child_pulse_like_thread) == 0, "thread that blocked all signals must survive an LSE instruction");
    CHECK(run_child(child_sigprocmask_block) == 0, "sigprocmask must not block SIGILL");
    CHECK(run_child(child_handler_mask_blocks_sigill) == 0, "another handler's sa_mask must not block SIGILL");
}

int main(void)
{
    test_registration_does_not_displace_the_fallback();
    test_unsupported_fault_reaches_the_program_handler();
    test_signal_returns_previous_and_resets();
    test_real_crashes_stay_loud_and_breakpad_scenario();
    test_signal_masks_cannot_hide_sigill_from_the_fallback();
    printf(failures ? "FAILED: %d check(s)\n" : "ALL PASSED\n", failures);
    return failures != 0;
}
