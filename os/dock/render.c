/* The desktop shell's software renderer: see render.h. */
#define _GNU_SOURCE
#include "render.h"

#include <ft2build.h>
#include FT_FREETYPE_H
#include <math.h>
#include <png.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static inline uint32_t div255(uint32_t v) { v += 128; return (v + (v >> 8)) >> 8; }

/* ------------------------------------------------------------------ pixels */
static inline uint32_t premul(RdColor col)
{
    uint32_t a = col >> 24;
    return (a << 24) | (div255(((col >> 16) & 255) * a) << 16) | (div255(((col >> 8) & 255) * a) << 8) | div255((col & 255) * a);
}

void rd_clear(RdCanvas *c, RdColor col)
{
    uint32_t v = premul(col);
    for (int y = 0; y < c->h; y++) for (int x = 0; x < c->w; x++) c->px[(size_t)y * (size_t)c->stride + (size_t)x] = v;
}

/* src-over of a straight colour at a coverage into the premultiplied canvas */
void rd_blend_px(RdCanvas *c, int x, int y, RdColor col, int cov)
{
    if (x < 0 || y < 0 || x >= c->w || y >= c->h || cov <= 0) return;
    uint32_t a = div255((col >> 24) * (uint32_t)(cov > 255 ? 255 : cov));
    if (a == 0) return;
    uint32_t r = div255(((col >> 16) & 255) * a), g = div255(((col >> 8) & 255) * a), b = div255((col & 255) * a);
    uint32_t *p = &c->px[(size_t)y * (size_t)c->stride + (size_t)x];
    uint32_t d = *p, ia = 255 - a;
    *p = ((a + div255((d >> 24) * ia)) << 24) | ((r + div255(((d >> 16) & 255) * ia)) << 16) | ((g + div255(((d >> 8) & 255) * ia)) << 8) | (b + div255((d & 255) * ia));
}

/* ------------------------------------------------------------------ shapes */
static float clampf(float v, float lo, float hi) { return v < lo ? lo : v > hi ? hi : v; }

/* Coverage of the pixel centred at (px, py) by a rounded box: one-pixel ramp on the signed distance to the outline. */
static float box_cov(float px, float py, float cx, float cy, float hx, float hy, float r)
{
    float qx = fabsf(px - cx) - (hx - r), qy = fabsf(py - cy) - (hy - r);
    float ox = qx > 0.f ? qx : 0.f, oy = qy > 0.f ? qy : 0.f;
    float d = sqrtf(ox * ox + oy * oy) + fminf(fmaxf(qx, qy), 0.f) - r;
    return clampf(0.5f - d, 0.f, 1.f);
}

static void shape(RdCanvas *c, float x0, float y0, float x1, float y1, float r, float ow, RdColor col)
{
    if (x1 <= x0 || y1 <= y0) return;
    float hx = (x1 - x0) / 2, hy = (y1 - y0) / 2, cx = x0 + hx, cy = y0 + hy;
    r = clampf(r, 0.f, fminf(hx, hy));
    int ix0 = (int)floorf(x0) - 1, iy0 = (int)floorf(y0) - 1, ix1 = (int)ceilf(x1) + 1, iy1 = (int)ceilf(y1) + 1;
    if (ix0 < 0) ix0 = 0;
    if (iy0 < 0) iy0 = 0;
    if (ix1 > c->w) ix1 = c->w;
    if (iy1 > c->h) iy1 = c->h;
    bool ring = ow > 0.f;
    float ihx = hx - ow, ihy = hy - ow, ir = r > ow ? r - ow : 0.f;
    for (int y = iy0; y < iy1; y++) {
        for (int x = ix0; x < ix1; x++) {
            float cov = box_cov((float)x + 0.5f, (float)y + 0.5f, cx, cy, hx, hy, r);
            if (ring && cov > 0.f && ihx > 0.f && ihy > 0.f) cov = fmaxf(0.f, cov - box_cov((float)x + 0.5f, (float)y + 0.5f, cx, cy, ihx, ihy, fminf(ir, fminf(ihx, ihy))));
            if (cov > 0.f) rd_blend_px(c, x, y, col, (int)(cov * 255.f + 0.5f));
        }
    }
}

