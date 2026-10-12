/* The pad's logic: how the handheld's controls become a pointer and a keyboard in desktop mode (docs/WISPOS-OS-DESIGN.md "Controls and the on-screen keyboard in desktop mode",
 * LOCKED, and "Browsing with the gamepad"). No I/O and no clock of its own: button and axis events and the time go in, actions come out, so it is tested with scripted streams
 * (os/pad/test_logic.c, os/test_pad.py). The reading of the device and the virtual keyboard and mouse are in pad-io.c.
 *
 * Layers (chosen from outside, see pad_set_layer): DESKTOP is the locked layout; BROWSER differs only in the left stick (it scrolls), B (Back, held Escape), Y (Forward) and Select (the
 * browser actions list); OSK is while the on-screen keyboard has the pad: the D-pad and the left stick move its key focus and the face buttons type, the pointer keeps working.
 * The rule over all of it: gamepad-first, never gamepad-only. Everything here comes from pad events and adds to a keyboard and a mouse; nothing is taken from them.
 *
 * Codes are Linux evdev codes (input-event-codes.h). The handheld's are read from its device tree source (sun50i-h700-anbernic-rg35xx-2024.dts, included by the RG34XX-SP's): A is
 * BTN_EAST, B BTN_SOUTH, X BTN_NORTH, Y BTN_WEST, the D-pad four BTN_DPAD_*, L1/R1 BTN_TL/BTN_TR (the outer shoulder buttons), L2/R2 BTN_TL2/BTN_TR2 (the inner ones), BTN_SELECT,
 * BTN_START, BTN_MODE, BTN_THUMBL and BTN_THUMBR; the sticks are ABS_X/ABS_Y and ABS_RX/ABS_RY. Not yet confirmed on the running device (evtest is in the device checklist). */
#ifndef WISPOS_PAD_LOGIC_H
#define WISPOS_PAD_LOGIC_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define PAD_A      305               /* BTN_EAST  */
#define PAD_B      304               /* BTN_SOUTH */
#define PAD_X      307               /* BTN_NORTH */
#define PAD_Y      308               /* BTN_WEST  */
#define PAD_L1     310               /* BTN_TL  */
#define PAD_R1     311               /* BTN_TR  */
#define PAD_L2     312               /* BTN_TL2 */
#define PAD_R2     313               /* BTN_TR2 */
#define PAD_SELECT 314
#define PAD_START  315
#define PAD_MODE   316
#define PAD_L3     317               /* BTN_THUMBL */
#define PAD_R3     318               /* BTN_THUMBR */
#define PAD_DUP    544               /* BTN_DPAD_UP .. BTN_DPAD_RIGHT */
#define PAD_DDOWN  545
#define PAD_DLEFT  546
#define PAD_DRIGHT 547

#define PAD_LX 0                     /* axis indices of pad_axis; a value is -PAD_AXIS_MAX..PAD_AXIS_MAX, up and left negative (the I/O layer normalises and flips) */
#define PAD_LY 1
#define PAD_RX 2
#define PAD_RY 3
#define PAD_AXIS_MAX 32767

/* what the virtual keyboard and mouse send (evdev) */
#define K_ESC 1
#define K_BACKSPACE 14
#define K_TAB 15
#define K_ENTER 28
#define K_LCTRL 29
#define K_LSHIFT 42
#define K_LALT 56
#define K_SPACE 57
#define K_UP 103
#define K_LEFT 105
#define K_RIGHT 106
#define K_DOWN 108
#define M_LEFT 0x110
#define M_RIGHT 0x111
#define M_SIDE 0x113                 /* the mouse's Back button */
#define M_EXTRA 0x114                /* ... and Forward */

typedef enum { PL_DESKTOP, PL_BROWSER, PL_OSK } PadLayer;
typedef enum { PO_KEY, PO_BTN, PO_REL, PO_WHEEL, PO_CMD } PadOutKind;

typedef struct {
    PadOutKind kind;
    int code;                        /* PO_KEY / PO_BTN: the evdev code */
    int value;                       /* PO_KEY / PO_BTN: 1 down, 0 up */
    int dx, dy;                      /* PO_REL: pixels; PO_WHEEL: hi-res units, 120 to a notch, up and right positive */
    int notch_x, notch_y;            /* PO_WHEEL: the whole notches completed by this step (the legacy REL_WHEEL events that a hi-res wheel also sends) */
    char cmd[20];                    /* PO_CMD: for the shell: menu summon osk actions scroll-on scroll-off nav-{up,down,left,right} osk-{type,del,shift,space,enter,hide} */
} PadOut;

