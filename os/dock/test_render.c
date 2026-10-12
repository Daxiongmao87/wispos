/* Tests of the shell's renderer (render.c): shapes, blending, text placement against the numbers Pillow gives for the same fonts (the locked pictures were drawn with it), images and the
 * PNG reader's limits. Run by os/test_dock_render.py, which hands over the font directory and the PNG files it wrote.
 *   test_render FONT_DIR tiny.png wide.png garbage.png */
#include "render.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int checks, failed;
static void check_true(const char *what, int ok) { checks++; if (!ok) { failed++; printf("FAIL %s\n", what); } }
static void check_int(const char *what, long got, long want) { checks++; if (got != want) { failed++; printf("FAIL %s: got %ld, want %ld\n", what, got, want); } }

static RdCanvas canvas(int w, int h, uint32_t fill)
{
    RdCanvas c = { w, h, w, calloc((size_t)w * (size_t)h, 4) };
    for (int i = 0; i < w * h; i++) c.px[i] = fill;
    return c;
}

static uint32_t at(const RdCanvas *c, int x, int y) { return c->px[(size_t)y * (size_t)c->stride + (size_t)x]; }
static int A(uint32_t p) { return (int)(p >> 24); }
static int R(uint32_t p) { return (int)((p >> 16) & 255); }
static int G(uint32_t p) { return (int)((p >> 8) & 255); }
static int B(uint32_t p) { return (int)(p & 255); }

static double coverage_sum(const RdCanvas *c)
{
    double s = 0;
    for (int i = 0; i < c->w * c->h; i++) s += A(c->px[i]) / 255.0;
    return s;
}

static void t_blending(void)
{
    RdCanvas c = canvas(4, 4, 0);
    rd_clear(&c, RD_RGB(0x0e141b));
    check_int("opaque clear", at(&c, 1, 1), 0xff0e141b);
    rd_clear(&c, RD_RGBA(0xffffff, 128));
    check_int("a translucent clear is premultiplied", at(&c, 0, 0), (128u << 24) | (128 << 16) | (128 << 8) | 128);
    rd_clear(&c, RD_RGB(0x000000));
    rd_blend_px(&c, 1, 1, RD_RGBA(0xff0000, 128), 255);
    uint32_t p = at(&c, 1, 1);
    check_true("half red over black: alpha 255, red 128", A(p) == 255 && R(p) == 128 && G(p) == 0 && B(p) == 0);
    rd_clear(&c, 0);
    rd_blend_px(&c, 2, 2, RD_RGBA(0x00ff00, 200), 128);
    p = at(&c, 2, 2);
    check_true("a translucent colour at half coverage over nothing: alpha 100, green 100", A(p) == 100 && G(p) == 100 && R(p) == 0);
    rd_blend_px(&c, -1, 0, RD_RGB(0xffffff), 255); rd_blend_px(&c, 0, -1, RD_RGB(0xffffff), 255); rd_blend_px(&c, 4, 0, RD_RGB(0xffffff), 255); rd_blend_px(&c, 0, 4, RD_RGB(0xffffff), 255);
    rd_blend_px(&c, 0, 0, RD_RGB(0xffffff), 0); rd_blend_px(&c, 0, 0, RD_RGBA(0xffffff, 0), 255);
    check_int("outside the canvas, or no coverage, or no alpha: nothing", at(&c, 0, 0), 0);
    rd_blend_px(&c, 0, 0, RD_RGB(0x123456), 999);
    check_int("a coverage past 255 is 255", at(&c, 0, 0), 0xff123456);
    rd_clear(&c, RD_RGB(0xffffff));
    for (int i = 0; i < 1000; i++) rd_blend_px(&c, 1, 1, RD_RGBA(0x000000, 3), 255);
    check_true("blending never leaves 0..255 or goes transparent", A(at(&c, 1, 1)) == 255 && R(at(&c, 1, 1)) < 60);
    free(c.px);
}