void rd_rrect(RdCanvas *c, float x0, float y0, float x1, float y1, float r, RdColor fill)
{
    if (r <= 0.f && x0 == floorf(x0) && y0 == floorf(y0) && x1 == floorf(x1) && y1 == floorf(y1)) {      /* a whole-pixel box: no distances needed */
        for (int y = (int)y0 < 0 ? 0 : (int)y0; y < (int)y1 && y < c->h; y++)
            for (int x = (int)x0 < 0 ? 0 : (int)x0; x < (int)x1 && x < c->w; x++) rd_blend_px(c, x, y, fill, 255);
        return;
    }
    shape(c, x0, y0, x1, y1, r, 0.f, fill);
}

void rd_rrect_ring(RdCanvas *c, float x0, float y0, float x1, float y1, float r, float ow, RdColor line) { shape(c, x0, y0, x1, y1, r, ow, line); }
void rd_pill(RdCanvas *c, float x0, float y0, float x1, float y1, RdColor fill) { rd_rrect(c, x0, y0, x1, y1, (y1 - y0) / 2, fill); }
void rd_disc(RdCanvas *c, float cx, float cy, float r, RdColor fill) { rd_rrect(c, cx - r, cy - r, cx + r, cy + r, r, fill); }
void rd_hline(RdCanvas *c, float x0, float x1, float y, float th, RdColor col) { rd_rrect(c, x0, y, x1, y + th, 0.f, col); }
void rd_vline(RdCanvas *c, float x, float y0, float y1, float th, RdColor col) { rd_rrect(c, x, y0, x + th, y1, 0.f, col); }

/* ------------------------------------------------------------------ text */
static FT_Library ft_lib;                                  /* the only global: FreeType's library handle */

typedef struct { uint32_t cp; bool used; int adv, left, top, w, h; uint8_t *bm; } Glyph;
#define GLYPH_SLOTS 512

struct RdFont {
    FT_Face face;
    int px, ascent, descent;
    Glyph g[GLYPH_SLOTS];
    int nused;
};

RdFont *rd_font_open(const char *path, int px)
{
    if (!ft_lib && FT_Init_FreeType(&ft_lib) != 0) return NULL;
    RdFont *f = calloc(1, sizeof *f);
    if (!f) return NULL;
    if (FT_New_Face(ft_lib, path, 0, &f->face) != 0) { free(f); return NULL; }
    if (FT_Set_Pixel_Sizes(f->face, 0, (FT_UInt)px) != 0) { FT_Done_Face(f->face); free(f); return NULL; }
    f->px = px;
    f->ascent = (int)(f->face->size->metrics.ascender / 64);
    f->descent = (int)(-f->face->size->metrics.descender / 64);
    return f;
}

static void glyphs_clear(RdFont *f)
{
    for (int i = 0; i < GLYPH_SLOTS; i++) { free(f->g[i].bm); f->g[i] = (Glyph){ 0 }; }
    f->nused = 0;
}

void rd_font_close(RdFont *f)
{
    if (!f) return;
    glyphs_clear(f);
    FT_Done_Face(f->face);
    free(f);
}

int rd_font_ascent(const RdFont *f) { return f->ascent; }
int rd_font_descent(const RdFont *f) { return f->descent; }

