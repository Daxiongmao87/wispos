/* The desktop shell's software renderer: shapes, text and images into a premultiplied ARGB8888 canvas (what a wl_shm buffer holds). No Wayland and no global state, so the pictures of
 * docs/WISPOS-UI-LANGUAGE.md "Desktop mode" can be reproduced and compared on the host (os/test_dock_render.py).
 *
 * Shapes are anti-aliased with a one-pixel ramp on the signed distance to the outline; a box is the continuous region [x0, x1] x [y0, y1] (so (36, 5, 72, 41) covers pixels 36..71 and
 * 5..40 fully), the convention of os/dock/mockup.py. Text is FreeType, grey-scale, hinted the way Pillow's basic layout does it (the pictures were drawn with that): integer advances,
 * the font's kerning table, a baseline from the anchor. Images are PNGs read with libpng (bounded: they may come from a downloaded file's icon). Every colour is 0xAARRGGBB, straight
 * (not premultiplied); the canvas stores premultiplied. */
#ifndef WISPOS_DOCK_RENDER_H
#define WISPOS_DOCK_RENDER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef struct { int w, h, stride; uint32_t *px; } RdCanvas;        /* stride in pixels */
typedef uint32_t RdColor;
#define RD_RGB(rgb)      (0xff000000u | ((uint32_t)(rgb) & 0xffffffu))
#define RD_RGBA(rgb, a)  (((uint32_t)(a) << 24) | ((uint32_t)(rgb) & 0xffffffu))

#define RD_IMAGE_MAX 1024                /* an icon larger than this is refused */
#define RD_FILE_MAX  (4 * 1024 * 1024)

/* ------------------------------------------------------------------ canvas and shapes */
void rd_clear(RdCanvas *c, RdColor col);                                     /* every pixel becomes col (not blended) */
void rd_blend_px(RdCanvas *c, int x, int y, RdColor col, int coverage);     /* one pixel, src-over, coverage 0..255 times the colour's alpha */
void rd_rrect(RdCanvas *c, float x0, float y0, float x1, float y1, float r, RdColor fill);
void rd_rrect_ring(RdCanvas *c, float x0, float y0, float x1, float y1, float r, float ow, RdColor line);     /* the outline only, ow wide, inside the box */
void rd_pill(RdCanvas *c, float x0, float y0, float x1, float y1, RdColor fill);        /* fully round ends */
void rd_disc(RdCanvas *c, float cx, float cy, float r, RdColor fill);
void rd_hline(RdCanvas *c, float x0, float x1, float y, float th, RdColor col);
void rd_vline(RdCanvas *c, float x, float y0, float y1, float th, RdColor col);

/* ------------------------------------------------------------------ text */
typedef struct RdFont RdFont;
RdFont *rd_font_open(const char *path, int px);               /* NULL when it cannot be opened */
void    rd_font_close(RdFont *f);
int     rd_font_ascent(const RdFont *f);                       /* whole pixels, as Pillow's getmetrics() */
int     rd_font_descent(const RdFont *f);
int     rd_text_width(RdFont *f, const char *utf8);           /* advance of the whole string */
enum { RD_LEFT = 0, RD_CENTER = 1, RD_RIGHT = 2 };
enum { RD_TOP = 0, RD_MIDDLE = 1, RD_BASELINE = 2 };           /* the vertical anchors 'a', 'm' and 's' of Pillow */
/* Draws `utf8` with its anchor at (x, y). Returns the advance. */
int     rd_text(RdCanvas *c, RdFont *f, int x, int y, const char *utf8, RdColor col, int h, int v);
/* The same, cut with "..." so that it is no wider than maxw (never wrapped). The text drawn is written to `drawn` when it is not NULL. */
int     rd_text_fit(RdCanvas *c, RdFont *f, int x, int y, int maxw, const char *utf8, RdColor col, int h, int v);

/* ------------------------------------------------------------------ images and masks */
typedef struct { int w, h; uint32_t *px; } RdImage;            /* premultiplied ARGB8888 */
RdImage *rd_png_load(const char *path);                        /* NULL for anything that is not a sane PNG (unreadable, larger than RD_IMAGE_MAX or RD_FILE_MAX) */
RdImage *rd_png_from_memory(const unsigned char *data, size_t len);
RdImage *rd_image_scaled(const RdImage *src, int w, int h);    /* area average down, bilinear up */
void     rd_image_free(RdImage *im);
void     rd_blit(RdCanvas *c, const RdImage *im, int x, int y);              /* src-over */
void     rd_blit_scaled(RdCanvas *c, const RdImage *im, int x, int y, int w, int h);
/* An 8-bit alpha mask drawn in one colour (the glyphs of the icon set). */
void     rd_mask(RdCanvas *c, const uint8_t *alpha, int w, int h, int x, int y, RdColor col);

#endif
