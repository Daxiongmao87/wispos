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

/* large spinner of the Steam download screen (LOCKED, operator 2026-10-14; docs/WISPOS-UI-LANGUAGE.md "Large spinner", design record os/setup/design/mockup-large-spinner.html): in every phase,
 * LEFT of the bar and the percentage, its top edge on the bar's top (y 170) and its bottom edge on the bottom of the digits (y 217). Same dots, fade and speed as above. The dots are 9 px
 * and anti-aliased: disc() draws only even sizes, so the program needs its own dot routine for this ring. Check the bottom edge on a screenshot of the device (+-1 px). */
#define WISP_UI_SPIN_BIG_SIZE    47       /* outer diameter, px */
#define WISP_UI_SPIN_BIG_R       19.0f    /* ring radius, centre to dot centre */
#define WISP_UI_SPIN_BIG_DOT_D   9.0f     /* dot diameter */
#define WISP_UI_SPIN_BIG_GAP     14       /* spinner to bar */
#define WISP_UI_SPIN_BIG_CX      273.5f   /* WISP_UI_PANEL_X + SIZE / 2 */
#define WISP_UI_SPIN_BIG_CY      193.5f   /* middle of y 170 .. 217 */
#define WISP_UI_PROGRESS_X       311      /* the bar and the number start here (PANEL_X + SIZE + GAP); the bar ends at x 700 */
#define WISP_UI_PROGRESS_W       389


/* desktop-mode wallpaper (docs/WISPOS-UI-LANGUAGE.md "Desktop mode"; LOCKED 2026-10-12, operator 2026-10-12: "something subtle with gradients, perhaps with a very subtle negative space of our badge").
 * os/ui/wallpaper.py draws it from these; the blues are the ground and the accent, only darker and lifted a little. */
#define WISP_UI_WALL_TOP      0x142132   /* top-left of the vertical gradient */
#define WISP_UI_WALL_BOTTOM   0x0a1017   /* bottom edge */
#define WISP_UI_WALL_GLOW_X   28         /* centre of the accent glow, percent of the width ... */
#define WISP_UI_WALL_GLOW_Y   16         /* ... and of the height */
#define WISP_UI_WALL_GLOW     6          /* glow strength, percent of the accent colour added at its centre */


/* desktop-mode shell (docs/WISPOS-UI-LANGUAGE.md "Desktop mode"; LOCKED 2026-10-12, operator requirements 2026-10-12): a strip along the bottom edge and overlays on top of ordinary
 * windows, in the palette and type above. Corner radius is WISP_UI_RADIUS; pills, dots and switches are fully round. */
#define WISP_UI_DOCK_H         44        /* the strip; it reserves its space, so maximised windows are not covered */
#define WISP_UI_DOCK_ALPHA     236       /* strip opacity 0..255 over the wallpaper */
#define WISP_UI_DOCK_TILE      36        /* an item's tile (a well in the ground colour; white when focused) */
#define WISP_UI_DOCK_ICON      22        /* the glyph inside it */
#define WISP_UI_DOCK_PITCH     44        /* from one tile to the next */
#define WISP_UI_DOCK_INSET     8         /* first tile from the left edge, last status item from the right */
#define WISP_UI_DOCK_RUN_DOT   4         /* a running application: a dot ... */
#define WISP_UI_DOCK_RUN_BAR   14        /* ... a bar for the window in front */
#define WISP_UI_DOCK_RUN_H     3
#define WISP_UI_DOCK_STATUS    20        /* status glyphs at the right */
#define WISP_UI_DOCK_BAR       28        /* the bar above the strip while it is summoned: the item's name and the buttons */
#define WISP_UI_POPUP_ROW      36        /* rows of a popup list above an item, and of the quick-settings list */
#define WISP_UI_MENU_COLS      4         /* application menu: the grid */
#define WISP_UI_MENU_X         60
#define WISP_UI_MENU_Y         112
#define WISP_UI_MENU_CELL_W    150
#define WISP_UI_MENU_CELL_H    128
#define WISP_UI_MENU_TILE      72
#define WISP_UI_MENU_ICON      44
#define WISP_UI_MENU_SCRIM     248       /* the ground colour over what is behind the menu, opacity 0..255 */

/* components shared by the keyboard, the quick settings and the lists (LOCKED 2026-10-12 with the desktop-mode shell; the first-time setup keyboard is the reference for the keys) */
#define WISP_UI_FIELD          0x000000  /* text fields and the special keys of the keyboard (the setup screen's black) */
#define WISP_UI_KEY_H          40
#define WISP_UI_KEY_GAP        4
#define WISP_UI_KEY_W          60        /* a letter key of the full-width desktop keyboard (the setup's narrower panel uses 40) */
#define WISP_UI_OSK_Y          204       /* top of the desktop keyboard's panel; the hints stay at WISP_UI_HINT_Y; the dock is hidden while it is open */
#define WISP_UI_SWITCH_W       40
#define WISP_UI_SWITCH_H       22
#define WISP_UI_SLIDER_H       4
#define WISP_UI_SLIDER_KNOB    6
#define WISP_UI_SEGMENT_H      26
#define WISP_UI_QS_W           316       /* the quick-settings panel above the strip */

#endif