/* the glyph for a code point, rendered once: hinted, grey-scale */
static const Glyph *glyph(RdFont *f, uint32_t cp)
{
    unsigned h = (cp * 2654435761u) % GLYPH_SLOTS;
    for (int probe = 0; probe < GLYPH_SLOTS; probe++) {
        Glyph *g = &f->g[(h + (unsigned)probe) % GLYPH_SLOTS];
        if (g->used && g->cp == cp) return g;
        if (g->used) continue;
        if (f->nused >= GLYPH_SLOTS * 3 / 4) { glyphs_clear(f); return glyph(f, cp); }                 /* full: start again rather than grow */
        FT_UInt idx = FT_Get_Char_Index(f->face, cp);
        if (FT_Load_Glyph(f->face, idx, FT_LOAD_DEFAULT) != 0 || FT_Render_Glyph(f->face->glyph, FT_RENDER_MODE_NORMAL) != 0) return NULL;
        FT_GlyphSlot s = f->face->glyph;
        g->used = true;
        g->cp = cp;
        g->adv = (int)((s->metrics.horiAdvance + 32) / 64);
        g->left = s->bitmap_left;
        g->top = s->bitmap_top;
        g->w = (int)s->bitmap.width;
        g->h = (int)s->bitmap.rows;
        g->bm = NULL;
        if (g->w > 0 && g->h > 0) {
            g->bm = malloc((size_t)g->w * (size_t)g->h);
            if (!g->bm) { g->used = false; return NULL; }
            for (int y = 0; y < g->h; y++) memcpy(g->bm + (size_t)y * (size_t)g->w, s->bitmap.buffer + (ptrdiff_t)y * s->bitmap.pitch, (size_t)g->w);
        }
        f->nused++;
        return g;
    }
    return NULL;
}

/* UTF-8, one code point at a time; malformed input is U+FFFD and always advances */
static uint32_t next_cp(const char **sp)
{
    const unsigned char *s = (const unsigned char *)*sp;
    uint32_t cp;
    int n;
    if (s[0] < 0x80) { cp = s[0]; n = 1; }
    else if ((s[0] & 0xe0) == 0xc0) { cp = s[0] & 0x1f; n = 2; }
    else if ((s[0] & 0xf0) == 0xe0) { cp = s[0] & 0x0f; n = 3; }
    else if ((s[0] & 0xf8) == 0xf0) { cp = s[0] & 0x07; n = 4; }
    else { *sp += 1; return 0xfffd; }
    for (int i = 1; i < n; i++) {
        if ((s[i] & 0xc0) != 0x80) { *sp += i; return 0xfffd; }
        cp = (cp << 6) | (s[i] & 0x3f);
    }
    static const uint32_t min[5] = { 0, 0, 0x80, 0x800, 0x10000 };
    *sp += n;
    return (cp < min[n] || cp > 0x10ffff || (cp >= 0xd800 && cp < 0xe000)) ? 0xfffd : cp;
}

int rd_text_width(RdFont *f, const char *s)
{
    int w = 0;
    while (*s) {
        const Glyph *g = glyph(f, next_cp(&s));
        if (g) w += g->adv;
    }
    return w;
}

int rd_text(RdCanvas *c, RdFont *f, int x, int y, const char *s, RdColor col, int h, int v)
{
    int width = rd_text_width(f, s);
    int pen = h == RD_CENTER ? x - (width + 1) / 2 : h == RD_RIGHT ? x - width : x;        /* a centred string of odd width starts on a half pixel, which Pillow floors */
    int base = v == RD_TOP ? y + f->ascent : v == RD_MIDDLE ? y + (f->ascent - f->descent + 1) / 2 : y;           /* Pillow's anchors 'a', 'm', 's' */
    while (*s) {
        const Glyph *g = glyph(f, next_cp(&s));
        if (!g) continue;
        for (int gy = 0; gy < g->h; gy++)
            for (int gx = 0; gx < g->w; gx++) rd_blend_px(c, pen + g->left + gx, base - g->top + gy, col, g->bm[(size_t)gy * (size_t)g->w + (size_t)gx]);
        pen += g->adv;
    }
    return width;
}