static void t_shapes(void)
{
    RdCanvas c = canvas(80, 60, 0);
    rd_rrect(&c, 10, 10, 50, 40, 0, RD_RGB(0xffffff));
    check_int("a whole-pixel box fills exactly its pixels (first)", at(&c, 10, 10), 0xffffffff);
    check_int("last", at(&c, 49, 39), 0xffffffff);
    check_int("one past, x", at(&c, 50, 20), 0);
    check_int("one past, y", at(&c, 20, 40), 0);
    check_int("one before", at(&c, 9, 20), 0);
    check_true("area of the box", fabs(coverage_sum(&c) - 40 * 30) < 1e-6);

    free(c.px); c = canvas(80, 60, 0);
    rd_rrect(&c, 10, 10, 50, 40, 6, RD_RGB(0xffffff));
    check_int("a rounded box: the middle is solid", at(&c, 30, 25), 0xffffffff);
    check_true("the corner pixel is nearly empty", A(at(&c, 10, 10)) < 40);
    check_true("an edge pixel away from the corner is solid", A(at(&c, 30, 10)) == 255);
    check_true("symmetric (left/right)", A(at(&c, 11, 14)) == A(at(&c, 48, 14)));
    check_true("symmetric (top/bottom)", A(at(&c, 14, 11)) == A(at(&c, 14, 38)));
    double area = coverage_sum(&c), want = 40 * 30 - (4 - M_PI) * 36;
    check_true("its area is the box less four corner cut-outs, within 1%", fabs(area - want) / want < 0.01);

    free(c.px); c = canvas(80, 60, 0);
    rd_disc(&c, 40, 30, 14, RD_RGB(0xffffff));
    area = coverage_sum(&c);
    check_true("a disc's area is pi r^2 within 1%", fabs(area - M_PI * 196) / (M_PI * 196) < 0.01);
    check_int("its centre", at(&c, 40, 30), 0xffffffff);
    check_int("outside", at(&c, 40 + 16, 30), 0);

    free(c.px); c = canvas(80, 60, 0);
    rd_pill(&c, 10, 20, 70, 42, RD_RGB(0xffffff));
    check_true("a pill is as round as it is high: the end pixel of the middle row is solid", A(at(&c, 11, 31)) > 200);
    check_true("and its corner pixel is empty", A(at(&c, 10, 20)) < 5);
    area = coverage_sum(&c); want = 22.0 * 38 + M_PI * 121;
    check_true("pill area", fabs(area - want) / want < 0.01);

    free(c.px); c = canvas(80, 60, 0);
    rd_rrect_ring(&c, 10, 10, 50, 40, 2, 1, RD_RGB(0xffffff));
    check_int("a ring: the edge pixel is on", at(&c, 30, 10), 0xffffffff);
    check_int("the inside is empty", at(&c, 30, 20), 0);
    check_int("and the other edge", at(&c, 30, 39), 0xffffffff);
    area = coverage_sum(&c); want = 2.0 * (38 + 28) + 4 * 0.5;
    check_true("its area is about the perimeter", fabs(area - 2 * (40 + 30) + 4) < 4);
    free(c.px); c = canvas(80, 60, 0);
    rd_rrect_ring(&c, 10, 10, 14, 14, 2, 3, RD_RGB(0xffffff));
    check_true("a ring wider than the shape does not crash", A(at(&c, 12, 12)) > 0);

    free(c.px); c = canvas(40, 40, 0);
    rd_rrect(&c, -10, -10, 10, 10, 3, RD_RGB(0xffffff));
    rd_rrect(&c, 30, 30, 90, 90, 3, RD_RGB(0xffffff));
    rd_rrect(&c, 20, 20, 20, 30, 3, RD_RGB(0xffffff));
    rd_rrect(&c, 25, 25, 24, 24, 3, RD_RGB(0xffffff));
    check_int("clipped at the corner", at(&c, 0, 0), 0xffffffff);
    check_int("clipped at the far corner", at(&c, 39, 39), 0xffffffff);
    check_int("an empty box draws nothing", at(&c, 20, 25), 0);
    free(c.px); c = canvas(40, 40, RD_RGB(0));
    rd_hline(&c, 5, 35, 10, 1, RD_RGB(0xffffff));
    rd_vline(&c, 10, 5, 35, 2, RD_RGB(0xff0000));
    check_int("hline", at(&c, 20, 10), 0xffffffff);
    check_int("above it", at(&c, 20, 9), 0xff000000);
    check_int("vline, two wide (first)", at(&c, 10, 20), 0xffff0000);
    check_int("vline, two wide (second)", at(&c, 11, 20), 0xffff0000);
    free(c.px);
}

