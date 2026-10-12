/* wisp-setup: WispOS first-run Wi-Fi screen.
 *
 * Shown before Steam is installed and never goes away until the device is online: a continuously refreshed list of
 * networks, a password field with an on-screen keyboard driven by the gamepad, and the steps that follow (reaching
 * Steam, downloading it). 720x480, gamepad first. Libraries: SDL2, SDL2_ttf, SDL2_image (all already in the image).
 *
 * Run with no arguments it is the real thing: it scans continuously, connects through nmcli, checks that Steam's servers
 * are reachable, and exits 0 only when they are (exits at once if the device is already online). For the design renders:
 *     wisp-setup --demo STATE --out FILE.bmp      STATE: list password connecting checking failed nointernet download
 * For tests: --script "down a type:secret start until:done" feeds buttons, --shots DIR saves a frame per state, --timeout S.
 * First run only: --intro MARKER plays the boot -> setup hand-over (3 s, fade out then fade in; os/setup/handover.h) before the first screen, unless the file MARKER exists, and creates it
 * when it is over. The first frame is the boot screen's own picture (os/setup/bootframe.c draws it with the code the boot screens share), so the panel does not change when this window
 * takes over from the boot screen. A design render of one frame: --demo handover:T (T from 0 to 1).
 * Environment: WISP_NMCLI, WISP_CURL, WISP_CHECK_URL replace the programs and the URL (the tests use fakes).
 */
#include <fcntl.h>
#include <SDL2/SDL.h>
#include <SDL2/SDL_ttf.h>
#include "../ui/wisp-ui.h"
#include "kb.h"
#include "handover.h"
#include "bootframe.h"
#include <SDL2/SDL_image.h>
#include "nm.h"
#ifdef WISP_DEBUG_CRASH
#include <execinfo.h>
static void on_segv(int sig) { void *bt[40]; int n = backtrace(bt, 40); fprintf(stderr, "CRASH signal %d\n", sig); backtrace_symbols_fd(bt, n, 2); _exit(139); }
#endif
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define W 720
#define H 480
#define PANEL_X WISP_UI_PANEL_X
#define PANEL_W WISP_UI_PANEL_W

typedef enum { ST_LIST, ST_PASSWORD, ST_CONNECTING, ST_CHECKING, ST_FAILED, ST_NOINTERNET, ST_DOWNLOAD, ST_NOADAPTER, ST_DONE, ST_DLFAILED } State;
static const char *STATE_NAMES[] = {"list", "password", "connecting", "checking", "failed", "nointernet", "download", "noadapter", "done", "dlfailed"};

typedef struct {
    State st;
    Net nets[32]; int n, sel, top;
    char ssid[64];
    char pw[96]; int show, shift, symbols, kr, kc;
    int choice, hidden, stage, job, shot_pending;
    float t, progress; long done_mb, total_mb;
    char msg[160], phase[24]; int installing;
} App;

/* The palette, radius and layout constants are the WispOS design tokens (os/ui/wisp-ui.h, docs/WISPOS-UI-LANGUAGE.md): Steam's own palette,
 * read from its stylesheets, so this screen and Steam look like one system. This screen is the reference implementation of that language. */
static const SDL_Color C_BG = WISP_SDL_COLOR(WISP_UI_BG), C_PANEL = WISP_SDL_COLOR(WISP_UI_PANEL), C_LINE = WISP_SDL_COLOR(WISP_UI_LINE), C_BLACK = {0, 0, 0, 255};
static const SDL_Color C_TEXT = WISP_SDL_COLOR(WISP_UI_TEXT), C_DIM = WISP_SDL_COLOR(WISP_UI_DIM), C_DIM2 = WISP_SDL_COLOR(WISP_UI_DIM2), C_GLYPH = WISP_SDL_COLOR(WISP_UI_GLYPH);
static const SDL_Color C_ACCENT = WISP_SDL_COLOR(WISP_UI_ACCENT), C_OK = WISP_SDL_COLOR(WISP_UI_OK), C_BAD = WISP_SDL_COLOR(WISP_UI_BAD);
static const SDL_Color C_FOCUS = WISP_SDL_COLOR(WISP_UI_FOCUS), C_FOCUS_FG = WISP_SDL_COLOR(WISP_UI_FOCUS_FG);
#define RAD WISP_UI_RADIUS

static SDL_Renderer *R;
static TTF_Font *F_TITLE, *F_BODY, *F_SMALL, *F_TINY;
static SDL_Texture *BADGE; static int BADGE_W, BADGE_H;
static int IS_DEV;      /* a development build (marker file present): the screen says so */

static void col(SDL_Color c) { SDL_SetRenderDrawColor(R, c.r, c.g, c.b, c.a); }

static void rrect(int x, int y, int w, int h, int rad, SDL_Color c) {
    col(c);
    if (rad * 2 > h) rad = h / 2;
    for (int j = 0; j < h; j++) {
        int inset = 0;
        if (j < rad) { int dy = rad - j; inset = rad - (int)sqrtf((float)(rad * rad - dy * dy)); }
        else if (j >= h - rad) { int dy = j - (h - rad) + 1; inset = rad - (int)sqrtf((float)(rad * rad - dy * dy)); }
        SDL_RenderDrawLine(R, x + inset, y + j, x + w - 1 - inset, y + j);
    }
}
static void disc(int cx, int cy, int r, SDL_Color c) { rrect(cx - r, cy - r, 2 * r, 2 * r, r, c); }

/* Shorten buf with an ellipsis until it fits max_w pixels, cutting only on UTF-8 character boundaries. */
static void fit(TTF_Font *f, char *buf, size_t cap, int max_w) {
    int tw = 0, th = 0;
    TTF_SizeUTF8(f, buf, &tw, &th);
    if (max_w <= 0 || tw <= max_w) return;
    size_t n = strlen(buf);
    while (n > 0) {
        n--; while (n > 0 && ((unsigned char)buf[n] & 0xC0) == 0x80) n--;
        char t[256]; snprintf(t, sizeof t, "%.*s...", (int)n, buf);
        TTF_SizeUTF8(f, t, &tw, &th);
        if (tw <= max_w) { snprintf(buf, cap, "%s", t); return; }
    }
    buf[0] = 0;
}

/* anchor: 0 left, 1 centre, 2 right; max_w = 0 means no limit */
static void text(TTF_Font *f, const char *s, int x, int y, SDL_Color c, int anchor, int max_w) {
    char buf[256]; snprintf(buf, sizeof buf, "%s", s);
    fit(f, buf, sizeof buf, max_w);
    if (!*buf) return;
    SDL_Surface *su = TTF_RenderUTF8_Blended(f, buf, c);
    if (!su) return;
    SDL_Texture *tx = SDL_CreateTextureFromSurface(R, su);
    SDL_Rect d = {x, y, su->w, su->h};
    if (anchor == 1) d.x -= su->w / 2; else if (anchor == 2) d.x -= su->w;
    SDL_RenderCopy(R, tx, NULL, &d);
    SDL_DestroyTexture(tx); SDL_FreeSurface(su);
}

static void background(void) { col(C_BG); SDL_RenderClear(R); }