int rd_text_fit(RdCanvas *c, RdFont *f, int x, int y, int maxw, const char *s, RdColor col, int h, int v)
{
    if (rd_text_width(f, s) <= maxw) return rd_text(c, f, x, y, s, col, h, v);
    char buf[512];
    size_t len = strlen(s);
    if (len > sizeof buf - 4) len = sizeof buf - 4;
    int dots = rd_text_width(f, "...");
    size_t keep = 0;
    int w = 0;
    const char *p = s;
    while ((size_t)(p - s) < len) {                                    /* the longest prefix (whole code points) that leaves room for the dots */
        const char *q = p;
        const Glyph *g = glyph(f, next_cp(&q));
        int adv = g ? g->adv : 0;
        if (w + adv + dots > maxw) break;
        w += adv;
        p = q;
        keep = (size_t)(p - s);
    }
    memcpy(buf, s, keep);
    strcpy(buf + keep, "...");
    return rd_text(c, f, x, y, buf, col, h, v);
}

/* ------------------------------------------------------------------ images */
static RdImage *image_new(int w, int h)
{
    if (w <= 0 || h <= 0 || w > RD_IMAGE_MAX || h > RD_IMAGE_MAX) return NULL;
    RdImage *im = malloc(sizeof *im);
    if (!im) return NULL;
    im->w = w;
    im->h = h;
    im->px = calloc((size_t)w * (size_t)h, 4);
    if (!im->px) { free(im); return NULL; }
    return im;
}

void rd_image_free(RdImage *im)
{
    if (!im) return;
    free(im->px);
    free(im);
}

RdImage *rd_png_from_memory(const unsigned char *data, size_t len)
{
    png_image img;
    memset(&img, 0, sizeof img);
    img.version = PNG_IMAGE_VERSION;
    if (len > (size_t)RD_FILE_MAX || !png_image_begin_read_from_memory(&img, data, len)) { png_image_free(&img); return NULL; }
    if (img.width == 0 || img.height == 0 || img.width > RD_IMAGE_MAX || img.height > RD_IMAGE_MAX) { png_image_free(&img); return NULL; }
    RdImage *im = image_new((int)img.width, (int)img.height);
    if (!im) { png_image_free(&img); return NULL; }
    img.format = PNG_FORMAT_BGRA;                                      /* B, G, R, A bytes: a little-endian 0xAARRGGBB, straight alpha */
    if (!png_image_finish_read(&img, NULL, im->px, 0, NULL)) { png_image_free(&img); rd_image_free(im); return NULL; }
    png_image_free(&img);
    for (size_t i = 0; i < (size_t)im->w * (size_t)im->h; i++) im->px[i] = premul(im->px[i]);
    return im;
}

RdImage *rd_png_load(const char *path)
{
    FILE *fp = fopen(path, "rb");
    if (!fp) return NULL;
    unsigned char *buf = malloc(RD_FILE_MAX + 1);
    if (!buf) { fclose(fp); return NULL; }
    size_t n = fread(buf, 1, RD_FILE_MAX + 1, fp);
    fclose(fp);
    RdImage *im = n > 0 && n <= (size_t)RD_FILE_MAX ? rd_png_from_memory(buf, n) : NULL;
    free(buf);
    return im;
}

static void unpack(uint32_t p, float *a, float *r, float *g, float *b)
{
    *a = (float)(p >> 24);
    *r = (float)((p >> 16) & 255);
    *g = (float)((p >> 8) & 255);
    *b = (float)(p & 255);
}

static uint32_t pack(float a, float r, float g, float b)
{
    uint32_t ia = (uint32_t)clampf(a + 0.5f, 0.f, 255.f), ir = (uint32_t)clampf(r + 0.5f, 0.f, 255.f), ig = (uint32_t)clampf(g + 0.5f, 0.f, 255.f), ib = (uint32_t)clampf(b + 0.5f, 0.f, 255.f);
    if (ir > ia) ir = ia;                                              /* keep it a valid premultiplied pixel */
    if (ig > ia) ig = ia;
    if (ib > ia) ib = ia;
    return (ia << 24) | (ir << 16) | (ig << 8) | ib;
}

