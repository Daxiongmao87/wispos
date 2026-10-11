/* WispOS UI tokens: the single source for the colours, sizes and layout of every screen. Plain C, no libraries.
 * The language and its rules: docs/WISPOS-UI-LANGUAGE.md. The reference implementation is the first-time setup screen (os/setup/wisp-setup.c,
 * rendered states in the PNG files of os/setup/design); its palette is Steam's own, read from Steam's stylesheets, so WispOS and Steam read as one system.
 * Do not put a colour, size or position for a screen in a component: add it here and in the language document first. */
#ifndef WISPOS_UI_TOKENS_H
#define WISPOS_UI_TOKENS_H

/* palette, 0xRRGGBB */
#define WISP_UI_BG        0x0e141b   /* ground */
#define WISP_UI_PANEL     0x23262e   /* surface: rows, key pills, unselected buttons */
#define WISP_UI_LINE      0x3d4450   /* divider, pending step dots */
#define WISP_UI_TEXT      0xffffff
#define WISP_UI_DIM       0x8b929a   /* secondary text, captions */
#define WISP_UI_DIM2      0x67707b   /* pending steps */
#define WISP_UI_GLYPH     0xb8bcbf   /* key-pill letters, icons */
#define WISP_UI_ACCENT    0x1a9fff   /* spinner, current step */
#define WISP_UI_OK        0x59bf40
#define WISP_UI_BAD       0xd94126   /* failure, DEV BUILD marker */
#define WISP_UI_FOCUS     0xffffff   /* selected row: white fill ... */
#define WISP_UI_FOCUS_FG  0x0e141b   /* ... with ground-coloured text */
#define WISP_UI_FOCUS_DIM 0x5a626c   /* secondary text on a selected row */
#define WISP_UI_FOCUS_ICO 0xbec3c9   /* icon background on a selected row */
#define WISP_UI_FOCUS_OK  0x24781e   /* "connected" on a selected row */
#define WISP_UI_BAR_FROM  0x47bfff   /* progress fill gradient, over the first 60% of the bar */
#define WISP_UI_BAR_TO    0x1a44c2

#define WISP_R(c) (((c) >> 16) & 0xff)
#define WISP_G(c) (((c) >> 8) & 0xff)
#define WISP_B(c) ((c) & 0xff)
#define WISP_SDL_COLOR(c)      { WISP_R(c), WISP_G(c), WISP_B(c), 255 }          /* initializer for an SDL_Color */
#define WISP_SDL_COLOR_A(c, a) { WISP_R(c), WISP_G(c), WISP_B(c), (a) }

/* shape and layout (720x480) */
#define WISP_UI_W          720
#define WISP_UI_H          480
#define WISP_UI_RADIUS     2         /* Steam's dominant corner radius; pills and discs are fully round */
#define WISP_UI_LEFT_CX    125       /* centre of the identity column */
#define WISP_UI_BADGE_Y    40
#define WISP_UI_BADGE_H    190
#define WISP_UI_NAME_Y     240       /* "WispOS" (Title) */
#define WISP_UI_CAPTION_Y  276       /* capitals, Tiny, dim */
#define WISP_UI_STEPS_Y    330
#define WISP_UI_DEVMARK_Y  458
#define WISP_UI_PANEL_X    250
#define WISP_UI_PANEL_W    450
#define WISP_UI_TITLE_Y    34
#define WISP_UI_SUBTITLE_Y 76
#define WISP_UI_ROWS_Y     150       /* first progress row; rows are 48 px apart */
#define WISP_UI_ROW_STEP   48
#define WISP_UI_DETAIL_Y   330       /* Small dim line under the rows */
#define WISP_UI_HINT_Y     452       /* key-pill hints, bottom-left of the right panel */

/* boot-time screens (the boot splash only, docs/WISPOS-UI-LANGUAGE.md "Boot-time screens"): a BLACK ground and one centred column, because they follow and precede
 * a black panel (bootloader, kernel, the compositor's first frames); every other screen uses the ground and the grid above. */
#define WISP_UI_BOOT_BG          0x000000
#define WISP_UI_BOOT_BADGE_H     150
#define WISP_UI_BOOT_BADGE_Y     36
#define WISP_UI_BOOT_NAME_Y      198      /* "WispOS" (Title), centred */
#define WISP_UI_BOOT_SPIN_CY     272      /* the spinner's centre; the ring is centred on the screen */
#define WISP_UI_BOOT_STATUS_Y    300      /* the current stage (Body), centred */
#define WISP_UI_BOOT_DETAIL_Y    338      /* Small dim detail line, centred */
#define WISP_UI_BOOT_FAIL_R      20       /* the failure disc, in place of the spinner */
#define WISP_UI_BOOT_DEVMARK_Y   10       /* "DEV BUILD", centred at the very top (the bottom centre is the key pill) */

/* type: Liberation Sans, pixel heights as the font sizes of SDL_ttf (os/setup/wisp-setup.c) */
#define WISP_UI_FONT_BOLD     "LiberationSans-Bold.ttf"
#define WISP_UI_FONT_REGULAR  "LiberationSans-Regular.ttf"
#define WISP_UI_SIZE_TITLE    30     /* Bold */
#define WISP_UI_SIZE_BODY     23
#define WISP_UI_SIZE_SMALL    18
#define WISP_UI_SIZE_TINY     15

/* spinner: a ring of round dots, the head opaque and the tail fading, WISP_UI_SPIN_SPEED steps a second */
#define WISP_UI_SPIN_DOTS     12
#define WISP_UI_SPIN_DOT_R    3
#define WISP_UI_SPIN_ROW_R    10     /* ring radius in a progress row */
#define WISP_UI_SPIN_SPEED    12
#define WISP_UI_SPIN_FADE     215    /* alpha = 255 - WISP_UI_SPIN_FADE x age, age 0 (head) .. 1 (tail) */

#endif
