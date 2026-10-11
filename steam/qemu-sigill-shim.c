/*
 * TEST-ONLY shim for running Steam under qemu-user (lse-qemu-test / docs/LSE-COVERAGE-TEST.md).
 * Steam's breakpad takes over SIGILL and then calls clone() without CLONE_VM,
 * which QEMU user mode rejects, ending the run in an unrelated SEGV. This shim
 * lets the first sigaction(SIGILL, new, ...) through (the fallback's own
 * constructor) and makes later ones report success without replacing it.
 * It is never installed on the handheld.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <signal.h>
#include <string.h>

int sigaction(int signum, const struct sigaction *act, struct sigaction *old)
{
    static int (*real)(int, const struct sigaction *, struct sigaction *);
    static int fallback_installed;
    if (!real)
        real = (int (*)(int, const struct sigaction *, struct sigaction *))dlsym(RTLD_NEXT, "sigaction");
    if (signum == SIGILL && act != NULL) {
        if (!fallback_installed) {         /* first registration: the fallback's */
            fallback_installed = 1;
            return real(signum, act, old);
        }
        return real(signum, NULL, old);    /* report the current handler, change nothing */
    }
    return real(signum, act, old);
}