RdImage *rd_image_scaled(const RdImage *src, int w, int h)
{
    RdImage *dst = image_new(w, h);
    if (!dst || !src) { rd_image_free(dst); return NULL; }
    float sx = (float)src->w / (float)w, sy = (float)src->h / (float)h;
    for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) {
            float a = 0, r = 0, g = 0, b = 0;
            if (sx >= 1.f && sy >= 1.f) {                              /* shrinking: the average of the source box under the pixel */
                float x0 = (float)x * sx, x1 = x0 + sx, y0 = (float)y * sy, y1 = y0 + sy, wsum = 0;
                for (int yy = (int)y0; yy < (int)ceilf(y1) && yy < src->h; yy++) {
                    float wy = fminf((float)yy + 1, y1) - fmaxf((float)yy, y0);
                    for (int xx = (int)x0; xx < (int)ceilf(x1) && xx < src->w; xx++) {
                        float wx = fminf((float)xx + 1, x1) - fmaxf((float)xx, x0), wgt = wx * wy, pa, pr, pg, pb;
                        unpack(src->px[(size_t)yy * (size_t)src->w + (size_t)xx], &pa, &pr, &pg, &pb);
                        a += pa * wgt; r += pr * wgt; g += pg * wgt; b += pb * wgt; wsum += wgt;
                    }
                }
                if (wsum > 0) { a /= wsum; r /= wsum; g /= wsum; b /= wsum; }
            } else {                                                   /* growing: bilinear between the centres of the four nearest */
                float fx = ((float)x + 0.5f) * sx - 0.5f, fy = ((float)y + 0.5f) * sy - 0.5f;
                int x0 = (int)floorf(fx), y0 = (int)floorf(fy);
                float tx = fx - (float)x0, ty = fy - (float)y0;
                for (int k = 0; k < 4; k++) {
                    int xx = x0 + (k & 1), yy = y0 + (k >> 1);
                    xx = xx < 0 ? 0 : xx >= src->w ? src->w - 1 : xx;
                    yy = yy < 0 ? 0 : yy >= src->h ? src->h - 1 : yy;
                    float wgt = ((k & 1) ? tx : 1 - tx) * ((k >> 1) ? ty : 1 - ty), pa, pr, pg, pb;
                    unpack(src->px[(size_t)yy * (size_t)src->w + (size_t)xx], &pa, &pr, &pg, &pb);
                    a += pa * wgt; r += pr * wgt; g += pg * wgt; b += pb * wgt;
                }
            }
            dst->px[(size_t)y * (size_t)w + (size_t)x] = pack(a, r, g, b);
        }
    }
    return dst;
}

void rd_blit(RdCanvas *c, const RdImage *im, int x, int y)
{
    for (int iy = 0; iy < im->h; iy++) {
        int cy = y + iy;
        if (cy < 0 || cy >= c->h) continue;
        for (int ix = 0; ix < im->w; ix++) {
            int cx = x + ix;
            if (cx < 0 || cx >= c->w) continue;
            uint32_t s = im->px[(size_t)iy * (size_t)im->w + (size_t)ix], a = s >> 24;
            if (a == 0) continue;
            uint32_t *p = &c->px[(size_t)cy * (size_t)c->stride + (size_t)cx], d = *p, ia = 255 - a;
            *p = ((a + div255((d >> 24) * ia)) << 24) | ((((s >> 16) & 255) + div255(((d >> 16) & 255) * ia)) << 16) |
                 ((((s >> 8) & 255) + div255(((d >> 8) & 255) * ia)) << 8) | ((s & 255) + div255((d & 255) * ia));
        }
    }
}

void rd_blit_scaled(RdCanvas *c, const RdImage *im, int x, int y, int w, int h)
{
    if (im->w == w && im->h == h) { rd_blit(c, im, x, y); return; }
    RdImage *s = rd_image_scaled(im, w, h);
    if (!s) return;
    rd_blit(c, s, x, y);
    rd_image_free(s);
}

void rd_mask(RdCanvas *c, const uint8_t *alpha, int w, int h, int x, int y, RdColor col)
{
    for (int iy = 0; iy < h; iy++)
        for (int ix = 0; ix < w; ix++) rd_blend_px(c, x + ix, y + iy, col, alpha[(size_t)iy * (size_t)w + (size_t)ix]);
}