static void signal_bars(int x, int y, int signal, SDL_Color on, SDL_Color off) {
    int lit = signal >= 75 ? 4 : signal >= 50 ? 3 : signal >= 25 ? 2 : 1;
    for (int i = 0; i < 4; i++) {
        int h = 6 + i * 4;
        rrect(x + i * 6, y + 18 - h, 4, h, 1, i < lit ? on : off);
    }
}
static void lock_icon(int x, int y, SDL_Color c) {
    col(c);
    SDL_Rect a = {x + 3, y, 2, 6}, b = {x + 9, y, 2, 6}, t = {x + 3, y, 8, 2};
    SDL_RenderFillRect(R, &a); SDL_RenderFillRect(R, &b); SDL_RenderFillRect(R, &t);
    rrect(x, y + 5, 14, 10, 2, c);
}
static void check_icon(int cx, int cy, SDL_Color c) {
    col(c);
    for (int i = -1; i <= 1; i++) { SDL_RenderDrawLine(R, cx - 7, cy + i, cx - 2, cy + 5 + i); SDL_RenderDrawLine(R, cx - 2, cy + 5 + i, cx + 8, cy - 6 + i); }
}
static void spinner(int cx, int cy, int r, float t) {
    SDL_SetRenderDrawBlendMode(R, SDL_BLENDMODE_BLEND);
    for (int i = 0; i < 12; i++) {
        float a = i * (float)M_PI / 6, age = fmodf(t * 12.0f - i + 24.0f, 12.0f) / 12.0f;   /* 0 = head, 1 = tail */
        disc(cx + (int)lroundf(sinf(a) * r), cy - (int)lroundf(cosf(a) * r), 3, (SDL_Color)WISP_SDL_COLOR_A(WISP_UI_ACCENT, (Uint8)(255 - age * WISP_UI_SPIN_FADE)));
    }
}
static void hint(int x, int y, const char *key, const char *label, int *advance) {
    int kw = 0, kh = 0, tw = 0, th = 0;
    TTF_SizeUTF8(F_TINY, key, &kw, &kh); TTF_SizeUTF8(F_TINY, label, &tw, &th);
    int pill = kw + 12 < 20 ? 20 : kw + 12;
    rrect(x, y, pill, 20, 10, C_PANEL); text(F_TINY, key, x + pill / 2, y + 2, C_GLYPH, 1, 0);
    text(F_TINY, label, x + pill + 8, y + 2, C_DIM, 0, 0);
    *advance = pill + 8 + tw + 18;
}

/* ---- left column: badge, name, and the three steps ahead ---- */
static void left_column(int step) {
    int bh = 190, bw = BADGE_W * bh / BADGE_H;
    SDL_Rect d = {125 - bw / 2, 40, bw, bh};
    SDL_RenderCopy(R, BADGE, NULL, &d);
    text(F_TITLE, "WispOS", 125, 240, C_TEXT, 1, 0);
    text(F_TINY, "FIRST-TIME SETUP", 125, 276, C_DIM, 1, 0);
    if (IS_DEV) text(F_TINY, "DEV BUILD", 125, 458, C_BAD, 1, 0);
    const char *steps[3] = {"Connect to Wi-Fi", "Download Steam", "Sign in"};
    for (int i = 0; i < 3; i++) {
        int y = 330 + i * 30; int cur = i == step, done = i < step;
        disc(40, y + 10, 10, done ? C_OK : cur ? C_ACCENT : C_LINE);
        if (done) check_icon(40, y + 10, C_BG); else { char n[4]; snprintf(n, sizeof n, "%d", i + 1); text(F_TINY, n, 40, y + 2, cur ? C_BG : C_DIM, 1, 0); }
        text(F_SMALL, steps[i], 62, y - 1, cur ? C_TEXT : C_DIM, 0, 0);
    }
}

static void panel_title(const char *title, const char *sub) {
    text(F_TITLE, title, PANEL_X, 34, C_TEXT, 0, PANEL_W);
    if (sub && *sub) text(F_SMALL, sub, PANEL_X, 76, C_DIM, 0, PANEL_W);
}

/* ---- screens ---- */
static void screen_list(App *a) {
    left_column(0);
    panel_title("Connect to Wi-Fi", "Pick your network. Steam is downloaded over it.");
    int row_h = 46, pitch = 50, y0 = 108, rows = 6;
    for (int i = 0; i < rows && a->top + i < a->n; i++) {
        Net *n = &a->nets[a->top + i]; int y = y0 + i * pitch, sel = a->top + i == a->sel;
        SDL_Color fg = sel ? C_FOCUS_FG : C_TEXT, sub = sel ? (SDL_Color)WISP_SDL_COLOR(WISP_UI_FOCUS_DIM) : C_DIM;
        rrect(PANEL_X, y, PANEL_W, row_h, RAD, sel ? C_FOCUS : C_PANEL);
        signal_bars(PANEL_X + 16, y + 13, n->signal, fg, sel ? (SDL_Color)WISP_SDL_COLOR(WISP_UI_FOCUS_ICO) : C_LINE);
        text(F_BODY, n->ssid, PANEL_X + 52, y + 10, fg, 0, n->secured ? 300 : 340);
        if (n->active) text(F_TINY, "connected", PANEL_X + PANEL_W - 44, y + 15, sel ? (SDL_Color)WISP_SDL_COLOR(WISP_UI_FOCUS_OK) : C_OK, 2, 0);
        if (n->secured) lock_icon(PANEL_X + PANEL_W - 30, y + 15, sub);
    }
    if (a->n > rows) {  /* scroll indicator */
        int th = 5 * pitch + row_h, bar = th * rows / a->n, off = th * a->top / a->n;
        rrect(PANEL_X + PANEL_W + 8, y0, 4, th, 2, C_PANEL); rrect(PANEL_X + PANEL_W + 8, y0 + off, 4, bar, 2, C_DIM2);
    }
    /* scanning status */
    int dots = ((int)(a->t * 2)) % 4; char s[40] = "Scanning for networks"; for (int i = 0; i < dots; i++) strcat(s, ".");
    text(F_TINY, s, PANEL_X, 420, C_DIM, 0, 0);
    int x = 250, adv;
    hint(x, 452, "A", "Connect", &adv); x += adv;
    hint(x, 452, "X", "Rescan", &adv); x += adv;
    hint(x, 452, "Y", "Hidden network", &adv);
}

static void key(int x, int y, int w, int h, const char *label, int selected, SDL_Color base) {
    rrect(x, y, w, h, RAD, selected ? C_FOCUS : base);
    text(F_BODY, label, x + w / 2, y + (h - 24) / 2 - 1, selected ? C_FOCUS_FG : C_TEXT, 1, w - 6);
}

