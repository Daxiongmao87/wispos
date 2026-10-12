/* The first-run screen's on-screen keyboard: which keys each page has and how wide each row is. No SDL in here, so a native test (test_kb.c) checks that every page fits the panel
 * and that every key can be reached; wisp-setup.c draws and navigates with these.
 *
 * Pages: letters (lower and upper) and symbols. Four rows of character keys (KB_ROWS), then the row of special keys. The letters pages end their last character row with the
 * "del" key (seven letters and del). The symbols page has up to ELEVEN symbols in a row, which together with "del" would not fit the 450 px panel (with ten 40 px keys it was 504 px and
 * the key ran off the screen, found 2026-10-12 while drawing the desktop keyboard): there "del" takes the place of "shift" among the special keys, which a page without letters
 * does not need, and the keys are 36 px wide. Every printable ASCII character can be typed (operator, 2026-10-12: a passphrase may hold any of them). */
#ifndef WISP_SETUP_KB_H
#define WISP_SETUP_KB_H

#include <string.h>

#define KB_ROWS 4
#define KB_KEY_W 40
#define KB_KEY_W_SYM 36                   /* the symbols page has ELEVEN keys in two rows (grave accent and tilde included): narrower keys keep them inside the panel */
#define KB_KEY_H 40
#define KB_GAP 4
#define KB_DEL_W 64
#define KB_SPECIALS 5
#define KB_SPECIAL_SHIFT 1                /* the second special key: "shift" on the letters pages, "del" on the symbols page */

static const char *const KB_LOWER[KB_ROWS] = {"1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm"};
static const char *const KB_UPPER[KB_ROWS] = {"1234567890", "QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"};
static const char *const KB_SYM[KB_ROWS]   = {"1234567890", "!@#$%^&*()~", "-_=+[]{};:`", "'\",.<>/?\\|"};
static const int KB_SPECIAL_W[KB_SPECIALS] = {54, 66, 118, 70, 114};            /* abc/123, shift or del, space, show/hide, Connect/Next */

static inline int kb_key_w(int symbols) { return symbols ? KB_KEY_W_SYM : KB_KEY_W; }
static inline const char *const *kb_page(int symbols, int shift) { return symbols ? KB_SYM : shift ? KB_UPPER : KB_LOWER; }
static inline int kb_row_has_del(int symbols, int row) { return row == 3 && !symbols; }
static inline int kb_row_len(int symbols, int shift, int row) {            /* keys in the row, "del" included; row KB_ROWS is the special keys; anything else is not a row */
    if (row == KB_ROWS) return KB_SPECIALS;
    if (row < 0 || row > KB_ROWS) return 0;
    return (int)strlen(kb_page(symbols, shift)[row]) + kb_row_has_del(symbols, row);
}
static inline int kb_row_width(int symbols, int shift, int row) {          /* pixels from the first key's left edge to the last key's right edge */
    if (row == KB_ROWS) { int w = 0; for (int i = 0; i < KB_SPECIALS; i++) w += KB_SPECIAL_W[i] + (i ? KB_GAP : 0); return w; }
    if (row < 0 || row > KB_ROWS) return 0;
    int chars = (int)strlen(kb_page(symbols, shift)[row]);
    return chars * (kb_key_w(symbols) + KB_GAP) - KB_GAP + (kb_row_has_del(symbols, row) ? KB_GAP + KB_DEL_W : 0);
}

#endif
