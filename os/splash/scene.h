/* The WispOS boot screen's drawing: a software canvas (XRGB8888) and the setup screen's components drawn on it (docs/WISPOS-UI-LANGUAGE.md "Boot-time screens"). Shared by every
 * program that shows this screen, so they cannot drift apart:
 *   os/splash/wisp-splash.c      the framebuffer splash of the initramfs (static, before Wayland exists)
 *   os/splash/wisp-bootscreen.c  the same screen as a Wayland overlay from the compositor's first moment until the first window (the "Wayland phase")
 *   os/setup/wisp-setup.c        the first-run hand-over (--intro) draws the boot elements as its first frame
 * Needs "wisp-ui.h" (tokens) and "wisp-ui-assets.h" (generated glyph atlases and badge: os/ui/gen_assets.py) on the include path; libc and libm only. */
#ifndef WISPOS_SPLASH_SCENE_H
#define WISPOS_SPLASH_SCENE_H

#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "wisp-ui.h"
#include "wisp-ui-assets.h"

#define MAXLINE 160

struct canvas { uint32_t *px; int w, h, stride; };   /* XRGB8888, stride in pixels */
enum anchor { LEFT = 0, CENTER = 1, RIGHT = 2 };

static inline uint32_t blend(uint32_t fg, uint32_t bg, int a)          /* a: 0..255 of fg */
{
    uint32_t out = 0;
    for (int sh = 0; sh <= 16; sh += 8) {
        int f = (fg >> sh) & 0xff, b = (bg >> sh) & 0xff;
        out |= (uint32_t)((f * a + b * (255 - a) + 127) / 255) << sh;
    }
    return out;
}

static inline void fill_rect(struct canvas *c, int x, int y, int w, int h, uint32_t col)
{
    if (x < 0) { w += x; x = 0; }
    if (y < 0) { h += y; y = 0; }
    if (x + w > c->w) w = c->w - x;
    if (y + h > c->h) h = c->h - y;
    if (w <= 0 || h <= 0) return;
    for (int j = 0; j < h; j++) {
        uint32_t *row = c->px + (size_t)(y + j) * c->stride + x;
        for (int i = 0; i < w; i++) row[i] = col;
    }
}

static inline int isqrt(int n) { int r = 0; while ((r + 1) * (r + 1) <= n) r++; return r; }

/* The setup screen's rrect(): corner inset per scanline, no anti-aliasing. It draws each scanline with SDL_RenderDrawLine(x + inset, ..., x + w - 1 - inset), which
 * is inclusive of both end points, so a scanline whose inset passes the middle still paints two pixels: that is what makes a radius-3 dot round. Same here. */
static inline void rrect(struct canvas *c, int x, int y, int w, int h, int rad, uint32_t col)
{
    if (rad * 2 > h) rad = h / 2;
    for (int j = 0; j < h; j++) {
        int inset = 0;
        if (j < rad) { int dy = rad - j; inset = rad - isqrt(rad * rad - dy * dy); }
        else if (j >= h - rad) { int dy = j - (h - rad) + 1; inset = rad - isqrt(rad * rad - dy * dy); }
        int x0 = x + inset, x1 = x + w - 1 - inset;
        if (x1 < x0) { int t = x0; x0 = x1; x1 = t; }
        fill_rect(c, x0, y + j, x1 - x0 + 1, 1, col);
    }
}
static inline void disc(struct canvas *c, int cx, int cy, int r, uint32_t col) { rrect(c, cx - r, cy - r, 2 * r, 2 * r, r, col); }

/* ------------------------------------------------------------------------- text from the glyph atlases */
static inline int glyph_index(int ch) { return (ch < 32 || ch > 126) ? '?' - 32 : ch - 32; }

static inline int text_width(const struct wisp_font *f, const char *s)
{
    int w = 0;
    for (; *s; s++) w += f->glyphs[glyph_index((unsigned char)*s)].adv;
    return w;
}

