/* Tests of the pad's logic (pad-logic.c) with scripted streams: buttons, sticks and time go in, the outputs are written out as short words and compared.
 *   k<code>+ / k<code>-   key down / up        b<code>+ / b<code>-   mouse button down / up        r<dx>,<dy>   pointer        w<dx>,<dy>/<nx>,<ny>   wheel (hi-res / notches)
 *   c:<name>              a command for the shell
 * Run by os/test_pad.py (natively under the address and undefined-behaviour sanitizers, and against rule mutants of pad-logic.c, every one of which must make this fail). */
#include "pad-logic.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int checks, failed;
static Pad P;
static PadOuts O;
static int64_t T;

#define V(f) ((int)((f) * 32767.0 + ((f) > 0 ? 0.5 : -0.5)))

static void check_str(const char *what, const char *got, const char *want)
{
    checks++;
    if (strcmp(got, want) != 0) { failed++; printf("FAIL %s\n  got:  [%s]\n  want: [%s]\n", what, got, want); }
}

static void check_int(const char *what, long got, long want)
{
    checks++;
    if (got != want) { failed++; printf("FAIL %s: got %ld, want %ld\n", what, got, want); }
}

static void check_true(const char *what, int cond)
{
    checks++;
    if (!cond) { failed++; printf("FAIL %s\n", what); }
}

/* the outputs since the last call, as words */
static const char *take(void)
{
    static char buf[2048];
    buf[0] = 0;
    size_t n = 0;
    for (int i = 0; i < O.n; i++) {
        const PadOut *v = &O.v[i];
        char w[64];
        pad_out_text(v, w, sizeof w);
        n += (size_t)snprintf(buf + n, sizeof buf - n, "%s%s", n ? " " : "", w);
    }
    O.n = 0;
    return buf;
}

static void reset(PadLayer l)
{
    PadConfig c;
    pad_config_default(&c);
    pad_init(&P, &c);
    pad_set_layer(&P, l);
    O.n = 0;
    T = 100000;
}

static void btn(int code, bool down) { pad_button(&P, T, code, down, &O); }
static void ax(int axis, double f) { pad_axis(&P, T, axis, V(f), &O); }
static int tick_at(int64_t t) { T = t; return pad_tick(&P, T, &O); }

#define EXPECT(what, want) check_str(what, take(), want)

/* ------------------------------------------------------------------ the locked layout, desktop layer */
static void t_buttons_desktop(void)
{
    reset(PL_DESKTOP);
    btn(PAD_A, true);        EXPECT("A down is Enter", "k28+");
    btn(PAD_A, false);       EXPECT("A up", "k28-");
    btn(PAD_B, true);        EXPECT("B down is Escape", "k1+");
    btn(PAD_B, false);       EXPECT("B up", "k1-");
    btn(PAD_Y, true);        EXPECT("Y down is Space", "k57+");
    btn(PAD_Y, false);       EXPECT("Y up", "k57-");
    btn(PAD_R1, true);       EXPECT("R1 is the left click", "b272+");
    btn(PAD_R1, false);      EXPECT("R1 up", "b272-");
    btn(PAD_L1, true);       EXPECT("L1 is the right click", "b273+");
    btn(PAD_L1, false);      EXPECT("L1 up", "b273-");
    btn(PAD_R2, true);       EXPECT("R2 is Tab", "k15+");
    btn(PAD_R2, false);      EXPECT("R2 up", "k15-");
    btn(PAD_L2, true);       EXPECT("L2 is Shift+Tab: Shift first", "k42+ k15+");
    btn(PAD_L2, false);      EXPECT("L2 up: Tab first", "k15- k42-");
    btn(PAD_X, true);        EXPECT("X shows the keyboard", "c:osk");
    btn(PAD_X, false);       EXPECT("X up does nothing", "");
    btn(PAD_START, true);    EXPECT("Start is the application menu", "c:menu");
    btn(PAD_START, false);
    btn(PAD_MODE, true);     EXPECT("Mode summons the dock", "c:summon");
    btn(PAD_MODE, false);
    btn(PAD_SELECT, true);   EXPECT("Select is free in the desktop layer", "");
    btn(PAD_SELECT, false);  EXPECT("Select up", "");
    btn(PAD_A, true); btn(PAD_A, true);
    EXPECT("a second down without an up is ignored", "k28+");
    btn(PAD_A, false); btn(PAD_A, false);
    EXPECT("a second up without a down is ignored", "k28-");
    btn(999, true); btn(-1, true); btn(PAD_CODES, true); btn(PAD_LX, true);
    EXPECT("unknown buttons do nothing", "");
}