static void screen_password(App *a) {
    left_column(0);
    int naming = a->hidden && a->stage == 0;
    panel_title(naming ? "Hidden network" : "Enter password", NULL);
    /* network line, or the validation message */
    Net *n = a->n && !a->hidden ? &a->nets[a->sel] : NULL;
    if (a->msg[0]) text(F_SMALL, a->msg, PANEL_X, 78, C_BAD, 0, PANEL_W);
    else if (naming) text(F_SMALL, "Type the name of the network", PANEL_X, 78, C_DIM, 0, PANEL_W);
    else { signal_bars(PANEL_X, 80, n ? n->signal : 80, C_DIM, C_LINE); text(F_SMALL, a->ssid, PANEL_X + 34, 78, C_DIM, 0, PANEL_W - 60); lock_icon(PANEL_X + PANEL_W - 14, 80, C_DIM); }
    /* field */
    rrect(PANEL_X, 104, PANEL_W, 46, RAD, C_ACCENT); rrect(PANEL_X + 2, 106, PANEL_W - 4, 42, RAD, C_BLACK);
    char shown[100] = {0};
    if (naming) snprintf(shown, sizeof shown, "%s", a->ssid);
    else if (a->show) snprintf(shown, sizeof shown, "%s", a->pw);
    else for (int i = 0, n = (int)strlen(a->pw); i < n && i < 90; i++) strcat(shown, "\xE2\x80\xA2");
    text(F_BODY, shown, PANEL_X + 16, 116, C_TEXT, 0, PANEL_W - 70);
    int tw = 0, th = 0; TTF_SizeUTF8(F_BODY, shown, &tw, &th); if (tw > PANEL_W - 70) tw = PANEL_W - 70;
    if (((int)(a->t * 2)) % 2 == 0) { SDL_Rect c = {PANEL_X + 18 + tw, 117, 2, 24}; col(C_ACCENT); SDL_RenderFillRect(R, &c); }
    if (!naming) text(F_TINY, a->show ? "visible" : "hidden", PANEL_X + PANEL_W - 14, 121, C_DIM, 2, 0);
    /* keyboard: keys on a surface, as Steam's on-screen keyboard */
    const char *const *rows = kb_page(a->symbols, a->shift);
    int ky = 166, kw = kb_key_w(a->symbols), kh = KB_KEY_H, gap = KB_GAP;
    rrect(PANEL_X, 158, PANEL_W, KB_ROWS * (kh + gap) + kh + 24, RAD, C_PANEL);
    for (int r = 0; r < KB_ROWS; r++) {
        int len = (int)strlen(rows[r]); int total = kb_row_width(a->symbols, a->shift, r);
        int x = PANEL_X + (PANEL_W - total) / 2;
        for (int c = 0; c < len; c++) { char s2[2] = {rows[r][c], 0}; key(x + c * (kw + gap), ky + r * (kh + gap), kw, kh, s2, a->kr == r && a->kc == c, C_BG); }
        if (kb_row_has_del(a->symbols, r)) key(x + len * (kw + gap), ky + r * (kh + gap), KB_DEL_W, kh, "del", a->kr == r && a->kc == len, C_BLACK);
    }
    int sy = ky + KB_ROWS * (kh + gap), x = PANEL_X + 6;
    /* the second special key is "shift" on the letters pages and "del" on the symbols page (kb.h: ten symbols leave no room for del in their row) */
    struct { const char *l; int w; } sp[KB_SPECIALS] = {{a->symbols ? "abc" : "123", KB_SPECIAL_W[0]}, {a->symbols ? "del" : "shift", KB_SPECIAL_W[1]}, {"space", KB_SPECIAL_W[2]},
                                                        {a->show ? "hide" : "show", KB_SPECIAL_W[3]}, {naming ? "Next" : "Connect", KB_SPECIAL_W[4]}};
    for (int i = 0; i < KB_SPECIALS; i++) {
        SDL_Color base = i == 4 ? C_ACCENT : (i == KB_SPECIAL_SHIFT && a->shift && !a->symbols) || (i == 3 && a->show) ? C_ACCENT : C_BLACK;
        key(x, sy, sp[i].w, kh, sp[i].l, a->kr == 4 && a->kc == i, base);
        x += sp[i].w + gap;
    }
    int hx = 250, adv;
    hint(hx, 452, "A", "Type", &adv); hx += adv;
    hint(hx, 452, "B", "Delete", &adv); hx += adv;
    hint(hx, 452, "X", "Shift", &adv); hx += adv;
    hint(hx, 452, "Start", "Connect", &adv);
}

static void screen_progress(App *a, const char *title, const char *line1, int step_done) {
    left_column(0);
    panel_title(title, NULL);
    int y = 150;
    const char *steps[3] = {"Joined the network", "Got an address", "Reaching Steam"};
    for (int i = 0; i < 3; i++) {
        int done = i < step_done, cur = i == step_done;
        if (done) { disc(PANEL_X + 14, y + 14, 12, C_OK); check_icon(PANEL_X + 14, y + 14, C_BG); }
        else if (cur) spinner(PANEL_X + 14, y + 14, 10, a->t);
        else disc(PANEL_X + 14, y + 14, 6, C_LINE);
        text(F_BODY, steps[i], PANEL_X + 44, y + 2, done || cur ? C_TEXT : C_DIM2, 0, 0);
        y += 48;
    }
    text(F_SMALL, line1, PANEL_X, 330, C_DIM, 0, PANEL_W);
}

static void screen_failed(App *a, int step, const char *title, const char *why, const char *opt1, const char *opt2) {
    left_column(step);
    disc(PANEL_X + 22, 70, 22, C_BAD); text(F_TITLE, "!", PANEL_X + 22, 52, C_TEXT, 1, 0);
    text(F_TITLE, title, PANEL_X + 58, 52, C_TEXT, 0, PANEL_W - 60);
    text(F_SMALL, why, PANEL_X, 118, C_DIM, 0, PANEL_W);
    text(F_SMALL, a->msg, PANEL_X, 150, C_DIM, 0, PANEL_W);
    const char *o[2] = {opt1, opt2};
    for (int i = 0; i < 2; i++) {
        if (!o[i]) continue;
        int y = 214 + i * 62, sel = i == a->choice;
        rrect(PANEL_X, y, PANEL_W, 52, RAD, sel ? C_FOCUS : C_PANEL);
        text(F_BODY, o[i], PANEL_X + 22, y + 13, sel ? C_FOCUS_FG : C_TEXT, 0, PANEL_W - 40);
    }
    int adv; hint(250, 452, "A", "Select", &adv);
}

static void screen_download(App *a) {
    left_column(1);
    int unpack = !strcmp(a->phase, "extract") || !strcmp(a->phase, "finish");
    panel_title(!strcmp(a->phase, "extract") ? "Unpacking Steam" : !strcmp(a->phase, "finish") ? "Almost there" : "Downloading Steam",
                unpack ? "Nearly done. Please don't turn the device off." : "This happens once, and can take a few minutes.");
    int y = 170, bw = PANEL_W;
    rrect(PANEL_X, y, bw, 12, RAD, C_LINE);
    int fw = (int)(bw * a->progress);                       /* fill: Steam's focus gradient #47bfff -> #1a44c2 over 60% */
    for (int xx = 0; xx < fw; xx++) {
        float k = (float)xx / (0.6f * bw); if (k > 1) k = 1;
        SDL_SetRenderDrawColor(R, WISP_R(WISP_UI_BAR_FROM) + (WISP_R(WISP_UI_BAR_TO) - WISP_R(WISP_UI_BAR_FROM)) * k, WISP_G(WISP_UI_BAR_FROM) + (WISP_G(WISP_UI_BAR_TO) - WISP_G(WISP_UI_BAR_FROM)) * k, WISP_B(WISP_UI_BAR_FROM) + (WISP_B(WISP_UI_BAR_TO) - WISP_B(WISP_UI_BAR_FROM)) * k, 255);
        SDL_RenderDrawLine(R, PANEL_X + xx, y, PANEL_X + xx, y + 11);
    }
    char s[80]; snprintf(s, sizeof s, "%d%%", (int)(a->progress * 100)); text(F_BODY, s, PANEL_X, y + 26, C_TEXT, 0, 0);
    if (!unpack) { snprintf(s, sizeof s, "%ld of %ld MB", a->done_mb, a->total_mb); text(F_SMALL, s, PANEL_X + bw, y + 30, C_DIM, 2, 0); }
    else spinner(PANEL_X + bw - 14, y + 40, 10, a->t);
    text(F_SMALL, "Then you'll sign in to your Steam account.", PANEL_X, 300, C_DIM, 0, PANEL_W);
    check_icon(PANEL_X + 8, 372, C_OK); { char l[120]; snprintf(l, sizeof l, "Online via %s", a->ssid); text(F_SMALL, l, PANEL_X + 30, 362, C_DIM, 0, PANEL_W - 30); }
}

