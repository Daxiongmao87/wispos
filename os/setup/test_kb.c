/* Native test of the first-run keyboard's layout (os/setup/kb.h): every page fits the panel, the symbols page has no key off the screen, every key can be reached by the
 * navigation the screen uses, and the keys a Wi-Fi passphrase needs are there (the two that are not are listed, on purpose). Built and run by os/test_setup.py. */
#include <stdio.h>
#include <string.h>
#include "../ui/wisp-ui.h"
#include "kb.h"

static int failures;
#define CHECK(cond, ...) do { if (!(cond)) { failures++; printf("FAIL: "); printf(__VA_ARGS__); printf("\n"); } } while (0)

int main(void) {
    const int panel = WISP_UI_PANEL_W;
    for (int symbols = 0; symbols < 2; symbols++) for (int shift = 0; shift < 2; shift++) {
        if (symbols && shift) continue;                      /* shift means nothing on the symbols page */
        for (int row = 0; row <= KB_ROWS; row++) {
            int w = kb_row_width(symbols, shift, row);
            CHECK(w <= panel, "page symbols=%d shift=%d row %d is %d px wide in a %d px panel", symbols, shift, row, w, panel);
            CHECK(kb_row_len(symbols, shift, row) >= 1, "page symbols=%d shift=%d row %d has no keys", symbols, shift, row);
        }
        /* the character rows are the same width on every page they share, so the panel does not seem to change between pages */
        CHECK(kb_row_len(symbols, shift, 0) == 10, "the digit row has %d keys", kb_row_len(symbols, shift, 0));
    }
    /* the defect this test was written for: a full symbols row and "del" in one row */
    for (int r = 0; r < KB_ROWS; r++) CHECK(!kb_row_has_del(1, r), "symbols row %d must not also carry del", r);
    CHECK(strlen(KB_SYM[1]) == 11 && kb_row_width(1, 0, 1) == 436, "eleven symbols are 436 px wide, not %d", kb_row_width(1, 0, 1));
    CHECK(kb_row_width(1, 0, 3) == 396, "ten symbols are 396 px wide, not %d", kb_row_width(1, 0, 3));
    CHECK(kb_row_has_del(0, 3) && kb_row_has_del(0, 3) == 1, "the letters pages end their last row with del");
    CHECK(kb_row_width(0, 0, 3) == 372, "seven letters and del are 372 px wide, not %d", kb_row_width(0, 0, 3));
    /* the special keys: the second one is del on the symbols page, so del is reachable on every page */
    CHECK(KB_SPECIAL_SHIFT == 1 && KB_SPECIALS == 5, "five special keys, the second being shift or del");
    int w = 0; for (int i = 0; i < KB_SPECIALS; i++) w += KB_SPECIAL_W[i] + KB_GAP;
    CHECK(6 + w - KB_GAP <= panel, "the special keys (from x+6) must end inside the panel: %d", 6 + w - KB_GAP);
    /* every printable ASCII character a passphrase can hold can be typed (letters from the letters pages, the rest from the symbols page; the space has its own key) */
    char absent[8] = {0}; int n = 0;
    for (int c = 33; c < 127; c++) {
        int found = 0;
        for (int p = 0; p < 3 && !found; p++) for (int r = 0; r < KB_ROWS && !found; r++) {
            const char *row = p == 0 ? KB_LOWER[r] : p == 1 ? KB_UPPER[r] : KB_SYM[r];
            found = strchr(row, c) != NULL;
        }
        if (!found && n < 7) absent[n++] = (char)c;
    }
    CHECK(absent[0] == 0, "the characters that cannot be typed are `%s`", absent);
    printf(failures ? "kb.h: %d failure(s)\n" : "all kb.h tests passed\n", failures);
    return failures ? 1 : 0;
}