static void t_dpad_desktop(void)
{
    reset(PL_DESKTOP);
    btn(PAD_DUP, true);      EXPECT("D-pad up is the arrow, held", "k103+");
    btn(PAD_DRIGHT, true);   EXPECT("two directions at once", "k106+");
    btn(PAD_DUP, false);     EXPECT("up released alone", "k103-");
    btn(PAD_DRIGHT, false);  EXPECT("right released", "k106-");
    btn(PAD_DDOWN, true);    EXPECT("down", "k108+");
    btn(PAD_DDOWN, false);   EXPECT("down up", "k108-");
    btn(PAD_DLEFT, true);    EXPECT("left", "k105+");
    btn(PAD_DLEFT, false);   EXPECT("left up", "k105-");
    check_int("a held D-pad needs no tick", tick_at(T + 5000), -1);
    EXPECT("and the daemon does not repeat it itself (the compositor does)", "");
}

static void t_scroll_toggle_and_slow(void)
{
    reset(PL_DESKTOP);
    btn(PAD_R3, true);       EXPECT("R3 scrolls", "c:scroll-on");
    btn(PAD_R3, false);      EXPECT("R3 up does nothing", "");
    btn(PAD_R3, true);       EXPECT("R3 again points", "c:scroll-off");
    btn(PAD_R3, false);
    btn(PAD_L3, true);       EXPECT("L3 is silent", "");
    btn(PAD_L3, false);
}

/* ------------------------------------------------------------------ the left stick as arrow keys */
static void t_stick_thresholds(void)
{
    reset(PL_DESKTOP);
    ax(PAD_LX, -0.54);       EXPECT("below the press level nothing happens", "");
    ax(PAD_LX, -0.30);       ax(PAD_LY, 0.30);     ax(PAD_LX, 0.30);
    for (int i = 0; i < 1000; i++) { ax(PAD_LX, (i % 2) ? -0.34 : 0.34); ax(PAD_LY, (i % 3) ? 0.33 : -0.33); }
    EXPECT("drift types nothing", "");
    check_int("and needs no tick", tick_at(T + 10), -1);
    ax(PAD_LX, 0); ax(PAD_LY, 0);
    ax(PAD_LX, -0.56);       EXPECT("past the press level: one tap of Left", "k105+ k105-");
    ax(PAD_LX, -0.40);       EXPECT("between the levels the direction is held, no new tap", "");
    ax(PAD_LX, -0.99);       EXPECT("harder does not tap again", "");
    ax(PAD_LX, -0.36);       EXPECT("just above the release level", "");
    ax(PAD_LX, -0.34);       EXPECT("below the release level it lets go", "");
    ax(PAD_LX, -0.50);       EXPECT("and 50 % is below the press level for a new one", "");
    ax(PAD_LX, -0.60);       EXPECT("a new press taps again", "k105+ k105-");
    for (int i = 0; i < 100; i++) { ax(PAD_LX, (i % 2) ? -0.56 : -0.54); }
    EXPECT("jitter around the press level while held taps nothing more", "");
}

static void t_stick_directions(void)
{
    reset(PL_DESKTOP);
    ax(PAD_LX, 0.9);         EXPECT("right", "k106+ k106-");
    ax(PAD_LX, 0);
    ax(PAD_LY, 0.9);         EXPECT("down", "k108+ k108-");
    ax(PAD_LY, 0);
    ax(PAD_LY, -0.9);        EXPECT("up", "k103+ k103-");
    ax(PAD_LY, 0);
    ax(PAD_LX, -0.7); /* the tap fires on the first axis event past the level */
    take();
    ax(PAD_LX, 0);
    ax(PAD_LX, -0.5); ax(PAD_LY, -0.8);
    EXPECT("a diagonal goes to the dominant axis (up)", "k103+ k103-");
    ax(PAD_LX, 0); ax(PAD_LY, 0);
    ax(PAD_LY, -0.5); ax(PAD_LX, -0.7);
    EXPECT("a diagonal goes to the dominant axis (left)", "k105+ k105-");
    ax(PAD_LX, 0); ax(PAD_LY, 0);
    ax(PAD_LX, -0.6);        take();
    ax(PAD_LX, -0.4); ax(PAD_LY, -0.7);
    EXPECT("swerving from left to up past the press level switches", "k103+ k103-");
    ax(PAD_LY, 0); ax(PAD_LX, 0);
    ax(PAD_LX, -0.9);        take();
    ax(PAD_LX, -0.6);        EXPECT("held, getting lighter", "");
    ax(PAD_LY, -0.9);
    EXPECT("with both past the press level the stronger axis wins", "k103+ k103-");
    ax(PAD_LY, 0); ax(PAD_LX, 0);
    ax(PAD_LX, -0.6);        take();
    ax(PAD_LX, 0.6);
    EXPECT("straight across the middle to the other side", "k106+ k106-");
    ax(PAD_LX, 0);
    ax(PAD_LX, -0.9);        take();
    ax(PAD_LY, -0.5);
    EXPECT("a held direction is kept while the other axis is below the press level", "");
}