static void render(App *a) {
    SDL_SetRenderDrawBlendMode(R, SDL_BLENDMODE_BLEND);
    background();
    switch (a->st) {
        case ST_LIST: screen_list(a); break;
        case ST_PASSWORD: screen_password(a); break;
        case ST_CONNECTING: { char l[120]; snprintf(l, sizeof l, "Joining %s. This can take a few seconds.", a->ssid); screen_progress(a, "Connecting", l, 0); break; }
        case ST_CHECKING: screen_progress(a, "Checking the connection", "Making sure Steam can be reached from here.", 2); break;
        case ST_FAILED: screen_failed(a, 0, "Couldn't connect", "Check the password and the signal, then try again.", "Try again", "Choose another network"); break;
        case ST_NOINTERNET: screen_failed(a, 0, "No internet access", "The network works, but Steam can't be reached from it.", "Try again", "Choose another network"); break;
        case ST_DOWNLOAD: case ST_DONE: screen_download(a); break;
        case ST_DLFAILED: screen_failed(a, 1, "Couldn't download Steam", "The download stopped. Your progress is kept.", "Try again", "Choose another network"); break;
        case ST_NOADAPTER: screen_failed(a, 0, "No Wi-Fi adapter", "The device didn't report a Wi-Fi adapter.", "Check again", NULL); break;
    }
}

static void demo_data(App *a, const char *state) {
    static const struct { const char *s; int sig, sec, act; } d[] = {
        {"Home-WiFi-5G", 92, 1, 0}, {"TP-Link_A3F2", 74, 1, 0}, {"Guest network", 61, 0, 0}, {"Xfinity", 47, 1, 0},
        {"NETGEAR-2.4", 33, 1, 0}, {"AndroidAP_7731", 22, 1, 0}, {"BT-Hub6-7XQ2", 14, 1, 0}, {"Cafe Open WiFi", 9, 0, 0}};
    a->n = 8; for (int i = 0; i < a->n; i++) { snprintf(a->nets[i].ssid, 64, "%s", d[i].s); a->nets[i].signal = d[i].sig; a->nets[i].secured = d[i].sec; a->nets[i].active = d[i].act; }
    a->sel = 0; a->top = 0; a->t = 0.7f; snprintf(a->ssid, 64, "Home-WiFi-5G");
    a->progress = 0.38f; a->done_mb = 412; a->total_mb = 1080;
    if (!strcmp(state, "list")) { a->st = ST_LIST; a->sel = 1; }
    else if (!strcmp(state, "password")) { a->st = ST_PASSWORD; snprintf(a->pw, 96, "my-secret-2026"); a->kr = 2; a->kc = 4; }
    else if (!strcmp(state, "symbols")) { a->st = ST_PASSWORD; a->symbols = 1; snprintf(a->pw, 96, "p@ss!w0rd"); a->kr = 3; a->kc = 9; }       /* the symbols page: its last row has ten keys */
    else if (!strcmp(state, "connecting")) a->st = ST_CONNECTING;
    else if (!strcmp(state, "checking")) a->st = ST_CHECKING;
    else if (!strcmp(state, "failed")) { a->st = ST_FAILED; snprintf(a->msg, 160, "Reason: Wrong password."); }
    else if (!strcmp(state, "nointernet")) { a->st = ST_NOINTERNET; snprintf(a->msg, 160, "It may need a sign-in page, or be blocking Steam."); }
    else if (!strcmp(state, "download")) { a->st = ST_DOWNLOAD; snprintf(a->phase, sizeof a->phase, "download"); }
    else if (!strcmp(state, "extract")) { a->st = ST_DOWNLOAD; a->progress = 1; a->done_mb = a->total_mb; snprintf(a->phase, sizeof a->phase, "extract"); }
    else if (!strcmp(state, "dlfailed")) { a->st = ST_DLFAILED; snprintf(a->msg, 160, "Lost the connection to Steam's servers."); }
}

/* ================= the first-run hand-over ================= */
/* The boot screen's pixels and the setup screen's are mixed by handover.h's arithmetic, which is the contract's (os/splash/preview_handover.py). The setup screen is drawn into a render
 * target each frame and read back (a 720 x 480 copy, a few milliseconds) because the mix needs its pixels, not its draw calls. */
typedef struct { uint32_t boot[W * H], setup[W * H], out[W * H]; SDL_Texture *target, *show; Uint32 t0; int frame, active; const char *marker; } Handover;
static Handover *HO;

static void handover_compose(App *a, double t, int spin_frame, uint32_t *out_px) {
    SDL_SetRenderTarget(R, HO->target);
    render(a);
    SDL_Rect all = {0, 0, W, H};
    SDL_RenderReadPixels(R, &all, SDL_PIXELFORMAT_ARGB8888, HO->setup, W * 4);
    SDL_SetRenderTarget(R, NULL);
    bootframe_render(HO->boot, W, H, "Getting ready for setup", "This only happens the first time.", IS_DEV, spin_frame);
    ho_frame(out_px, HO->boot, HO->setup, W * H, t);
}

static int handover_init(const char *marker) {
    HO = calloc(1, sizeof *HO);
    if (!HO) return 0;
    HO->target = SDL_CreateTexture(R, SDL_PIXELFORMAT_ARGB8888, SDL_TEXTUREACCESS_TARGET, W, H);
    HO->show = SDL_CreateTexture(R, SDL_PIXELFORMAT_ARGB8888, SDL_TEXTUREACCESS_STREAMING, W, H);
    if (!HO->target || !HO->show) { free(HO); HO = NULL; return 0; }
    HO->marker = marker; HO->t0 = SDL_GetTicks(); HO->active = 1;
    return 1;
}

/* Draws this frame of the hand-over into the back buffer; returns 0 once it is over (and then nothing was drawn). */
static int handover_frame(App *a) {
    if (!HO || !HO->active) return 0;
    double t = (SDL_GetTicks() - HO->t0) / 1000.0 / HO_SECONDS;
    if (t >= 1.0) {
        HO->active = 0;
        FILE *f = HO->marker ? fopen(HO->marker, "w") : NULL;                  /* once only: the next first-run screen starts without it */
        if (f) { fputs("played\n", f); fclose(f); }
        return 0;
    }
    handover_compose(a, t, HO->frame++, HO->out);
    SDL_UpdateTexture(HO->show, NULL, HO->out, W * 4);
    SDL_RenderClear(R);
    SDL_RenderCopy(R, HO->show, NULL, NULL);
    return 1;
}

/* One frame of the hand-over as a picture, for the design check against the preview (--demo handover:T). */
static void handover_demo(App *a, double t, const char *path) {
    HO = calloc(1, sizeof *HO);
    HO->target = SDL_CreateTexture(R, SDL_PIXELFORMAT_ARGB8888, SDL_TEXTUREACCESS_TARGET, W, H);
    handover_compose(a, t, 0, HO->out);
    SDL_Surface *shot = SDL_CreateRGBSurfaceWithFormatFrom(HO->out, W, H, 32, W * 4, SDL_PIXELFORMAT_ARGB8888);
    SDL_SaveBMP(shot, path); SDL_FreeSurface(shot);
}

/* ================= the real program: backend threads, input, state machine ================= */
typedef enum { B_UP, B_DOWN, B_LEFT, B_RIGHT, B_A, B_B, B_X, B_Y, B_START, B_SELECT, B_NONE } Btn;
static SDL_mutex *LOCK;
static App *APP;
static const char *NMCLI = "nmcli", *CURL = "curl", *CHECK_URL = "https://client-update.fastly.steamstatic.com/steam_client_publicbeta_linuxarm64";
static const char *INSTALLER = "/usr/lib/wispos/steam-install", *READY_FILE = "/storage/wispos/runtime/READY";
static volatile int WANT_RESCAN = 1, QUIT_ALL = 0;
#define ROWS_VISIBLE 6