#define PAD_OUTS 64                  /* an event makes at most 6 outputs and a tick at most 14, so this is never reached; Pad.dropped counts it if it ever is */
typedef struct { PadOut v[PAD_OUTS]; int n; } PadOuts;

typedef struct {
    float press, release;            /* the left stick as arrow keys: down at this deflection, up below the other (hysteresis) */
    float dead;                      /* the right stick's dead zone (a share of the full deflection) */
    float ptr_speed;                 /* pointer pixels a second at full deflection */
    float ptr_curve;                 /* exponent on the deflection past the dead zone: above 1 gives fine control near the middle */
    float slow;                      /* the pointer's factor while L3 is held */
    float scroll_rate;               /* wheel notches a second at full deflection */
    float scroll_curve;
    int   hold_ms;                   /* B held this long is Escape in the browser layer (a shorter press is Back) */
    int   repeat_delay_ms;           /* a held direction repeats after this long ... */
    int   repeat_ms;                 /* ... at this period, and the left stick's speeds up to */
    int   repeat_fast_ms;            /* ... this period over ramp_ms */
    int   ramp_ms;
} PadConfig;

typedef struct { uint8_t kind; int arg; } PadAct;            /* what a press started, so its release undoes exactly that, whatever layer it comes in */
typedef struct { int dir; int64_t start, next; } PadRep;     /* a held direction: 0 none, PAD_STICK_LOCKED until the stick has come back to the middle, else the arrow key code */
#define PAD_STICK_LOCKED (-1)
#define PAD_CODES 0x240

typedef struct {
    PadConfig cfg;
    PadLayer layer;
    bool scroll_mode;                /* R3: the right stick scrolls instead of moving the pointer */
    bool slow;                       /* L3 held */
    float ax[4];                     /* -1..1 */
    bool isdown[PAD_CODES];          /* pad buttons held */
    PadAct act[PAD_CODES];
    uint8_t held[PAD_CODES];         /* how many sources hold each output key or button down (the D-pad and the left stick can both want an arrow) */
    PadRep stick, dpad;              /* the left stick, and the D-pad in the OSK layer, as repeating directions */
    int64_t t_last;                  /* the last tick, -1 when the pad has been quiet */
    int64_t b_since;                 /* when B went down in the browser layer, -1 when no Back is pending */
    float fx, fy, wx, wy;            /* fractions of a pixel and of a wheel unit carried between ticks */
    int hi_x, hi_y;                  /* wheel units since the last whole notch */
    int dropped;                     /* outputs that did not fit (always 0; the tests assert it) */
} Pad;

void  pad_config_default(PadConfig *c);
void  pad_init(Pad *p, const PadConfig *c);
void  pad_button(Pad *p, int64_t now, int code, bool down, PadOuts *out);
void  pad_axis(Pad *p, int64_t now, int axis, int value, PadOuts *out);
/* Time passes (`now` in ms). Returns the ms until the next call is wanted, or -1 when nothing needs one. */
int   pad_tick(Pad *p, int64_t now, PadOuts *out);
/* Held keys and buttons stay down until their own release, so a layer change leaves no key stuck and no release stray. A stick held over in a direction waits for the middle. */
void  pad_set_layer(Pad *p, PadLayer l);
/* Every output key and button up, every state cleared (the device went away, or the daemon stops). */
void  pad_release_all(Pad *p, PadOuts *out);
/* The layer for the focused window: OSK while the keyboard is open, BROWSER for a known browser's app_id (case-insensitive), else DESKTOP. */
PadLayer pad_layer_for(const char *app_id, bool osk_open);
/* A key typed for the on-screen keyboard (it sits in the shell, the keys go out through the pad's virtual keyboard): down, up, with the modifiers asked for held around it (Ctrl and Alt first,
 * then Shift; released in the opposite order). Codes outside the keyboard range (1..PAD_KEY_MAX) are ignored. Shift shares its hold with L2, so a typed capital while L2 is down leaves Shift to
 * L2's release. */
#define PAD_KEY_MAX 247
#define PAD_MOD_SHIFT 1
#define PAD_MOD_CTRL 2
#define PAD_MOD_ALT 4
void  pad_type(Pad *p, int code, unsigned mods, PadOuts *out);
/* An output as one short word, for the tests, the dry run and logs: k<code>+ / k<code>- key, b<code>+ / b<code>- button, r<dx>,<dy> pointer, w<dx>,<dy>/<nx>,<ny> wheel, c:<name> command. */
int   pad_out_text(const PadOut *v, char *buf, size_t cap);

#endif
