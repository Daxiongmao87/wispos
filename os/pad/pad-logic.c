/* The pad's logic: see pad-logic.h for the layout and the rules. Pure: no file, no clock, no allocation. */
#include "pad-logic.h"

#include <math.h>
#include <stdio.h>
#include <string.h>
#include <strings.h>

#define TICK_MS 8                    /* the pointer and the wheel are stepped this often while a stick is out of its dead zone */
#define MAX_DT_MS 50                 /* a stalled process must not turn into a jump of the pointer */

enum { ACT_NONE = 0, ACT_HOLD, ACT_SHIFT_TAB, ACT_BACK, ACT_SLOW, ACT_NAV };

void pad_config_default(PadConfig *c)
{
    *c = (PadConfig){
        .press = 0.55f, .release = 0.35f,               /* locked with the operator: a press at 55 %, a release at 35 %, so drift never types */
        .dead = 0.12f,                                  /* locked: a dead zone of about 12 % */
        .ptr_speed = 700.f, .ptr_curve = 2.0f, .slow = 0.25f,
        .scroll_rate = 10.f, .scroll_curve = 1.5f,
        .hold_ms = 450,
        .repeat_delay_ms = 350, .repeat_ms = 125, .repeat_fast_ms = 71, .ramp_ms = 1500,   /* locked: after 350 ms, 8 a second rising to 14 */
    };
}

void pad_init(Pad *p, const PadConfig *c)
{
    memset(p, 0, sizeof *p);
    p->cfg = *c;
    p->layer = PL_DESKTOP;
    p->t_last = -1;
    p->b_since = -1;
}

/* ------------------------------------------------------------------ outputs */
static void emit(Pad *p, PadOuts *o, const PadOut *v)
{
    if (o->n >= PAD_OUTS) { p->dropped++; return; }
    o->v[o->n++] = *v;
}

static void emit_key(Pad *p, PadOuts *o, int code, int value)
{
    PadOut v;
    memset(&v, 0, sizeof v);
    v.kind = (code >= 0x110 && code < 0x120) ? PO_BTN : PO_KEY;
    v.code = code;
    v.value = value;
    emit(p, o, &v);
}

/* A key or button is down while any source holds it: the output goes down on the first and up on the last. */
static void hold(Pad *p, PadOuts *o, int code)
{
    if (code < 0 || code >= PAD_CODES || p->held[code] == 255) { p->dropped++; return; }
    if (p->held[code]++ == 0) emit_key(p, o, code, 1);
}

static void release(Pad *p, PadOuts *o, int code)
{
    if (code < 0 || code >= PAD_CODES || p->held[code] == 0) return;
    if (--p->held[code] == 0) emit_key(p, o, code, 0);
}

static void tap(Pad *p, PadOuts *o, int code) { hold(p, o, code); release(p, o, code); }

static void cmd(Pad *p, PadOuts *o, const char *name)
{
    PadOut v;
    memset(&v, 0, sizeof v);
    v.kind = PO_CMD;
    snprintf(v.cmd, sizeof v.cmd, "%s", name);
    emit(p, o, &v);
}

static const char *nav_name(int dir)
{
    switch (dir) {
    case K_UP:   return "nav-up";
    case K_DOWN: return "nav-down";
    case K_LEFT: return "nav-left";
    default:     return "nav-right";
    }
}

/* One step in a direction: an arrow key tap for the desktop, a command for the on-screen keyboard. */
static void nav_fire(Pad *p, int dir, PadOuts *o)
{
    if (p->layer == PL_OSK) cmd(p, o, nav_name(dir));
    else tap(p, o, dir);
}

static int dpad_key(int code)
{
    switch (code) {
    case PAD_DUP:    return K_UP;
    case PAD_DDOWN:  return K_DOWN;
    case PAD_DLEFT:  return K_LEFT;
    case PAD_DRIGHT: return K_RIGHT;
    default:         return 0;
    }
}

/* ------------------------------------------------------------------ repeating directions */
static int rep_period(const Pad *p, const PadRep *r, bool ramp, int64_t at)
{
    float ms = (float)p->cfg.repeat_ms;
    if (ramp && p->cfg.ramp_ms > 0) {
        float t = (float)(at - r->start - p->cfg.repeat_delay_ms) / (float)p->cfg.ramp_ms;
        t = t < 0.f ? 0.f : t > 1.f ? 1.f : t;
        ms += ((float)p->cfg.repeat_fast_ms - ms) * t;
    }
    return ms < 1.f ? 1 : (int)(ms + 0.5f);
}

static void rep_start(Pad *p, PadRep *r, int dir, int64_t now, PadOuts *o)
{
    r->dir = dir;
    r->start = now;
    r->next = now + p->cfg.repeat_delay_ms;
    nav_fire(p, dir, o);
}