static void t_stick_repeat(void)
{
    reset(PL_DESKTOP);
    ax(PAD_LX, -0.9);        EXPECT("first tap at once", "k105+ k105-");
    int64_t t0 = T;
    check_int("the first repeat is wanted after 350 ms", tick_at(t0), 350);
    EXPECT("nothing yet", "");
    check_int("1 ms before", tick_at(t0 + 349), 1);
    EXPECT("nothing at 349 ms", "");
    int wake = tick_at(t0 + 350);
    EXPECT("the first repeat at 350 ms", "k105+ k105-");
    check_int("then at 8 a second", wake, 125);
    int64_t t = t0 + 350, prev = t;
    int n = 1, last_gap = 0, min_gap = 1000, mono = 1;
    while (t < t0 + 6000) {
        t += wake;
        wake = tick_at(t);
        if (strcmp(take(), "k105+ k105-") == 0) {
            int gap = (int)(t - prev);
            if (n > 1 && gap > last_gap) mono = 0;
            if (gap < min_gap) min_gap = gap;
            last_gap = gap;
            prev = t;
            n++;
        }
    }
    check_true("the repeat only ever speeds up", mono);
    check_int("it ends at 14 a second (71 ms)", last_gap, 71);
    check_int("and never faster", min_gap, 71);
    check_true("about 8-14 a second over six seconds", n > 5.65 * 8 && n < 5.65 * 14);
    ax(PAD_LX, 0);
    check_int("released: no repeat is wanted", tick_at(T + 1000), -1);
    EXPECT("and none comes", "");
    ax(PAD_LX, -0.9);        EXPECT("pressing again starts over with a tap", "k105+ k105-");
    ax(PAD_LX, -0.9);        EXPECT("pressing again starts over (second time)", "");
    ax(PAD_LX, -0.3);        ax(PAD_LY, -0.9);        EXPECT("a change of direction is a tap in the new one", "k103+ k103-");
}

/* ------------------------------------------------------------------ the right stick */
static int sum_run(int ms, int *sx, int *sy)
{
    int64_t t0 = T;
    int ticks = 0, wake = tick_at(T);
    *sx = *sy = 0;
    take();
    while (T < t0 + ms) {
        if (wake < 0) break;
        wake = tick_at(T + wake);
        for (int i = 0; i < O.n; i++) if (O.v[i].kind == PO_REL) { *sx += O.v[i].dx; *sy += O.v[i].dy; }
        O.n = 0;
        ticks++;
    }
    return ticks;
}

