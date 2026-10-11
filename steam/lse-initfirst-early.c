/* A library whose constructor calls close() while the dynamic loader is still initialising dependencies: what libpython, libGL and libX11 do on the handheld.
 * Dependencies initialise BEFORE any LD_PRELOAD object's constructor, which is exactly the window the SIGILL fallback must cover (see test_lse_initfirst.py). */
#include <unistd.h>

__attribute__((constructor)) static void early(void)
{
    (void)close(-1);
}

void early_marker(void) {}
