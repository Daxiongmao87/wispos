/* The first-run hand-over from the boot screen to the setup screen: the arithmetic (no SDL, so a native test checks it against the contract). The contract is
 * os/splash/preview_handover.py and docs/WISPOS-UI-LANGUAGE.md "Hand-over": 3 seconds, fade OUT then fade in, never a cross-fade. With t = elapsed / 3 s and s(x) = smoothstep(x):
 *   ground      = lerp(black, navy, s(t))            the black -> navy gradient runs the whole 3 seconds
 *   boot layer  = 1 - s(t / 0.5)                     the boot screen's elements are gone at t = 0.5
 *   setup layer = s((t - 0.5) / 0.5)                 the setup screen's elements (what differs from the navy ground) come in over the last half
 * The 8-bit arithmetic is the preview's, so that the two agree to the last bit: each term is truncated toward zero like Python's int(v * a), and the three are added with saturation.
 * One deliberate difference from the first version of the preview: the setup layer is the SIGNED difference from the navy ground, so an element darker than the ground (the setup
 * screen's black key pills and text field) fades in too, and the last frame IS the setup screen. The first version clipped the difference at 0, which would have left those elements
 * missing at t = 1 and had them pop in the instant the hand-over ended. */
#ifndef WISP_SETUP_HANDOVER_H
#define WISP_SETUP_HANDOVER_H

#include <stdint.h>

#define HO_SECONDS 3.0
#define HO_NAVY 0x0e141bu

static inline double ho_smooth(double x) { if (x < 0) { x = 0; } if (x > 1) { x = 1; } return x * x * (3 - 2 * x); }

static inline void ho_levels(double t, double *ground, double *boot, double *setup)
{
    *ground = ho_smooth(t);
    *boot = 1 - ho_smooth(t / 0.5);
    *setup = ho_smooth((t - 0.5) / 0.5);
}

/* One XRGB8888 pixel of the hand-over. boot: the boot screen's pixel (its ground is black, so its pixels ARE its elements); setup: the setup screen's pixel (on its navy ground). */
static inline uint32_t ho_pixel(uint32_t boot, uint32_t setup, double ground, double ab, double as)
{
    uint32_t out = 0;
    for (int sh = 0; sh <= 16; sh += 8) {
        int navy = (int)((HO_NAVY >> sh) & 255), b = (int)((boot >> sh) & 255), s = (int)((setup >> sh) & 255);
        int d = s - navy;                                                                     /* signed: see above */
        int v = (int)(navy * ground) + (int)(b * ab) + (int)(d * as);
        if (v > 255) v = 255;
        if (v < 0) v = 0;
        out |= (uint32_t)v << sh;
    }
    return out;
}

/* A whole frame at t (0..1; clamped). */
static inline void ho_frame(uint32_t *out, const uint32_t *boot, const uint32_t *setup, int n, double t)
{
    double g, ab, as;
    if (t < 0) { t = 0; }
    if (t > 1) { t = 1; }
    ho_levels(t, &g, &ab, &as);
    for (int i = 0; i < n; i++) out[i] = ho_pixel(boot[i], setup[i], g, ab, as);
}

#endif