static void t_pointer(void)
{
    int sx, sy;
    reset(PL_DESKTOP);
    ax(PAD_RX, 0.11);        check_int("inside the dead zone no tick is wanted", tick_at(T), -1);
    EXPECT("and nothing moves", "");
    ax(PAD_RX, 1.0);
    check_int("out of it, a tick every 8 ms", tick_at(T), 8);
    sum_run(1000, &sx, &sy);
    check_true("full deflection is about 700 pixels a second", sx >= 695 && sx <= 705 && sy == 0);
    reset(PL_DESKTOP);
    ax(PAD_RX, 0.5);
    tick_at(T); sum_run(1000, &sx, &sy);
    check_true("half deflection is much slower than half speed (the curve)", sx > 100 && sx < 160);
    reset(PL_DESKTOP);
    ax(PAD_RX, 0.7); ax(PAD_RY, 0.7);
    tick_at(T); sum_run(1000, &sx, &sy);
    check_true("a diagonal moves equally in both", abs(sx - sy) <= 2 && sx > 400);
    reset(PL_DESKTOP);
    ax(PAD_RX, -1.0); ax(PAD_RY, -1.0);
    tick_at(T); sum_run(1000, &sx, &sy);
    check_true("up and left are negative, and a corner is not faster than full", sx < 0 && sy < 0 && abs(sx) <= 705);
    reset(PL_DESKTOP);
    btn(PAD_L3, true); ax(PAD_RX, 1.0);
    tick_at(T); sum_run(1000, &sx, &sy);
    check_true("L3 held is slow (a quarter)", sx >= 170 && sx <= 180);
    btn(PAD_L3, false);
    sum_run(1000, &sx, &sy);
    check_true("and normal again when released", sx >= 695 && sx <= 705);
    reset(PL_DESKTOP);
    ax(PAD_RX, 0.2);
    tick_at(T); sum_run(2000, &sx, &sy);
    check_true("fractions of a pixel are carried, not lost (0.2 is about 5 pixels a second)", sx >= 8 && sx <= 12);
    reset(PL_DESKTOP);
    ax(PAD_RX, 1.0);
    tick_at(T);
    tick_at(T + 10000);
    int moved = 0;
    for (int i = 0; i < O.n; i++) if (O.v[i].kind == PO_REL) moved += O.v[i].dx;
    O.n = 0;
    check_true("after a stall one step is at most 50 ms worth", moved <= 36);
    ax(PAD_RX, 0);
    check_int("back in the middle the ticks stop", tick_at(T + 8), -1);
    ax(PAD_RX, 1.0);
    tick_at(T + 20);
    tick_at(T + 8);
    int again = 0;
    for (int i = 0; i < O.n; i++) if (O.v[i].kind == PO_REL) again += O.v[i].dx;
    O.n = 0;
    check_true("and starting again begins gently, not with the stall's time", again >= 4 && again <= 6);
}

static void t_scroll_mode(void)
{
    reset(PL_DESKTOP);
    btn(PAD_R3, true); btn(PAD_R3, false); take();
    ax(PAD_RY, -1.0);
    tick_at(T);
    int64_t t0 = T;
    long hi = 0, notches = 0, rels = 0;
    int wake = 8;
    while (T < t0 + 1000) {
        wake = tick_at(T + wake);
        for (int i = 0; i < O.n; i++) {
            if (O.v[i].kind == PO_WHEEL) { hi += O.v[i].dy; notches += O.v[i].notch_y; check_int("vertical only", O.v[i].dx, 0); }
            if (O.v[i].kind == PO_REL) rels++;
        }
        O.n = 0;
    }
    check_true("stick up scrolls up (positive), about 10 notches a second", hi >= 1180 && hi <= 1220);
    check_true("the whole notches add up to the hi-res total", labs(notches - hi / 120) <= 1);
    check_int("and the pointer does not move", rels, 0);
    ax(PAD_RY, 1.0);
    tick_at(T);
    hi = 0;
    for (int i = 0; i < 25; i++) {
        tick_at(T + 8);
        for (int j = 0; j < O.n; j++) if (O.v[j].kind == PO_WHEEL) hi += O.v[j].dy;
        O.n = 0;
    }
    check_true("stick down scrolls down (negative)", hi < -150);
    ax(PAD_RY, 0.3); ax(PAD_RX, 0.9);
    tick_at(T + 8); take();
    int ok = 0;
    for (int i = 0; i < 20; i++) {
        tick_at(T + 8);
        for (int j = 0; j < O.n; j++) if (O.v[j].kind == PO_WHEEL && O.v[j].dx > 0) ok = 1;
        O.n = 0;
    }
    check_true("sideways scrolls sideways", ok);
}