/* Like the setup screen's text(): anchor 0 left, 1 centre, 2 right; max_w > 0 cuts with "..." so it fits; (x, y) is the top-left of the text line. */
static inline void text(struct canvas *c, const struct wisp_font *f, const char *s, int x, int y, uint32_t col, int anchor, int max_w)
{
    char buf[MAXLINE + 4];
    snprintf(buf, sizeof buf, "%s", s);
    if (max_w > 0 && text_width(f, buf) > max_w) {
        size_t n = strlen(buf);
        while (n > 0) { n--; snprintf(buf + n, sizeof buf - n, "..."); if (text_width(f, buf) <= max_w) break; buf[n] = 0; }
    }
    int tw = text_width(f, buf);
    if (anchor == CENTER) x -= tw / 2; else if (anchor == RIGHT) x -= tw;
    for (const char *p = buf; *p; p++) {
        const struct wisp_glyph *g = &f->glyphs[glyph_index((unsigned char)*p)];
        for (int j = 0; j < g->h; j++) {
            int py = y + g->top + j;
            if (py < 0 || py >= c->h) continue;
            for (int i = 0; i < g->w; i++) {
                int px = x + g->left + i, a = f->alpha[g->off + j * g->w + i];
                if (a == 0 || px < 0 || px >= c->w) continue;
                uint32_t *d = &c->px[(size_t)py * c->stride + px];
                *d = blend(col, *d, a);
            }
        }
        x += g->adv;
    }
}

/* ------------------------------------------------------------------------- the setup screen's components */
static inline void badge(struct canvas *c, int cx, int y)       /* the boot-size badge, centred on cx */
{
    int x0 = cx - WISP_BADGE_BOOT_W / 2;
    for (int j = 0; j < WISP_BADGE_BOOT_H; j++)
        for (int i = 0; i < WISP_BADGE_BOOT_W; i++) {
            const unsigned char *p = &wisp_badge_boot_bgra[(j * WISP_BADGE_BOOT_W + i) * 4];
            int px = x0 + i, py = y + j;
            if (p[3] == 0 || px < 0 || px >= c->w || py < 0 || py >= c->h) continue;
            uint32_t *d = &c->px[(size_t)py * c->stride + px];
            *d = blend(((uint32_t)p[2] << 16) | ((uint32_t)p[1] << 8) | p[0], *d, p[3]);
        }
}

static inline void spinner(struct canvas *c, int cx, int cy, int r, int head)
{
    fill_rect(c, cx - r - WISP_UI_SPIN_DOT_R - 1, cy - r - WISP_UI_SPIN_DOT_R - 1, 2 * (r + WISP_UI_SPIN_DOT_R + 1), 2 * (r + WISP_UI_SPIN_DOT_R + 1), WISP_UI_BOOT_BG);
    for (int i = 0; i < WISP_UI_SPIN_DOTS; i++) {
        float a = i * (float)M_PI / 6, age = (float)((head - i + 2 * WISP_UI_SPIN_DOTS) % WISP_UI_SPIN_DOTS) / WISP_UI_SPIN_DOTS;   /* 0 = head, 1 = tail */
        int alpha = (int)(255 - age * WISP_UI_SPIN_FADE);
        disc(c, cx + (int)lroundf(sinf(a) * r), cy - (int)lroundf(cosf(a) * r), WISP_UI_SPIN_DOT_R, blend(WISP_UI_ACCENT, WISP_UI_BOOT_BG, alpha));
    }
}

static inline void hint(struct canvas *c, int x, int y, const char *key, const char *label, bool centred)   /* the setup's key pill and label; centred on x when asked */
{
    int kw = text_width(&font_tiny, key);
    int pill = kw + 12 < 20 ? 20 : kw + 12;
    if (centred) x -= (pill + 8 + text_width(&font_tiny, label)) / 2;
    rrect(c, x, y, pill, 20, 10, WISP_UI_PANEL);
    text(c, &font_tiny, key, x + pill / 2, y + 2, WISP_UI_GLYPH, CENTER, 0);
    text(c, &font_tiny, label, x + pill + 8, y + 2, WISP_UI_DIM, LEFT, 0);
}