static void set_state(App *a, State s) {          /* caller holds LOCK */
    a->st = s; a->choice = 0; a->shot_pending = 1;
    printf("state=%s\n", STATE_NAMES[s]); fflush(stdout);
}

/* Steam must really be reachable: its manifest, not just any web page (a captive portal answers everything). */
static int internet_ok(void) {
    char path[64], code[64]; snprintf(path, sizeof path, "/tmp/wisp-check.%d", (int)getpid());
    char *argv[] = {(char *)CURL, "-sS", "-m", "12", "-o", path, "-w", "%{http_code}", (char *)CHECK_URL, NULL};
    int rc = nm_run(argv, code, sizeof code, 16), ok = 0;
    if (rc == 0) { FILE *f = fopen(path, "rb"); if (f) { char body[128] = {0}; size_t k = fread(body, 1, sizeof body - 1, f); body[k] = 0; fclose(f); ok = nm_steam_reply_ok(atoi(code), body); } }
    unlink(path);
    return ok;
}

static int have_wifi_adapter(void) {
    char out[1024]; char *argv[] = {(char *)NMCLI, "-t", "-f", "TYPE", "device", NULL};
    if (nm_run(argv, out, sizeof out, 10) != 0) return 0;
    return strstr(out, "wifi") != NULL;
}

static int scan_thread(void *p) {
    App *a = p;
    while (!QUIT_ALL) {
        int busy; SDL_LockMutex(LOCK); busy = a->st == ST_CONNECTING || a->st == ST_CHECKING || a->st == ST_DOWNLOAD; SDL_UnlockMutex(LOCK);
        if (!busy) {
            char out[8192]; char *argv[] = {(char *)NMCLI, "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", WANT_RESCAN ? "yes" : "auto", NULL};
            WANT_RESCAN = 0;
            if (nm_run(argv, out, sizeof out, 25) == 0) {
                Net tmp[32]; int n = nm_parse_list(out, tmp, 32);
                SDL_LockMutex(LOCK);
                char keep[64]; snprintf(keep, sizeof keep, "%s", a->n ? a->nets[a->sel].ssid : "");
                memcpy(a->nets, tmp, sizeof tmp); a->n = n; a->sel = 0;
                for (int i = 0; i < n; i++) if (!strcmp(a->nets[i].ssid, keep)) a->sel = i;     /* keep the cursor on the same network */
                if (a->sel < a->top) a->top = a->sel;
                if (a->sel >= a->top + ROWS_VISIBLE) a->top = a->sel - ROWS_VISIBLE + 1;
                SDL_UnlockMutex(LOCK);
            }
        }
        for (int i = 0; i < 40 && !QUIT_ALL && !WANT_RESCAN; i++) SDL_Delay(100);
    }
    return 0;
}

typedef struct { App *a; int job; char ssid[64], pw[96]; int hidden; } Job;

static void start_install(App *a);

static int check_thread(void *p) {
    Job *j = p; App *a = j->a; int ok = internet_ok();
    SDL_LockMutex(LOCK);
    if (j->job == a->job) {
        if (ok) start_install(a);
        else { snprintf(a->msg, sizeof a->msg, "It may need a sign-in page, or be blocking Steam."); set_state(a, ST_NOINTERNET); }
    }
    SDL_UnlockMutex(LOCK); free(j); return 0;
}

/* The Steam download: steam-install does the work (resume, hashes, unpacking, READY) and reports in lines; this only shows it.
 * The screen goes to "done" only after the installer said so, exited 0 and READY really exists. */
static void install_fail(App *a, int job, const char *why) {
    SDL_LockMutex(LOCK);
    if (job == a->job) { snprintf(a->msg, sizeof a->msg, "%s", why); a->installing = 0; set_state(a, ST_DLFAILED); }
    else a->installing = 0;
    SDL_UnlockMutex(LOCK);
}

static int install_thread(void *p) {
    Job *j = p; App *a = j->a; int job = j->job; free(j);
    char *argv[] = {(char *)INSTALLER, NULL}; pid_t pid; int fd = nm_spawn(argv, &pid);
    if (fd < 0) { install_fail(a, job, "Couldn't start the Steam installer."); return 0; }
    char buf[1024], err[160] = {0}; size_t used = 0; int saw_done = 0;
    for (;;) {
        fd_set rs; FD_ZERO(&rs); FD_SET(fd, &rs); struct timeval tv = {1, 0};
        if (QUIT_ALL) { kill(pid, SIGTERM); break; }
        int r = select(fd + 1, &rs, NULL, NULL, &tv);
        if (r <= 0) continue;
        ssize_t k = read(fd, buf + used, sizeof buf - 1 - used);
        if (k <= 0) break;
        used += (size_t)k; buf[used] = 0;
        char *line = buf, *nl;
        while ((nl = strchr(line, '\n'))) {
            *nl = 0; InstEvent ev;
            if (nm_parse_install_line(line, &ev) != INST_NONE) {
                SDL_LockMutex(LOCK);
                if (job == a->job) {
                    if (ev.kind == INST_PHASE) snprintf(a->phase, sizeof a->phase, "%s", ev.text);
                    else if (ev.kind == INST_PROGRESS) { a->progress = (float)((double)ev.done / (double)ev.total); a->done_mb = (long)(ev.done >> 20); a->total_mb = (long)(ev.total >> 20); }
                }
                SDL_UnlockMutex(LOCK);
                if (ev.kind == INST_ERROR) snprintf(err, sizeof err, "%s", ev.text);
                else if (ev.kind == INST_DONE) saw_done = 1;
            }
            line = nl + 1;
        }
        used = (size_t)(buf + used - line); memmove(buf, line, used);
        if (used >= sizeof buf - 1) used = 0;                              /* a line this long is not ours: drop it */
    }
    close(fd); int st = 0; waitpid(pid, &st, 0);
    int ok = saw_done && WIFEXITED(st) && WEXITSTATUS(st) == 0 && access(READY_FILE, F_OK) == 0;
    if (ok) {
        SDL_LockMutex(LOCK);
        if (job == a->job) { a->progress = 1; a->installing = 0; set_state(a, ST_DONE); } else a->installing = 0;
        SDL_UnlockMutex(LOCK);
    } else if (WIFEXITED(st) && WEXITSTATUS(st) == 127) install_fail(a, job, "The Steam installer is missing from this system.");
    else install_fail(a, job, err[0] ? err : "The installer stopped unexpectedly.");
    return 0;
}

static void start_install(App *a) {               /* caller holds LOCK */
    if (a->installing) return;
    a->installing = 1; a->progress = 0; a->done_mb = a->total_mb = 0; a->msg[0] = 0; snprintf(a->phase, sizeof a->phase, "download");
    Job *j = calloc(1, sizeof *j); j->a = a; j->job = ++a->job; set_state(a, ST_DOWNLOAD);
    SDL_DetachThread(SDL_CreateThread(install_thread, "install", j));
}

static void start_check(App *a) {                 /* caller holds LOCK */
    Job *j = calloc(1, sizeof *j); j->a = a; j->job = ++a->job; set_state(a, ST_CHECKING);
    SDL_DetachThread(SDL_CreateThread(check_thread, "check", j));
}