/* ------------------------------------------------------------------ the browser layer */
static void t_browser(void)
{
    reset(PL_BROWSER);
    ax(PAD_LX, -0.9);
    EXPECT("the left stick sends no arrow keys here", "");
    ax(PAD_LX, 0); ax(PAD_LY, 0.8);
    int64_t t0 = T;
    tick_at(T);
    long hi = 0;
    int wake = 8;
    while (T < t0 + 500) {
        wake = tick_at(T + wake);
        for (int i = 0; i < O.n; i++) { check_true("only wheel events", O.v[i].kind == PO_WHEEL); hi += O.v[i].dy; }
        O.n = 0;
    }
    check_true("the left stick scrolls down, proportionally", hi < -400 && hi > -700);
    ax(PAD_LY, 0);
    btn(PAD_DUP, true);      EXPECT("the D-pad is still the arrows", "k103+");
    btn(PAD_DUP, false);     EXPECT("D-pad up", "k103-");
    btn(PAD_Y, true);        EXPECT("Y is Forward", "b276+");
    btn(PAD_Y, false);       EXPECT("Y up", "b276-");
    btn(PAD_SELECT, true);   EXPECT("Select opens the actions list", "c:actions");
    btn(PAD_SELECT, false);
    btn(PAD_R1, true);       EXPECT("the clicks are unchanged", "b272+");
    btn(PAD_R1, false);      take();

    T = 200000;
    btn(PAD_B, true);
    EXPECT("B down does nothing yet", "");
    check_int("the hold time is waited for", tick_at(T), 450);
    T += 100;
    btn(PAD_B, false);       EXPECT("a short press is Back, at the release", "b275+ b275-");
    check_int("nothing is pending afterwards", tick_at(T), -1);

    btn(PAD_B, true);
    check_int("again", tick_at(T), 450);
    check_int("still waiting", tick_at(T + 449), 1);
    EXPECT("nothing before the time", "");
    check_int("at the hold time", tick_at(T + 1), -1);
    EXPECT("held is Escape", "k1+ k1-");
    T += 1000;
    btn(PAD_B, false);       EXPECT("and the release then does nothing", "");
    check_int("and nothing is pending", tick_at(T + 1000), -1);
}

/* ------------------------------------------------------------------ the on-screen keyboard layer */
static void t_osk(void)
{
    reset(PL_OSK);
    btn(PAD_A, true);        EXPECT("A types", "c:osk-type");
    btn(PAD_A, false);       EXPECT("A up", "");
    btn(PAD_B, true);        EXPECT("B deletes", "c:osk-del");
    btn(PAD_B, false);
    btn(PAD_X, true);        EXPECT("X is shift", "c:osk-shift");
    btn(PAD_X, false);
    btn(PAD_Y, true);        EXPECT("Y is space", "c:osk-space");
    btn(PAD_Y, false);
    btn(PAD_START, true);    EXPECT("Start is enter", "c:osk-enter");
    btn(PAD_START, false);
    btn(PAD_SELECT, true);   EXPECT("Select hides it", "c:osk-hide");
    btn(PAD_SELECT, false);
    btn(PAD_MODE, true);     EXPECT("Mode still summons the dock", "c:summon");
    btn(PAD_MODE, false);
    btn(PAD_R1, true);       EXPECT("the clicks work on the keyboard", "b272+");
    btn(PAD_R1, false);      EXPECT("click up", "b272-");
    btn(PAD_R2, true);       EXPECT("Tab still works", "k15+");
    btn(PAD_R2, false);      take();

    T = 300000;
    int64_t b = T;
    btn(PAD_DUP, true);      EXPECT("the D-pad moves the key focus", "c:nav-up");
    check_int("it repeats after 350 ms", tick_at(b), 350);
    EXPECT("", "");
    check_int("and then at a steady 8 a second (no ramp)", tick_at(b + 350), 125);
    EXPECT("repeat", "c:nav-up");
    check_int("again", tick_at(b + 475), 125);
    EXPECT("repeat 2", "c:nav-up");
    check_int("again", tick_at(b + 600), 125);
    EXPECT("repeat 3", "c:nav-up");
    btn(PAD_DUP, false);
    check_int("released: quiet", tick_at(b + 725), -1);
    EXPECT("no more", "");
    btn(PAD_DLEFT, true); btn(PAD_DLEFT, false);
    EXPECT("left", "c:nav-left");
    btn(PAD_DRIGHT, true); btn(PAD_DRIGHT, false); EXPECT("right", "c:nav-right");
    btn(PAD_DDOWN, true);  btn(PAD_DDOWN, false);  EXPECT("down", "c:nav-down");

    T = 400000;
    ax(PAD_LX, 0.9);         EXPECT("the left stick moves it too", "c:nav-right");
    check_int("with the ramp", tick_at(T), 350);
    ax(PAD_LX, 0);
    T = 402000;
    ax(PAD_RX, 1.0);
    check_int("the pointer keeps working", tick_at(T), 8);
    tick_at(T + 8);
    int moved = 0;
    for (int i = 0; i < O.n; i++) if (O.v[i].kind == PO_REL) moved += O.v[i].dx;
    O.n = 0;
    check_true("it moved", moved > 0);
}

