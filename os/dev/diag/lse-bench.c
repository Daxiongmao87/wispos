/* Dev: cost of one emulated LSE atomic (SIGILL -> handler -> emulate -> sigreturn) vs the same atomic as an LL/SC loop. Build -march=armv8.1-a (the LSE loop must be real LSE). */
#define _GNU_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <time.h>
static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t); return t.tv_sec + t.tv_nsec * 1e-9; }
int main(int argc, char **argv)
{
    int mode = argc > 1 ? argv[1][0] : 'l';
    long n = argc > 2 ? 0 : 0; (void)n;
    long count = mode == 'l' ? 200000 : 5000000;                  /* LSE is slow (trap): fewer iterations */
    volatile uint32_t cell = 0; uint32_t one = 1, old;
    double t0 = now();
    for (long i = 0; i < count; ++i) {
        if (mode == 'l') __asm__ volatile("ldaddal %w1, %w0, [%2]" : "=&r"(old) : "r"(one), "r"(&cell) : "memory");
        else { uint32_t tmp, st; __asm__ volatile("1: ldaxr %w0, [%3]\n add %w1, %w0, %w4\n stlxr %w2, %w1, [%3]\n cbnz %w2, 1b\n dmb ish"
                                                    : "=&r"(old), "=&r"(tmp), "=&r"(st) : "r"(&cell), "r"(one) : "memory"); }
    }
    double dt = now() - t0;
    printf("%s: %ld ops in %.3f s = %.0f ns/op (cell=%u)\n", mode == 'l' ? "LSE ldaddal via SIGILL fallback" : "LL/SC loop (ldaxr/stlxr)", count, dt, dt / count * 1e9, (unsigned)cell);
    return 0;
}