static int connect_thread(void *p) {
    Job *j = p; App *a = j->a; char out[1024]; char *argv[16]; int n = 0;
    argv[n++] = (char *)NMCLI; argv[n++] = "--wait"; argv[n++] = "45"; argv[n++] = "device"; argv[n++] = "wifi"; argv[n++] = "connect"; argv[n++] = j->ssid;
    if (j->pw[0]) { argv[n++] = "password"; argv[n++] = j->pw; }
    if (j->hidden) { argv[n++] = "hidden"; argv[n++] = "yes"; }
    argv[n] = NULL;
    int rc = nm_run(argv, out, sizeof out, 60);
    SDL_LockMutex(LOCK);
    if (j->job == a->job) {
        if (rc == 0) start_check(a);
        else {
            snprintf(a->msg, sizeof a->msg, "Reason: %s", nm_failure_reason(out)); set_state(a, ST_FAILED);
            char *del[] = {(char *)NMCLI, "connection", "delete", "id", j->ssid, NULL}; char junk[256]; nm_run(del, junk, sizeof junk, 10);   /* drop the bad profile */
        }
    }
    SDL_UnlockMutex(LOCK); free(j); return 0;
}

static void start_connect(App *a) {               /* caller holds LOCK */
    Job *j = calloc(1, sizeof *j); j->a = a; j->job = ++a->job;
    snprintf(j->ssid, sizeof j->ssid, "%s", a->ssid); snprintf(j->pw, sizeof j->pw, "%s", a->pw); j->hidden = a->hidden;
    a->msg[0] = 0; set_state(a, ST_CONNECTING);
    SDL_DetachThread(SDL_CreateThread(connect_thread, "connect", j));
}

/* ---- on-screen keyboard ---- */
static int row_len(App *a, int r) { return kb_row_len(a->symbols, a->shift, r); }
static void clamp_key(App *a) { int len = row_len(a, a->kr); if (a->kc >= len) a->kc = len - 1; }     /* after a page change: the new page's row can be shorter */
static char *typed(App *a) { return a->hidden && a->stage == 0 ? a->ssid : a->pw; }
static size_t typed_cap(App *a) { return a->hidden && a->stage == 0 ? sizeof a->ssid : sizeof a->pw; }
static void type_char(App *a, char c) { char *b = typed(a); size_t n = strlen(b); if (n + 1 < typed_cap(a) && n < (a->hidden && a->stage == 0 ? 32u : 63u)) { b[n] = c; b[n + 1] = 0; } a->msg[0] = 0; }
static void backspace(App *a) { char *b = typed(a); size_t n = strlen(b); if (n) b[n - 1] = 0; a->msg[0] = 0; }

static void submit(App *a) {
    if (a->hidden && a->stage == 0) {
        if (!a->ssid[0]) { snprintf(a->msg, sizeof a->msg, "Type the network name first."); return; }
        a->stage = 1; a->pw[0] = 0; a->kr = 1; a->kc = 0; a->msg[0] = 0; return;
    }
    if (a->pw[0] && strlen(a->pw) < 8) { snprintf(a->msg, sizeof a->msg, "A Wi-Fi password is at least 8 characters."); return; }
    start_connect(a);
}

static void open_password(App *a, int hidden) {
    a->hidden = hidden; a->stage = 0; a->pw[0] = 0; a->msg[0] = 0; a->shift = a->symbols = a->show = 0; a->kr = 1; a->kc = 0;
    if (hidden) a->ssid[0] = 0;
    set_state(a, ST_PASSWORD);
}

static void activate_key(App *a) {
    const char *const *rows = kb_page(a->symbols, a->shift);
    if (a->kr < KB_ROWS) {
        int len = (int)strlen(rows[a->kr]);
        if (a->kc < len) { type_char(a, rows[a->kr][a->kc]); if (a->shift) a->shift = 0; }
        else if (kb_row_has_del(a->symbols, a->kr)) backspace(a);       /* the "del" key ends row 3 of the letters pages */
        return;
    }
    switch (a->kc) {
        case 0: a->symbols = !a->symbols; clamp_key(a); break;
        case KB_SPECIAL_SHIFT: if (a->symbols) backspace(a); else a->shift = !a->shift; break;      /* "del" on the symbols page, "shift" on the letters pages */
        case 2: type_char(a, ' '); break;
        case 3: a->show = !a->show; break;
        case 4: submit(a); break;
    }
}

static void ensure_visible(App *a) {
    if (a->sel < a->top) a->top = a->sel;
    if (a->sel >= a->top + ROWS_VISIBLE) a->top = a->sel - ROWS_VISIBLE + 1;
}

/* One button press, in whichever state the screen is in. */
static void press(App *a, Btn b) {
    if (HO && HO->active) return;                                          /* nothing can be pressed while the screen is still arriving (the first-run hand-over) */
    SDL_LockMutex(LOCK);
    switch (a->st) {
    case ST_LIST:
        if (b == B_DOWN && a->sel + 1 < a->n) { a->sel++; ensure_visible(a); }
        else if (b == B_UP && a->sel > 0) { a->sel--; ensure_visible(a); }
        else if (b == B_X) WANT_RESCAN = 1;
        else if (b == B_Y) open_password(a, 1);
        else if (b == B_A && a->n) {
            Net *n = &a->nets[a->sel]; snprintf(a->ssid, sizeof a->ssid, "%s", n->ssid);
            if (n->secured) open_password(a, 0); else { a->hidden = 0; a->pw[0] = 0; start_connect(a); }
        }
        break;
    case ST_PASSWORD: {
        int len;
        if (b == B_LEFT) { len = row_len(a, a->kr); a->kc = (a->kc + len - 1) % len; }
        else if (b == B_RIGHT) { len = row_len(a, a->kr); a->kc = (a->kc + 1) % len; }
        else if (b == B_UP || b == B_DOWN) {
            int old = row_len(a, a->kr), nr = b == B_UP ? (a->kr + KB_ROWS) % (KB_ROWS + 1) : (a->kr + 1) % (KB_ROWS + 1), nl = row_len(a, nr);
            a->kc = a->kc * nl / old; if (a->kc >= nl) a->kc = nl - 1; a->kr = nr;
        }
        else if (b == B_A) activate_key(a);
        else if (b == B_B) { if (typed(a)[0]) backspace(a); else if (a->hidden && a->stage == 1) { a->stage = 0; } else set_state(a, ST_LIST); }
        else if (b == B_X) { if (!a->symbols) a->shift = !a->shift; }                       /* shift means nothing on the symbols page */
        else if (b == B_Y) { a->symbols = !a->symbols; clamp_key(a); }
        else if (b == B_START) submit(a);
        else if (b == B_SELECT) set_state(a, ST_LIST);
        break; }
    case ST_CONNECTING: case ST_CHECKING:
        if (b == B_B) { a->job++; set_state(a, ST_LIST); WANT_RESCAN = 1; }      /* cancel: a late result is ignored */
        break;
    case ST_FAILED: case ST_NOINTERNET: case ST_DLFAILED: case ST_NOADAPTER: {
        int opts = a->st == ST_NOADAPTER ? 1 : 2;
        if (b == B_DOWN && a->choice + 1 < opts) a->choice++;
        else if (b == B_UP && a->choice > 0) a->choice--;
        else if (b == B_A) {
            if (a->st == ST_NOADAPTER) { if (have_wifi_adapter()) set_state(a, ST_LIST); }
            else if (a->choice == 1) { set_state(a, ST_LIST); WANT_RESCAN = 1; }
            else if (a->st == ST_DLFAILED) start_install(a);
            else if (a->st == ST_FAILED) { a->pw[0] = 0; a->msg[0] = 0; a->stage = a->hidden ? 1 : 0; set_state(a, ST_PASSWORD); a->kr = 1; a->kc = 0; }
            else start_check(a);
        }
        break; }
    default: break;
    }
    SDL_UnlockMutex(LOCK);
}