/* ------------------------------------------------------------------ layer changes while buttons are held */
static void t_layer_changes(void)
{
    reset(PL_DESKTOP);
    btn(PAD_A, true);                       EXPECT("A in the desktop layer", "k28+");
    pad_set_layer(&P, PL_OSK);
    btn(PAD_A, false);                      EXPECT("released in another layer: still the Enter that was pressed", "k28-");
    btn(PAD_A, true);                       EXPECT("a new press is the new layer's", "c:osk-type");
    pad_set_layer(&P, PL_DESKTOP);
    btn(PAD_A, false);                      EXPECT("and its release is silent", "");

    btn(PAD_X, true);                       EXPECT("X opens the keyboard", "c:osk");
    pad_set_layer(&P, PL_OSK);
    btn(PAD_X, false);                      EXPECT("its release in the keyboard layer is not a Shift", "");

    reset(PL_DESKTOP);
    btn(PAD_DUP, true);                     EXPECT("D-pad up held in the desktop", "k103+");
    pad_set_layer(&P, PL_OSK);
    btn(PAD_DUP, false);                    EXPECT("released after the layer changed", "k103-");

    reset(PL_OSK);
    btn(PAD_DUP, true);                     EXPECT("nav in the keyboard layer", "c:nav-up");
    check_int("repeating", tick_at(T), 350);
    pad_set_layer(&P, PL_DESKTOP);
    check_int("the layer change ends the repeat", tick_at(T + 400), -1);
    EXPECT("nothing is sent", "");
    btn(PAD_DUP, false);                    EXPECT("its release is silent", "");

    reset(PL_BROWSER);
    btn(PAD_B, true);                       EXPECT("B pending in the browser", "");
    pad_set_layer(&P, PL_DESKTOP);
    btn(PAD_B, false);                      EXPECT("the release still decides: Back", "b275+ b275-");

    reset(PL_BROWSER);
    btn(PAD_B, true);
    pad_set_layer(&P, PL_DESKTOP);
    tick_at(T + 500);
    EXPECT("or Escape if it was held", "k1+ k1-");
    btn(PAD_B, false);                      EXPECT("released silently", "");

    reset(PL_DESKTOP);
    btn(PAD_L2, true); btn(PAD_R1, true); take();
    pad_set_layer(&P, PL_BROWSER); pad_set_layer(&P, PL_OSK);
    btn(PAD_L2, false);                     EXPECT("Shift+Tab is released whole", "k15- k42-");
    btn(PAD_R1, false);                     EXPECT("the click too", "b272-");
}

static void t_stick_lock(void)
{
    reset(PL_DESKTOP);
    ax(PAD_LX, -0.9);                       take();
    pad_set_layer(&P, PL_BROWSER);
    check_int("in the browser layer the repeat is over", tick_at(T + 400), 8);
    take();
    pad_set_layer(&P, PL_DESKTOP);
    ax(PAD_LX, -0.9);
    EXPECT("a stick still pushed when the layer changes does not type", "");
    check_int("no repeat either", tick_at(T + 1000), -1);
    ax(PAD_LX, -0.5);
    ax(PAD_LX, -0.2);                       EXPECT("back in the middle", "");
    ax(PAD_LX, -0.9);                       EXPECT("then it types again", "k105+ k105-");
}

/* ------------------------------------------------------------------ everything released */
static void t_release_all(void)
{
    reset(PL_DESKTOP);
    btn(PAD_R1, true); btn(PAD_A, true); btn(PAD_DUP, true); btn(PAD_L2, true); btn(PAD_L3, true);
    ax(PAD_LX, 0.9); ax(PAD_RX, 0.9); take();
    pad_release_all(&P, &O);
    const char *w = take();
    char sorted[512];
    snprintf(sorted, sizeof sorted, "%s", w);
    check_true("every held output goes up once", strstr(w, "b272-") && strstr(w, "k28-") && strstr(w, "k103-") && strstr(w, "k15-") && strstr(w, "k42-"));
    int ups = 0;
    for (const char *s = w; *s; s++) if (*s == '-') ups++;
    check_int("and nothing else", ups, 5);
    btn(PAD_R1, false); btn(PAD_A, false); btn(PAD_DUP, false); btn(PAD_L2, false);
    EXPECT("physical releases afterwards are silent", "");
    check_int("quiet", tick_at(T + 100), -1);
    ax(PAD_RX, 0);
    pad_release_all(&P, &O);
    EXPECT("nothing held: nothing sent", "");
    btn(PAD_L3, true);
    pad_release_all(&P, &O);
    ax(PAD_RX, 1.0);
    tick_at(T);
    int s1, s2;
    sum_run(1000, &s1, &s2);
    check_true("the slow factor is cleared as well", s1 >= 695);
}