static void t_text(const char *dir)
{
    char path[512];
    snprintf(path, sizeof path, "%s/LiberationSans-Regular.ttf", dir);
    RdFont *f18 = rd_font_open(path, 18);
    check_true("the font opens", f18 != NULL);
    if (!f18) return;
    check_int("ascent of Liberation Sans 18 as Pillow's getmetrics", rd_font_ascent(f18), 17);
    check_int("descent", rd_font_descent(f18), 4);
    check_int("width of H (Pillow: 13)", rd_text_width(f18, "H"), 13);
    check_int("width of an empty string", rd_text_width(f18, ""), 0);
    check_int("a string is the sum of its letters", rd_text_width(f18, "HH"), 26);
    check_true("a non-ASCII letter has a width", rd_text_width(f18, "\xc3\xa9") > 5);
    check_true("malformed UTF-8 does not stop the walk", rd_text_width(f18, "a\xc3" "b\xff" "c\xe2\x82") > 10);
    check_true("an overlong form is a replacement, not the letter", rd_text_width(f18, "\xc0\x80") == rd_text_width(f18, "\xef\xbf\xbd"));
    check_true("a surrogate is a replacement", rd_text_width(f18, "\xed\xa0\x80") == rd_text_width(f18, "\xef\xbf\xbd"));

    static const struct { int h, v; int top, bottom; } want[] = {      /* the rows of 'H' relative to y, measured on Pillow 10.2 */
        { RD_LEFT, RD_TOP, 5, 17 }, { RD_LEFT, RD_MIDDLE, -5, 7 }, { RD_CENTER, RD_TOP, 5, 17 }, { RD_CENTER, RD_MIDDLE, -5, 7 }, { RD_RIGHT, RD_MIDDLE, -5, 7 }, { RD_LEFT, RD_BASELINE, -12, 0 },
    };
    for (size_t i = 0; i < sizeof want / sizeof *want; i++) {
        RdCanvas c = canvas(100, 100, 0);
        rd_text(&c, f18, 50, 50, "H", RD_RGB(0xffffff), want[i].h, want[i].v);
        int top = 999, bottom = -999, left = 999, right = -999;
        for (int y = 0; y < 100; y++) for (int x = 0; x < 100; x++) if (A(at(&c, x, y)) > 128) { if (y < top) top = y; if (y > bottom) bottom = y; if (x < left) left = x; if (x > right) right = x; }
        char what[64];
        snprintf(what, sizeof what, "anchor %d/%d: top row", want[i].h, want[i].v);
        check_int(what, top - 50, want[i].top);
        snprintf(what, sizeof what, "anchor %d/%d: bottom row", want[i].h, want[i].v);
        check_int(what, bottom + 1 - 50, want[i].bottom);
        if (want[i].h == RD_RIGHT) check_true("right anchor: the glyph ends at x", right + 1 <= 50 && right + 1 >= 47);
        if (want[i].h == RD_CENTER) check_true("centre anchor: the glyph is centred on x", abs((left + right + 1) / 2 - 50) <= 1);
        if (want[i].h == RD_LEFT) check_true("left anchor: the glyph starts at x", left >= 50 && left <= 53);
        free(c.px);
    }
    RdCanvas c = canvas(200, 40, 0);
    rd_text(&c, f18, 5, 5, "Hello", RD_RGB(0xff8000), RD_LEFT, RD_TOP);
    uint32_t seen = 0;
    int ink = 0;
    for (int i = 0; i < c.w * c.h; i++) if (A(c.px[i])) { ink++; seen |= c.px[i]; }
    check_true("text leaves grey-scale ink in the colour asked for", ink > 80 && R(seen | 0) > 0);
    int max_r = 0, max_b = 0;
    for (int i = 0; i < c.w * c.h; i++) { if (R(c.px[i]) > max_r) max_r = R(c.px[i]); if (B(c.px[i]) > max_b) max_b = B(c.px[i]); }
    check_true("it is orange (red high, blue none)", max_r > 200 && max_b == 0);
    free(c.px);

    c = canvas(300, 40, 0);
    int full = rd_text_width(f18, "A rather long caption that will not fit");
    rd_text_fit(&c, f18, 0, 5, 100, "A rather long caption that will not fit", RD_RGB(0xffffff), RD_LEFT, RD_TOP);
    int right = 0;
    for (int y = 0; y < 40; y++) for (int x = 0; x < 300; x++) if (A(at(&c, x, y))) right = x > right ? x : right;
    check_true("text cut to the width ends before it", full > 100 && right < 100);
    free(c.px); c = canvas(300, 40, 0);
    rd_text_fit(&c, f18, 0, 5, 200, "short", RD_RGB(0xffffff), RD_LEFT, RD_TOP);
    int n1 = 0;
    for (int i = 0; i < c.w * c.h; i++) n1 += A(c.px[i]) > 0;
    RdCanvas d = canvas(300, 40, 0);
    rd_text(&d, f18, 0, 5, "short", RD_RGB(0xffffff), RD_LEFT, RD_TOP);
    check_true("text that fits is drawn as it is", memcmp(c.px, d.px, (size_t)c.w * (size_t)c.h * 4) == 0 && n1 > 0);
    free(c.px); free(d.px);
    c = canvas(300, 40, 0);
    check_true("a width too small for the dots does not crash", rd_text_fit(&c, f18, 0, 5, 2, "a long text", RD_RGB(0xffffff), RD_LEFT, RD_TOP) >= 0);
    rd_text(&c, f18, 290, 5, "clipped at the edge", RD_RGB(0xffffff), RD_LEFT, RD_TOP);
    rd_text(&c, f18, -50, -5, "clipped at the other edge", RD_RGB(0xffffff), RD_LEFT, RD_TOP);
    free(c.px);

    int w0 = rd_text_width(f18, "Applications");
    for (int i = 0; i < 3000; i++) { char s[8]; snprintf(s, sizeof s, "%c", (char)(33 + (i * 7) % 90)); rd_text_width(f18, s); }
    uint32_t cp[4] = { 0x20ac, 0x4e2d, 0x1f600, 0x00e9 };
    for (int i = 0; i < 2000; i++) {
        char s[8] = { 0 };
        uint32_t u = cp[i % 4] + (uint32_t)(i / 4);
        if (u < 0x800) { s[0] = (char)(0xc0 | (u >> 6)); s[1] = (char)(0x80 | (u & 63)); }
        else if (u < 0x10000) { s[0] = (char)(0xe0 | (u >> 12)); s[1] = (char)(0x80 | ((u >> 6) & 63)); s[2] = (char)(0x80 | (u & 63)); }
        else { s[0] = (char)(0xf0 | (u >> 18)); s[1] = (char)(0x80 | ((u >> 12) & 63)); s[2] = (char)(0x80 | ((u >> 6) & 63)); s[3] = (char)(0x80 | (u & 63)); }
        rd_text_width(f18, s);
    }
    check_int("many different glyphs fill and reset the cache without changing a width", rd_text_width(f18, "Applications"), w0);
    rd_font_close(f18);
    snprintf(path, sizeof path, "%s/NoSuchFont.ttf", dir);
    check_true("a missing font is NULL", rd_font_open(path, 18) == NULL);
    snprintf(path, sizeof path, "%s/LiberationSans-Bold.ttf", dir);
    RdFont *b30 = rd_font_open(path, 30);
    check_true("bold 30 opens", b30 && rd_font_ascent(b30) == 28 && rd_font_descent(b30) == 7);
    rd_font_close(b30);
}

