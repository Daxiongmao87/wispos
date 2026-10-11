/* Test double for Steam's gameoverlayrenderer.so (see test_lse_initfirst.py): it interposes libc close() the way the real overlay does, and its hook runs an LSE
 * compare-and-swap (casal w0, w1, [x2], encoded by hand so this file needs no -march flag) before forwarding to the next close().
 * On a CPU without LSE that instruction raises SIGILL unless the WispOS fallback has already installed its handler. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <unistd.h>

static volatile uint32_t slot;

int close(int fd)
{
    register uint32_t expected __asm__("w0") = 0;
    register uint32_t desired __asm__("w1") = 1;
    register volatile uint32_t *address __asm__("x2") = &slot;
    __asm__ volatile(".inst 0x88e0fc41" /* casal w0, w1, [x2] */
                     : "+r"(expected) : "r"(desired), "r"(address) : "memory");
    static int (*next)(int);
    if (next == NULL)
        next = (int (*)(int))dlsym(RTLD_NEXT, "close");
    return next(fd);
}
