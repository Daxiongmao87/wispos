/* Test helper for os/setup/handover.h: reads "t boot setup" per line (t as a decimal, the pixels as hex XRGB) and prints the hand-over pixel for each, so os/test_setup.py can
 * compare the C arithmetic with the contract's (os/splash/preview_handover.py) bit for bit on thousands of random inputs. */
#include <stdio.h>
#include "handover.h"

int main(void)
{
    double t; unsigned b, s;
    while (scanf("%lf %x %x", &t, &b, &s) == 3) {
        double g, ab, as;
        if (t < 0) { t = 0; }
        if (t > 1) { t = 1; }
        ho_levels(t, &g, &ab, &as);
        printf("%06x\n", ho_pixel(b, s, g, ab, as) & 0xffffffu);
    }
    return 0;
}