static int soonest(int wake, int64_t in)
{
    int ms = in < 1 ? 1 : in > 1000000 ? 1000000 : (int)in;
    return (wake < 0 || ms < wake) ? ms : wake;
}

static int rep_tick(Pad *p, PadRep *r, bool ramp, int64_t now, PadOuts *o, int wake)
{
    if (r->dir <= 0) return wake;
    for (int guard = 0; now >= r->next && guard < 4; guard++) {
        nav_fire(p, r->dir, o);
        r->next += rep_period(p, r, ramp, r->next);
    }
    if (now >= r->next) r->next = now + rep_period(p, r, ramp, now);     /* far behind: resume, do not burst */
    return soonest(wake, r->next - now);
}

/* ------------------------------------------------------------------ the left stick as four directions */
/* The direction the stick should hold given the one it holds: a held direction stays while its own deflection is above the release level, unless the other axis is out past the press
 * level and further than it; a new one needs the dominant axis past the press level. Diagonals snap to the dominant axis. */
static int stick_pick(const Pad *p, int cur)
{
    float x = p->ax[PAD_LX], y = p->ax[PAD_LY], axx = fabsf(x), ayy = fabsf(y);
    if (cur > 0) {
        bool horiz = cur == K_LEFT || cur == K_RIGHT;
        float own = horiz ? (cur == K_LEFT ? -x : x) : (cur == K_UP ? -y : y);
        float other = horiz ? ayy : axx;
        if (own >= p->cfg.release && !(other >= p->cfg.press && other > own)) return cur;
    }
    if (axx >= ayy) return axx >= p->cfg.press ? (x < 0 ? K_LEFT : K_RIGHT) : 0;
    return ayy >= p->cfg.press ? (y < 0 ? K_UP : K_DOWN) : 0;
}

static void stick_update(Pad *p, int64_t now, PadOuts *o)
{
    if (p->layer == PL_BROWSER) { p->stick.dir = 0; return; }          /* there the left stick scrolls */
    if (p->stick.dir == PAD_STICK_LOCKED) {
        if (fmaxf(fabsf(p->ax[PAD_LX]), fabsf(p->ax[PAD_LY])) < p->cfg.release) p->stick.dir = 0;
        return;
    }
    int d = stick_pick(p, p->stick.dir);
    if (d == p->stick.dir) return;
    if (d == 0) { p->stick.dir = 0; return; }
    rep_start(p, &p->stick, d, now, o);
}

/* ------------------------------------------------------------------ buttons */
static void press(Pad *p, int64_t now, int code, PadOuts *o)
{
    PadAct *a = &p->act[code];
    PadLayer L = p->layer;
    a->kind = ACT_NONE;
    a->arg = 0;
    switch (code) {
    case PAD_A:
        if (L == PL_OSK) cmd(p, o, "osk-type");
        else { hold(p, o, K_ENTER); *a = (PadAct){ ACT_HOLD, K_ENTER }; }
        break;
    case PAD_B:
        if (L == PL_OSK) cmd(p, o, "osk-del");
        else if (L == PL_BROWSER) { p->b_since = now; *a = (PadAct){ ACT_BACK, 0 }; }     /* Back on a short press, Escape when held: decided at the release or the timeout */
        else { hold(p, o, K_ESC); *a = (PadAct){ ACT_HOLD, K_ESC }; }
        break;
    case PAD_X:
        cmd(p, o, L == PL_OSK ? "osk-shift" : "osk");
        break;
    case PAD_Y:
        if (L == PL_OSK) cmd(p, o, "osk-space");
        else if (L == PL_BROWSER) { hold(p, o, M_EXTRA); *a = (PadAct){ ACT_HOLD, M_EXTRA }; }
        else { hold(p, o, K_SPACE); *a = (PadAct){ ACT_HOLD, K_SPACE }; }
        break;
    case PAD_R1: hold(p, o, M_LEFT);  *a = (PadAct){ ACT_HOLD, M_LEFT };  break;
    case PAD_L1: hold(p, o, M_RIGHT); *a = (PadAct){ ACT_HOLD, M_RIGHT }; break;
    case PAD_R2: hold(p, o, K_TAB);   *a = (PadAct){ ACT_HOLD, K_TAB };   break;
    case PAD_L2: hold(p, o, K_LSHIFT); hold(p, o, K_TAB); *a = (PadAct){ ACT_SHIFT_TAB, 0 }; break;
    case PAD_L3: p->slow = true; *a = (PadAct){ ACT_SLOW, 0 }; break;
    case PAD_R3:
        p->scroll_mode = !p->scroll_mode;
        cmd(p, o, p->scroll_mode ? "scroll-on" : "scroll-off");
        break;
    case PAD_START:  cmd(p, o, L == PL_OSK ? "osk-enter" : "menu"); break;
    case PAD_SELECT:
        if (L == PL_OSK) cmd(p, o, "osk-hide");
        else if (L == PL_BROWSER) cmd(p, o, "actions");                  /* in the desktop layer Select is free */
        break;
    case PAD_MODE:   cmd(p, o, "summon"); break;
    case PAD_DUP: case PAD_DDOWN: case PAD_DLEFT: case PAD_DRIGHT: {
        int k = dpad_key(code);
        if (L == PL_OSK) { rep_start(p, &p->dpad, k, now, o); *a = (PadAct){ ACT_NAV, k }; }
        else { hold(p, o, k); *a = (PadAct){ ACT_HOLD, k }; }          /* a key held down: the compositor's own repeat applies, as for any keyboard */
        break;
    }
    default: break;
    }
}