/* ---- input sources: keyboard (for testing), game controller, plain joystick, and the test script ----
 * A pad SDL knows as a game controller also reports the same press as raw joystick events (a hat or a button). Only devices that were
 * NOT opened as controllers are read as plain joysticks, per device, or every press would count twice and a D-pad step would jump two rows. */
static Btn from_key(SDL_Keycode k) {
    switch (k) {
        case SDLK_UP: return B_UP; case SDLK_DOWN: return B_DOWN; case SDLK_LEFT: return B_LEFT; case SDLK_RIGHT: return B_RIGHT;
        case SDLK_RETURN: case SDLK_z: return B_A; case SDLK_BACKSPACE: case SDLK_x: return B_B;
        case SDLK_TAB: return B_X; case SDLK_h: return B_Y; case SDLK_s: return B_START; case SDLK_ESCAPE: return B_SELECT;
        default: return B_NONE;
    }
}
static Btn from_pad(Uint8 b) {
    switch (b) {
        case SDL_CONTROLLER_BUTTON_DPAD_UP: return B_UP; case SDL_CONTROLLER_BUTTON_DPAD_DOWN: return B_DOWN;
        case SDL_CONTROLLER_BUTTON_DPAD_LEFT: return B_LEFT; case SDL_CONTROLLER_BUTTON_DPAD_RIGHT: return B_RIGHT;
        case SDL_CONTROLLER_BUTTON_A: return B_A; case SDL_CONTROLLER_BUTTON_B: return B_B;
        case SDL_CONTROLLER_BUTTON_X: return B_X; case SDL_CONTROLLER_BUTTON_Y: return B_Y;
        case SDL_CONTROLLER_BUTTON_START: return B_START; case SDL_CONTROLLER_BUTTON_BACK: return B_SELECT;
        default: return B_NONE;
    }
}
static Btn from_name(const char *t) {
    static const struct { const char *n; Btn b; } m[] = {{"up", B_UP}, {"down", B_DOWN}, {"left", B_LEFT}, {"right", B_RIGHT}, {"a", B_A}, {"b", B_B},
        {"x", B_X}, {"y", B_Y}, {"start", B_START}, {"select", B_SELECT}};
    for (size_t i = 0; i < sizeof m / sizeof *m; i++) if (!strcmp(t, m[i].n)) return m[i].b;
    return B_NONE;
}
static int DEBUG_INPUT;                           /* WISP_DEBUG_INPUT=1: say which input device each press came from */
static void open_pads(void) {
    for (int i = 0; i < SDL_NumJoysticks(); i++) {
        if (DEBUG_INPUT) { printf("input: device %d \"%s\" controller=%d\n", i, SDL_JoystickNameForIndex(i), SDL_IsGameController(i)); fflush(stdout); }
        if (SDL_IsGameController(i)) SDL_GameControllerOpen(i); else SDL_JoystickOpen(i);
    }
}

/* Render the current screen into an offscreen W x H texture and save it. Reading the window itself would use the display's
 * size (the real program runs full-screen), not 720x480, and overrun the buffer. */
static void save_frame(App *a, const char *path) {
    static SDL_Texture *tex;
    if (!tex) tex = SDL_CreateTexture(R, SDL_PIXELFORMAT_ARGB8888, SDL_TEXTUREACCESS_TARGET, W, H);
    SDL_SetRenderTarget(R, tex);
    render(a);
    SDL_Surface *shot = SDL_CreateRGBSurfaceWithFormat(0, W, H, 32, SDL_PIXELFORMAT_ARGB8888);
    SDL_Rect all = {0, 0, W, H};
    SDL_RenderReadPixels(R, &all, SDL_PIXELFORMAT_ARGB8888, shot->pixels, shot->pitch);
    SDL_SaveBMP(shot, path); SDL_FreeSurface(shot);
    SDL_SetRenderTarget(R, NULL);
}
static void save_shot(App *a, const char *dir, int *seq, const char *name) {
    char p[300]; snprintf(p, sizeof p, "%s/%02d-%s.bmp", dir, (*seq)++, name); save_frame(a, p);
}

int main(int argc, char **argv) {
    const char *demo = NULL, *out = "frame.bmp", *fonts = "/usr/share/fonts/liberation", *badge = "/usr/share/wispos/wispos-badge.png", *script = NULL, *shots = NULL, *intro = NULL;
    int timeout_s = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--demo") && i + 1 < argc) demo = argv[++i];
        else if (!strcmp(argv[i], "--out") && i + 1 < argc) out = argv[++i];
        else if (!strcmp(argv[i], "--fonts") && i + 1 < argc) fonts = argv[++i];
        else if (!strcmp(argv[i], "--badge") && i + 1 < argc) badge = argv[++i];
        else if (!strcmp(argv[i], "--script") && i + 1 < argc) script = argv[++i];
        else if (!strcmp(argv[i], "--shots") && i + 1 < argc) shots = argv[++i];
        else if (!strcmp(argv[i], "--timeout") && i + 1 < argc) timeout_s = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--intro") && i + 1 < argc) intro = argv[++i];
    }
#ifdef WISP_DEBUG_CRASH
    signal(SIGSEGV, on_segv);