static void t_images(const char *tiny, const char *wide, const char *garbage)
{
    RdImage *im = rd_png_load(tiny);
    check_true("a 2x2 PNG loads", im && im->w == 2 && im->h == 2);
    if (im) {
        check_int("pixel 0 (opaque red)", im->px[0], 0xffff0000);
        check_true("pixel 1 is half-transparent green, premultiplied", A(im->px[1]) == 128 && G(im->px[1]) == 128 && R(im->px[1]) == 0);
        check_int("pixel 2 is fully transparent", A(im->px[2]), 0);
        check_int("pixel 3 (opaque blue)", im->px[3], 0xff0000ff);
        RdCanvas c = canvas(6, 6, RD_RGB(0x000000));
        rd_blit(&c, im, 1, 1);
        check_int("blit: red", at(&c, 1, 1), 0xffff0000);
        check_true("blit: green over black stays opaque with half brightness", A(at(&c, 2, 1)) == 255 && G(at(&c, 2, 1)) == 128);
        check_int("blit: transparent leaves the background", at(&c, 1, 2), 0xff000000);
        rd_blit(&c, im, -1, -1);
        rd_blit(&c, im, 5, 5);
        check_int("blit clipped at the corner", at(&c, 0, 0), 0xff0000ff);
        RdImage *up = rd_image_scaled(im, 4, 4);
        check_true("scaled up 4x4", up && up->w == 4 && up->h == 4 && up->px[0] == 0xffff0000 && up->px[15] == 0xff0000ff);
        RdImage *down = rd_image_scaled(up, 2, 2);
        check_true("and back down keeps each corner's character (the blur of the bilinear step stays within its neighbours)",
                   down && R(down->px[0]) > 2 * G(down->px[0]) && R(down->px[0]) > 2 * B(down->px[0]) && B(down->px[3]) > 2 * R(down->px[3]) && B(down->px[3]) > 2 * G(down->px[3]));
        RdImage *flat = rd_image_scaled(down, 1, 1);
        check_true("scaling down a few pixels to one is their mean", flat && A(flat->px[0]) > 100);
        rd_image_free(flat);
        RdImage *one = rd_image_scaled(im, 1, 1);
        check_true("2x2 to 1x1 is the average, still a valid premultiplied pixel", one && R(one->px[0]) <= A(one->px[0]) && G(one->px[0]) <= A(one->px[0]) && B(one->px[0]) <= A(one->px[0]) && A(one->px[0]) > 100);
        check_true("a size of zero is refused", rd_image_scaled(im, 0, 5) == NULL && rd_image_scaled(im, 5, -1) == NULL && rd_image_scaled(im, RD_IMAGE_MAX + 1, 5) == NULL);
        rd_blit_scaled(&c, im, 0, 0, 4, 4);
        rd_blit_scaled(&c, im, 2, 2, 2, 2);
        rd_image_free(up); rd_image_free(down); rd_image_free(one);
        free(c.px);
        rd_image_free(im);
    }
    check_true("a PNG wider than the limit is refused", rd_png_load(wide) == NULL);
    check_true("garbage is refused", rd_png_load(garbage) == NULL);
    check_true("a missing file is refused", rd_png_load("/nonexistent/none.png") == NULL);
    check_true("an empty buffer is refused", rd_png_from_memory((const unsigned char *)"", 0) == NULL);
    FILE *fp = fopen(tiny, "rb");
    unsigned char buf[4096];
    size_t n = fp ? fread(buf, 1, sizeof buf, fp) : 0;
    if (fp) fclose(fp);
    check_true("the tiny file is read", n > 40);
    int survived = 0;
    for (size_t cut = 0; cut < n; cut++) { RdImage *t = rd_png_from_memory(buf, cut); survived += t == NULL; rd_image_free(t); }
    check_true("every truncation of a PNG is refused or loads, none crashes", survived > (int)n / 2);
    unsigned seed = 12345;
    for (int i = 0; i < 4000; i++) {
        unsigned char m[4096];
        memcpy(m, buf, n);
        for (int k = 0; k < 4; k++) { seed = seed * 1103515245u + 12345u; m[(seed >> 8) % n] ^= (unsigned char)(seed >> 20); }
        RdImage *t = rd_png_from_memory(m, n);
        rd_image_free(t);
    }
    check_true("4000 corrupted copies of a PNG did not crash the reader", 1);

    uint8_t mask[9] = { 255, 128, 0, 0, 255, 0, 64, 64, 64 };
    RdCanvas c = canvas(5, 5, RD_RGB(0));
    rd_mask(&c, mask, 3, 3, 1, 1, RD_RGB(0xffffff));
    check_int("a mask: full coverage", at(&c, 1, 1), 0xffffffff);
    check_true("half coverage", R(at(&c, 2, 1)) >= 127 && R(at(&c, 2, 1)) <= 129);
    check_int("none", at(&c, 3, 1), 0xff000000);
    rd_mask(&c, mask, 3, 3, -2, -2, RD_RGB(0xffffff));
    rd_mask(&c, mask, 3, 3, 4, 4, RD_RGB(0xffffff));
    free(c.px);
}

int main(int argc, char **argv)
{
    if (argc < 5) { fprintf(stderr, "usage: test_render FONT_DIR tiny.png wide.png garbage.png\n"); return 2; }
    t_blending();
    t_shapes();
    t_text(argv[1]);
    t_images(argv[2], argv[3], argv[4]);
    printf("%d checks, %d failed\n", checks, failed);
    return failed ? 1 : 0;
}