static void unpress(Pad *p, int code, PadOuts *o)
{
    PadAct a = p->act[code];
    p->act[code] = (PadAct){ ACT_NONE, 0 };
    switch (a.kind) {
    case ACT_HOLD: release(p, o, a.arg); break;
    case ACT_SHIFT_TAB: release(p, o, K_TAB); release(p, o, K_LSHIFT); break;
    case ACT_BACK:
        if (p->b_since >= 0) { tap(p, o, M_SIDE); p->b_since = -1; }     /* released before the hold time: Back (if the hold came first, it already was Escape) */
        break;
    case ACT_SLOW: p->slow = false; break;
    case ACT_NAV: if (p->dpad.dir == a.arg) p->dpad.dir = 0; break;
    default: break;
    }
}

void pad_button(Pad *p, int64_t now, int code, bool down, PadOuts *o)
{
    if (code < 0 || code >= PAD_CODES) return;
    if (down) {
        if (p->isdown[code]) return;
        p->isdown[code] = true;
        press(p, now, code, o);
    } else {
        if (!p->isdown[code]) return;
        p->isdown[code] = false;
        unpress(p, code, o);
    }
}

void pad_axis(Pad *p, int64_t now, int axis, int value, PadOuts *o)
{
    if (axis < 0 || axis > 3) return;
    if (value > PAD_AXIS_MAX) value = PAD_AXIS_MAX;
    if (value < -PAD_AXIS_MAX) value = -PAD_AXIS_MAX;
    p->ax[axis] = (float)value / (float)PAD_AXIS_MAX;
    if (axis == PAD_LX || axis == PAD_LY) stick_update(p, now, o);
}

/* ------------------------------------------------------------------ time */
/* The stick past its dead zone: a unit direction and how far out, 0..1 (the dead zone itself is 0). */
static float shape(float x, float y, float dead, float *ux, float *uy)
{
    float m = hypotf(x, y);
    if (m <= dead || m <= 0.f) { *ux = *uy = 0.f; return 0.f; }
    *ux = x / m;
    *uy = y / m;
    if (m > 1.f) m = 1.f;
    return (m - dead) / (1.f - dead);
}

static void wheel(Pad *p, PadOuts *o, float ux, float uy)
{
    p->wx += ux;
    p->wy += uy;
    int ix = (int)truncf(p->wx), iy = (int)truncf(p->wy);
    if (!ix && !iy) return;
    p->wx -= (float)ix;
    p->wy -= (float)iy;
    p->hi_x += ix;
    p->hi_y += iy;
    PadOut v;
    memset(&v, 0, sizeof v);
    v.kind = PO_WHEEL;
    v.dx = ix;
    v.dy = iy;
    v.notch_x = p->hi_x / 120;
    v.notch_y = p->hi_y / 120;
    p->hi_x -= v.notch_x * 120;
    p->hi_y -= v.notch_y * 120;
    emit(p, o, &v);
}