static void t_two_sources_one_key(void)
{
    reset(PL_DESKTOP);
    btn(PAD_DLEFT, true);                   EXPECT("the D-pad holds Left", "k105+");
    ax(PAD_LX, -0.9);                       EXPECT("the stick taps the same key: no second down, no early up", "");
    ax(PAD_LX, 0);
    btn(PAD_DLEFT, false);                  EXPECT("the key goes up when the last source lets go", "k105-");
    btn(PAD_R2, true); btn(PAD_L2, true);   EXPECT("R2 and L2 together share Tab", "k15+ k42+");
    btn(PAD_R2, false);                     EXPECT("one lets go, Tab stays down", "");
    btn(PAD_L2, false);                     EXPECT("the other lets go", "k15- k42-");
}

/* ------------------------------------------------------------------ the layer for a window */
static void t_layer_for(void)
{
    check_int("a terminal", pad_layer_for("foot", false), PL_DESKTOP);
    check_int("firefox", pad_layer_for("firefox", false), PL_BROWSER);
    check_int("the browser of the image (Firefox started with its own class)", pad_layer_for("wisp-browser", false), PL_BROWSER);
    check_int("another window of ours is not one", pad_layer_for("wisp-monitor", false), PL_DESKTOP);
    check_int("firefox ESR on Wayland", pad_layer_for("Firefox-esr", false), PL_BROWSER);
    check_int("the full application ID", pad_layer_for("org.mozilla.firefox", false), PL_BROWSER);
    check_int("chromium", pad_layer_for("chromium-browser", false), PL_BROWSER);
    check_int("none (no window focused)", pad_layer_for(NULL, false), PL_DESKTOP);
    check_int("empty", pad_layer_for("", false), PL_DESKTOP);
    check_int("the keyboard wins", pad_layer_for("firefox", true), PL_OSK);
    check_int("a name that only contains one", pad_layer_for("not-firefox", false), PL_DESKTOP);
}

/* ------------------------------------------------------------------ typing for the on-screen keyboard */
static void t_type(void)
{
    reset(PL_OSK);
    pad_type(&P, 30, 0, &O);            EXPECT("a letter", "k30+ k30-");
    pad_type(&P, 30, PAD_MOD_SHIFT, &O);             EXPECT("a capital: Shift around it", "k42+ k30+ k30- k42-");
    pad_type(&P, 0, 0, &O);             EXPECT("code 0 is not a key", "");
    pad_type(&P, PAD_KEY_MAX + 1, 0, &O);  EXPECT("past the keyboard range", "");
    pad_type(&P, PAD_KEY_MAX, 0, &O);   EXPECT("the last code", "k247+ k247-");
    pad_type(&P, -5, PAD_MOD_SHIFT, &O);             EXPECT("negative", "");
    btn(PAD_L2, true);                      EXPECT("L2 holds Shift and Tab", "k42+ k15+");
    pad_type(&P, 30, PAD_MOD_SHIFT, &O);             EXPECT("a capital while Shift is held: no second Shift down, no Shift up", "k30+ k30-");
    btn(PAD_L2, false);                     EXPECT("L2 lets go of Shift last", "k15- k42-");
    pad_type(&P, 30, PAD_MOD_CTRL, &O);     EXPECT("Ctrl+A", "k29+ k30+ k30- k29-");
    pad_type(&P, 30, PAD_MOD_CTRL | PAD_MOD_SHIFT, &O);   EXPECT("Ctrl+Shift+A: Ctrl first, released last", "k29+ k42+ k30+ k30- k42- k29-");
    pad_type(&P, 30, PAD_MOD_ALT, &O);      EXPECT("Alt+A", "k56+ k30+ k30- k56-");
    pad_type(&P, 30, PAD_MOD_CTRL | PAD_MOD_ALT | PAD_MOD_SHIFT, &O);   EXPECT("all three", "k29+ k56+ k42+ k30+ k30- k42- k56- k29-");
    btn(PAD_R2, true);                      take();
    pad_type(&P, K_TAB, 0, &O);         EXPECT("typing a key another source holds is silent", "");
    btn(PAD_R2, false);                     EXPECT("and it stays down until that source lets go", "k15-");
}

/* ------------------------------------------------------------------ names fit */
static void t_names(void)
{
    const char *names[] = { "menu", "summon", "osk", "actions", "scroll-on", "scroll-off", "nav-up", "nav-down", "nav-left", "nav-right",
                            "osk-type", "osk-del", "osk-shift", "osk-space", "osk-enter", "osk-hide" };
    PadOut v;
    for (size_t i = 0; i < sizeof names / sizeof *names; i++) check_true(names[i], strlen(names[i]) < sizeof v.cmd);
}