#endif
    DEBUG_INPUT = getenv("WISP_DEBUG_INPUT") != NULL;
    IS_DEV = access("/usr/lib/wispos/DEV-BUILD", F_OK) == 0 || getenv("WISP_DEV_LABEL");
    if (getenv("WISP_NMCLI")) NMCLI = getenv("WISP_NMCLI");
    if (getenv("WISP_CURL")) CURL = getenv("WISP_CURL");
    if (getenv("WISP_CHECK_URL")) CHECK_URL = getenv("WISP_CHECK_URL");
    if (getenv("WISP_INSTALLER")) INSTALLER = getenv("WISP_INSTALLER");
    if (getenv("WISP_READY")) READY_FILE = getenv("WISP_READY");
    if (!demo && access(READY_FILE, F_OK) == 0) { printf("already installed\n"); return 0; }          /* nothing to ask the person */
    int online = !demo && internet_ok();                                                          /* online already: no Wi-Fi screen, just the download */

    SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1");
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_GAMECONTROLLER | SDL_INIT_JOYSTICK) != 0 || TTF_Init() != 0) { fprintf(stderr, "init failed: %s\n", SDL_GetError()); return 1; }
    SDL_Window *win = demo ? SDL_CreateWindow("WispOS setup", 0, 0, W, H, 0) : SDL_CreateWindow("WispOS setup", 0, 0, W, H, SDL_WINDOW_FULLSCREEN_DESKTOP);
    R = SDL_CreateRenderer(win, -1, demo ? SDL_RENDERER_SOFTWARE : SDL_RENDERER_ACCELERATED | SDL_RENDERER_PRESENTVSYNC);
    if (!R) R = SDL_CreateRenderer(win, -1, SDL_RENDERER_SOFTWARE);
    SDL_RenderSetLogicalSize(R, W, H);
    char p[300];
    snprintf(p, sizeof p, "%s/LiberationSans-Bold.ttf", fonts); F_TITLE = TTF_OpenFont(p, 30);
    snprintf(p, sizeof p, "%s/LiberationSans-Regular.ttf", fonts); F_BODY = TTF_OpenFont(p, 23); F_SMALL = TTF_OpenFont(p, 18); F_TINY = TTF_OpenFont(p, 15);
    SDL_Surface *bs = IMG_Load(badge);
    if (!win || !R || !F_TITLE || !F_BODY || !bs) { fprintf(stderr, "setup failed: %s %s\n", SDL_GetError(), IMG_GetError()); return 1; }
    BADGE = SDL_CreateTextureFromSurface(R, bs); BADGE_W = bs->w; BADGE_H = bs->h; SDL_FreeSurface(bs);
    SDL_SetTextureBlendMode(BADGE, SDL_BLENDMODE_BLEND);
    static App a; memset(&a, 0, sizeof a);
    if (demo) {
        if (!strncmp(demo, "handover:", 9)) { demo_data(&a, "list"); handover_demo(&a, atof(demo + 9), out); return 0; }
        demo_data(&a, demo); save_frame(&a, out);
        return 0;
    }
    LOCK = SDL_CreateMutex(); APP = &a; SDL_ShowCursor(SDL_DISABLE); open_pads();
    char *radio[] = {(char *)NMCLI, "radio", "wifi", "on", NULL}; char junk[256]; nm_run(radio, junk, sizeof junk, 10);
    SDL_LockMutex(LOCK);
    if (online) start_install(&a); else set_state(&a, have_wifi_adapter() ? ST_LIST : ST_NOADAPTER);
    SDL_UnlockMutex(LOCK);
    SDL_Thread *scanner = SDL_CreateThread(scan_thread, "scan", &a);
    if (intro && access(intro, F_OK) != 0) {                                      /* first run: tell the boot screen what to show as its last words, then play */
        int bf = open("/run/wisp-bootscreen.fifo", O_WRONLY | O_NONBLOCK | O_CLOEXEC);      /* never blocks: with no boot screen (no reader) it simply fails */
        if (bf >= 0) { static const char say[] = "status Getting ready for setup\ndetail This only happens the first time.\n"; (void)!write(bf, say, sizeof say - 1); close(bf); SDL_Delay(200); }
        if (!handover_init(intro)) fprintf(stderr, "wisp-setup: no hand-over this time\n");
    }

    char script_buf[2048] = {0}; char *tok = NULL, *save = NULL; Uint32 next_script = 0;
    if (script) { snprintf(script_buf, sizeof script_buf, "%s", script); tok = strtok_r(script_buf, " ", &save); }
    Uint32 start = SDL_GetTicks(); int seq = 0, exit_code = 0; Btn held = B_NONE; Uint32 repeat_at = 0;
    for (;;) {
        SDL_Event e;
        while (SDL_PollEvent(&e)) {
            if (e.type == SDL_QUIT) continue;                              /* a setup screen is not closable */
            Btn b = B_NONE;
            if (DEBUG_INPUT && (e.type == SDL_CONTROLLERBUTTONDOWN || e.type == SDL_JOYHATMOTION || e.type == SDL_JOYBUTTONDOWN)) {
                printf("input: %s device-instance=%d\n", e.type == SDL_CONTROLLERBUTTONDOWN ? "controller-button" : e.type == SDL_JOYHATMOTION ? "joystick-hat" : "joystick-button",
                       e.type == SDL_CONTROLLERBUTTONDOWN ? (int)e.cbutton.which : e.type == SDL_JOYHATMOTION ? (int)e.jhat.which : (int)e.jbutton.which); fflush(stdout);
            }
            if (e.type == SDL_KEYDOWN) b = from_key(e.key.keysym.sym);
            else if (e.type == SDL_CONTROLLERBUTTONDOWN) { b = from_pad(e.cbutton.button); held = b; repeat_at = SDL_GetTicks() + 400; }
            else if (e.type == SDL_CONTROLLERBUTTONUP) held = B_NONE;
            else if (e.type == SDL_CONTROLLERDEVICEADDED) SDL_GameControllerOpen(e.cdevice.which);
            else if (e.type == SDL_JOYDEVICEADDED && !SDL_IsGameController(e.jdevice.which)) SDL_JoystickOpen(e.jdevice.which);
            else if (e.type == SDL_JOYHATMOTION && !SDL_GameControllerFromInstanceID(e.jhat.which)) b = e.jhat.value & SDL_HAT_UP ? B_UP : e.jhat.value & SDL_HAT_DOWN ? B_DOWN : e.jhat.value & SDL_HAT_LEFT ? B_LEFT : e.jhat.value & SDL_HAT_RIGHT ? B_RIGHT : B_NONE;
            else if (e.type == SDL_JOYBUTTONDOWN && !SDL_GameControllerFromInstanceID(e.jbutton.which)) b = e.jbutton.button == 0 ? B_A : e.jbutton.button == 1 ? B_B : e.jbutton.button == 2 ? B_X : e.jbutton.button == 3 ? B_Y : e.jbutton.button == 7 ? B_START : e.jbutton.button == 6 ? B_SELECT : B_NONE;
            if (b != B_NONE) press(&a, b);
        }
        if (held != B_NONE && SDL_GetTicks() >= repeat_at && held <= B_RIGHT) { press(&a, held); repeat_at = SDL_GetTicks() + 90; }
        while (tok && SDL_GetTicks() >= next_script) {                     /* the test script */
            if (!strncmp(tok, "wait:", 5)) next_script = SDL_GetTicks() + (Uint32)atoi(tok + 5);
            else if (!strcmp(tok, "until:networks")) { SDL_LockMutex(LOCK); int ok = a.n > 0; SDL_UnlockMutex(LOCK); if (!ok) break; }
            else if (!strncmp(tok, "until:", 6)) { SDL_LockMutex(LOCK); int ok = !strcmp(STATE_NAMES[a.st], tok + 6); SDL_UnlockMutex(LOCK); if (!ok) break; }
            else if (!strncmp(tok, "type:", 5)) { SDL_LockMutex(LOCK); for (const char *c = tok + 5; *c; c++) type_char(&a, *c == '_' ? ' ' : *c); SDL_UnlockMutex(LOCK); }
            else if (!strcmp(tok, "dump")) { SDL_LockMutex(LOCK); printf("typed pw=[%s] ssid=[%s] shift=%d symbols=%d\n", a.pw, a.ssid, a.shift, a.symbols); fflush(stdout); SDL_UnlockMutex(LOCK); }
            else if (!strcmp(tok, "quit")) goto done;
            else if (from_name(tok) != B_NONE) press(&a, from_name(tok));
            else { fprintf(stderr, "bad script token '%s'\n", tok); exit_code = 4; goto done; }
            tok = strtok_r(NULL, " ", &save);
        }
        SDL_LockMutex(LOCK); a.t = (SDL_GetTicks() - start) / 1000.0f; State st = a.st; int shot = a.shot_pending; a.shot_pending = 0;
        App snap = a; SDL_UnlockMutex(LOCK);                               /* draw from a copy: no lock held while SDL works */
        if (!handover_frame(&snap)) render(&snap);
        if (shots && shot) save_shot(&snap, shots, &seq, STATE_NAMES[st]);
        SDL_RenderPresent(R);
        if (st == ST_DONE) { SDL_Delay(shots ? 50 : 1500); break; }          /* online and Steam reachable: hand over */
        if (timeout_s && (SDL_GetTicks() - start) / 1000 >= (Uint32)timeout_s) { fprintf(stderr, "timeout in state %s\n", STATE_NAMES[st]); exit_code = 3; break; }
        SDL_Delay(33);
    }
done:
    QUIT_ALL = 1; SDL_WaitThread(scanner, NULL);
    return exit_code;
}