int pad_tick(Pad *p, int64_t now, PadOuts *o)
{
    int64_t dt = p->t_last < 0 ? 0 : now - p->t_last;
    if (dt < 0) dt = 0;
    if (dt > MAX_DT_MS) dt = MAX_DT_MS;
    p->t_last = now;
    int wake = -1;

    if (p->b_since >= 0) {                                              /* B held in the browser layer */
        int64_t due = p->b_since + p->cfg.hold_ms;
        if (now >= due) { tap(p, o, K_ESC); p->b_since = -1; }
        else wake = soonest(wake, due - now);
    }
    wake = rep_tick(p, &p->stick, true, now, o, wake);
    wake = rep_tick(p, &p->dpad, false, now, o, wake);

    bool active = false;
    float sx = 0.f, sy = 0.f, ux, uy;                                   /* wheel units this tick */
    float s = shape(p->ax[PAD_RX], p->ax[PAD_RY], p->cfg.dead, &ux, &uy);
    if (s > 0.f) {
        active = true;
        if (!p->scroll_mode) {
            float v = p->cfg.ptr_speed * powf(s, p->cfg.ptr_curve) * (p->slow ? p->cfg.slow : 1.f) * (float)dt / 1000.f;
            p->fx += ux * v;
            p->fy += uy * v;
        } else {
            float v = p->cfg.scroll_rate * 120.f * powf(s, p->cfg.scroll_curve) * (float)dt / 1000.f;
            sx += ux * v;
            sy -= uy * v;                                               /* stick up (negative) scrolls up (positive) */
        }
    }
    if (p->layer == PL_BROWSER) {                                       /* the left stick is the wheel */
        s = shape(p->ax[PAD_LX], p->ax[PAD_LY], p->cfg.dead, &ux, &uy);
        if (s > 0.f) {
            active = true;
            float v = p->cfg.scroll_rate * 120.f * powf(s, p->cfg.scroll_curve) * (float)dt / 1000.f;
            sx += ux * v;
            sy -= uy * v;
        }
    }
    if (active) {
        int ix = (int)truncf(p->fx), iy = (int)truncf(p->fy);
        if (ix || iy) {
            p->fx -= (float)ix;
            p->fy -= (float)iy;
            PadOut v;
            memset(&v, 0, sizeof v);
            v.kind = PO_REL;
            v.dx = ix;
            v.dy = iy;
            emit(p, o, &v);
        }
        wheel(p, o, sx, sy);
        wake = soonest(wake, TICK_MS);
    } else {
        p->fx = p->fy = p->wx = p->wy = 0.f;
        p->hi_x = p->hi_y = 0;
    }
    if (wake < 0) p->t_last = -1;                                       /* quiet: the next tick starts from nothing */
    return wake;
}

void pad_set_layer(Pad *p, PadLayer l)
{
    if (l == p->layer) return;
    p->layer = l;
    p->dpad.dir = 0;
    p->stick.dir = fmaxf(fabsf(p->ax[PAD_LX]), fabsf(p->ax[PAD_LY])) >= p->cfg.release ? PAD_STICK_LOCKED : 0;
}

void pad_release_all(Pad *p, PadOuts *o)
{
    for (int c = 0; c < PAD_CODES; c++) {
        if (p->held[c]) emit_key(p, o, c, 0);
        p->held[c] = 0;
        p->isdown[c] = false;
        p->act[c] = (PadAct){ ACT_NONE, 0 };
    }
    memset(p->ax, 0, sizeof p->ax);
    p->stick = (PadRep){ 0 };
    p->dpad = (PadRep){ 0 };
    p->slow = false;
    p->b_since = -1;
    p->fx = p->fy = p->wx = p->wy = 0.f;
    p->hi_x = p->hi_y = 0;
    p->t_last = -1;
}

void pad_type(Pad *p, int code, unsigned mods, PadOuts *o)
{
    if (code < 1 || code > PAD_KEY_MAX) return;
    if (mods & PAD_MOD_CTRL) hold(p, o, K_LCTRL);
    if (mods & PAD_MOD_ALT) hold(p, o, K_LALT);
    if (mods & PAD_MOD_SHIFT) hold(p, o, K_LSHIFT);
    tap(p, o, code);
    if (mods & PAD_MOD_SHIFT) release(p, o, K_LSHIFT);
    if (mods & PAD_MOD_ALT) release(p, o, K_LALT);
    if (mods & PAD_MOD_CTRL) release(p, o, K_LCTRL);
}

int pad_out_text(const PadOut *v, char *buf, size_t cap)
{
    switch (v->kind) {
    case PO_KEY: return snprintf(buf, cap, "k%d%c", v->code, v->value ? '+' : '-');
    case PO_BTN: return snprintf(buf, cap, "b%d%c", v->code, v->value ? '+' : '-');
    case PO_REL: return snprintf(buf, cap, "r%d,%d", v->dx, v->dy);
    case PO_WHEEL: return snprintf(buf, cap, "w%d,%d/%d,%d", v->dx, v->dy, v->notch_x, v->notch_y);
    default: return snprintf(buf, cap, "c:%s", v->cmd);
    }
}

/* ------------------------------------------------------------------ which layer */
static const char *const BROWSERS[] = {
    "wisp-browser", "firefox", "org.mozilla.firefox", "librewolf", "chromium", "org.chromium.chromium", "google-chrome", "brave", "com.brave",
    "epiphany", "org.gnome.epiphany", "falkon", "qutebrowser", "vivaldi",
};

PadLayer pad_layer_for(const char *app_id, bool osk_open)
{
    if (osk_open) return PL_OSK;
    if (!app_id) return PL_DESKTOP;
    for (size_t i = 0; i < sizeof BROWSERS / sizeof *BROWSERS; i++)
        if (strncasecmp(app_id, BROWSERS[i], strlen(BROWSERS[i])) == 0) return PL_BROWSER;
    return PL_DESKTOP;
}