/* ------------------------------------------------------------------ random streams: no key is ever stuck, doubled or dropped */
static unsigned long long rng_state = 88172645463325252ULL;
static unsigned rnd(void) { rng_state ^= rng_state << 13; rng_state ^= rng_state >> 7; rng_state ^= rng_state << 17; return (unsigned)(rng_state >> 11); }

static const int CODES[] = { PAD_A, PAD_B, PAD_X, PAD_Y, PAD_L1, PAD_R1, PAD_L2, PAD_R2, PAD_SELECT, PAD_START, PAD_MODE, PAD_L3, PAD_R3, PAD_DUP, PAD_DDOWN, PAD_DLEFT, PAD_DRIGHT };

static unsigned char model_down[PAD_CODES];
static long model_double, model_updown, model_big;

static void model_apply(void)
{
    for (int i = 0; i < O.n; i++) {
        const PadOut *v = &O.v[i];
        if (v->kind == PO_KEY || v->kind == PO_BTN) {
            if (v->code < 0 || v->code >= PAD_CODES) { model_big++; continue; }
            if (v->value && model_down[v->code]) model_double++;
            if (!v->value && !model_down[v->code]) model_updown++;
            model_down[v->code] = v->value ? 1 : 0;
        } else if (v->kind == PO_REL) {
            if (abs(v->dx) > 40 || abs(v->dy) > 40) model_big++;
        } else if (v->kind == PO_WHEEL) {
            if (abs(v->dx) > 800 || abs(v->dy) > 800) model_big++;
        }
    }
    O.n = 0;
}

static void t_random(void)
{
    reset(PL_DESKTOP);
    memset(model_down, 0, sizeof model_down);
    model_double = model_updown = model_big = 0;
    int64_t t = 1000000;
    for (long step = 0; step < 300000; step++) {
        t += rnd() % 30;
        T = t;
        unsigned r = rnd() % 100;
        if (r < 30) pad_button(&P, t, CODES[rnd() % (sizeof CODES / sizeof *CODES)], rnd() & 1, &O);
        else if (r < 60) pad_axis(&P, t, (int)(rnd() % 4), (int)(rnd() % 70000) - 35000, &O);
        else if (r < 65) pad_axis(&P, t, (int)(rnd() % 4), 0, &O);
        else if (r < 70) pad_set_layer(&P, (PadLayer)(rnd() % 3));
        else if (r < 72) t += 2000;
        else pad_tick(&P, t, &O);
        model_apply();
        if (step % 50000 == 49999) {                  /* a settling: let go of everything, centre the sticks, let time pass */
            for (size_t i = 0; i < sizeof CODES / sizeof *CODES; i++) pad_button(&P, t, CODES[i], false, &O);
            for (int a = 0; a < 4; a++) pad_axis(&P, t, a, 0, &O);
            model_apply();
            t += 2000;
            int w = pad_tick(&P, t, &O);
            model_apply();
            t += 2000;
            w = pad_tick(&P, t, &O);
            model_apply();
            check_int("settled: no tick is wanted", w, -1);
            int stuck = 0, held = 0;
            for (int c = 0; c < PAD_CODES; c++) { stuck += model_down[c]; held += P.held[c]; }
            check_int("settled: no output key is down", stuck, 0);
            check_int("settled: no hold is counted", held, 0);
        }
    }
    check_int("never a key down twice", model_double, 0);
    check_int("never an up without a down", model_updown, 0);
    check_int("no step beyond what the time allows", model_big, 0);
    check_int("no output ever dropped", P.dropped, 0);
    pad_release_all(&P, &O);
    model_apply();
    int stuck = 0;
    for (int c = 0; c < PAD_CODES; c++) stuck += model_down[c];
    check_int("after release_all nothing is down", stuck, 0);
}

int main(void)
{
    t_buttons_desktop();
    t_dpad_desktop();
    t_scroll_toggle_and_slow();
    t_stick_thresholds();
    t_stick_directions();
    t_stick_repeat();
    t_pointer();
    t_scroll_mode();
    t_browser();
    t_osk();
    t_layer_changes();
    t_stick_lock();
    t_release_all();
    t_two_sources_one_key();
    t_layer_for();
    t_type();
    t_names();
    t_random();
    printf("%d checks, %d failed\n", checks, failed);
    return failed ? 1 : 0;
}