struct screen {
    char title[96], status[MAXLINE], detail[MAXLINE], hint_key[24], hint_label[64], reason[MAXLINE];
    bool failed, dev;
};

/* The boot screen: one centred column on black (docs/WISPOS-UI-LANGUAGE.md "Boot-time screens"): the badge, "WispOS", then (in place of the setup screen's progress
 * row) the spinner and the current stage below the logo; a failure replaces the spinner and the stage by the setup's red "!" and its text. */
static inline void draw_screen(struct canvas *c, const struct screen *s)
{
    int mid = c->w / 2;
    fill_rect(c, 0, 0, c->w, c->h, WISP_UI_BOOT_BG);
    badge(c, mid, WISP_UI_BOOT_BADGE_Y);
    text(c, &font_title, "WispOS", mid, WISP_UI_BOOT_NAME_Y, WISP_UI_TEXT, CENTER, 0);
    if (s->dev) text(c, &font_tiny, "DEV BUILD", mid, WISP_UI_BOOT_DEVMARK_Y, WISP_UI_BAD, CENTER, 0);
    if (s->failed) {
        disc(c, mid, WISP_UI_BOOT_SPIN_CY - 2, WISP_UI_BOOT_FAIL_R, WISP_UI_BAD);
        text(c, &font_title, "!", mid, WISP_UI_BOOT_SPIN_CY - 20, WISP_UI_TEXT, CENTER, 0);
        text(c, &font_body, "Boot problem", mid, WISP_UI_BOOT_STATUS_Y + 8, WISP_UI_TEXT, CENTER, c->w - 40);
        text(c, &font_small, s->reason, mid, WISP_UI_BOOT_DETAIL_Y + 8, WISP_UI_DIM, CENTER, c->w - 40);
        text(c, &font_small, "The boot log opens in a moment.", mid, WISP_UI_BOOT_DETAIL_Y + 34, WISP_UI_DIM, CENTER, c->w - 40);
    } else {
        text(c, &font_body, s->status, mid, WISP_UI_BOOT_STATUS_Y, WISP_UI_TEXT, CENTER, c->w - 40);
        if (s->detail[0]) text(c, &font_small, s->detail, mid, WISP_UI_BOOT_DETAIL_Y, WISP_UI_DIM, CENTER, c->w - 40);
    }
    hint(c, mid, WISP_UI_HINT_Y, s->hint_key, s->hint_label, true);
}

/* The program's own text view (used when there is no console to reveal: the Wayland boot screen, tests and --log-file): the last `n` of the lines, in the Tiny type. */
static inline void draw_logview_lines(struct canvas *c, char lines[][MAXLINE], int n)
{
    fill_rect(c, 0, 0, c->w, c->h, WISP_UI_BOOT_BG);
    int lh = font_tiny.height + 2, cap = c->h / lh - 1; if (cap > 60) cap = 60;
    for (int i = n > cap ? n - cap : 0, row = 0; i < n; i++, row++) text(c, &font_tiny, lines[i], 4, row * lh, WISP_UI_TEXT, LEFT, c->w - 8);
}

/* The same, from the last lines of a file. */
static inline void draw_logview(struct canvas *c, const char *path)
{
    int lh = (&font_tiny)->height + 2, cap = c->h / lh - 1; if (cap > 60) cap = 60;
    static char lines[60][MAXLINE]; int n = 0;
    FILE *f = fopen(path, "r");
    if (f) {
        char buf[MAXLINE];
        while (fgets(buf, sizeof buf, f)) {
            buf[strcspn(buf, "\r\n")] = 0;
            if (n == cap) { memmove(lines, lines + 1, sizeof lines[0] * (cap - 1)); n--; }
            snprintf(lines[n++], MAXLINE, "%s", buf);
        }
        fclose(f);
    }
    draw_logview_lines(c, lines, n);
}

#endif
