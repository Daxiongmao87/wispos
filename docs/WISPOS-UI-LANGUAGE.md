# WispOS UI language

**Reference implementation: the first-time setup screen** (`os/setup/wisp-setup.c`; every state rendered in `os/setup/design/*.png`). Its look follows Steam's own UI (palette read from Steam's stylesheets), so a WispOS screen and Steam read as one system. **Every screen and component reuses it** (boot splash, desktop-mode menu, the SSH and USB-host toggles, update screens, failure screens): no colours, typefaces, spinners, buttons, corner radii or layouts of its own. If something genuinely new is needed, add it HERE and to `os/ui/wisp-ui.h` first, then use it. (Operator, 2026-10-11, after the first splash prototype invented its own palette, pixel font and spinner: "stop reinventing the ui design language for every component ... add the first time setup as a design reference".)

## Tokens (single source: `os/ui/wisp-ui.h`)
| Token | Value | Use |
| --- | --- | --- |
| ground | `#0e141b` | screen background |
| surface | `#23262e` | rows, key pills, unselected buttons |
| divider | `#3d4450` | lines, pending step dots |
| text | `#ffffff` | titles, active text |
| dim | `#8b929a` | secondary text, captions |
| dim2 | `#67707b` | pending steps |
| glyph | `#b8bcbf` | key-pill letters, icons |
| accent | `#1a9fff` | spinner, current step, links |
| ok | `#59bf40` | done, connected |
| bad | `#d94126` | failure, "DEV BUILD" marker |
| focus | white fill, ground-coloured text | the selected row or button (as Steam's keyboard and menus) |
| progress fill | `#47bfff` to `#1a44c2` over 60% | download bars |
| boot ground | `#000000` | boot-time screens only (see "Boot-time screens") |
| radius | 2 px (pills and discs: fully round) | all rectangles |
| screen | 720x480, gamepad first | |
| type | Liberation Sans (Steam names Motiva Sans, which is not shipped): Title Bold 30, Body 23, Small 18, Tiny 15 | titles, rows, details, captions and hints |

## Layout grid
- **Left identity column** (centre x = 125): the WispOS badge (190 px tall at y = 40), "WispOS" in Title at y = 240, a Tiny caption in capitals (dim) at y = 276 ("FIRST-TIME SETUP", "STARTING"), optional step list from y = 330, a red "DEV BUILD" at y = 458 on development builds. It stays pixel-identical from screen to screen, so a hand-over between two screens changes only the right panel.
- **Right panel** (x = 250, width 450): Title at y = 34 (+ optional Small dim subtitle at y = 76), content from y = 150, a Small dim detail line at y = 330, footer hints at y = 452.

## Components
- **Spinner:** a ring of 12 round dots (radius 3), accent colour, the head full opacity and the tail fading (alpha = 255 - 215 x age); 12 steps a second. Radius 10 in rows.
- **Progress row:** a status disc at x = 264 (done: green disc with a ground-coloured check; current: the spinner; pending: a small divider-coloured disc), then the text in Body at x = 294 (white when done or current, dim2 when pending); rows 48 px apart from y = 150.
- **Buttons / list rows:** 450 x 52, surface fill, text in Body at +22; the selected one is a white fill with ground-coloured text.
- **Hint:** a key pill (surface, fully round, the key letter in glyph colour, Tiny) followed by the label (Tiny, dim): "A Select". Always at the bottom-left of the right panel.
- **Failure:** a bad-coloured disc (radius 22) with "!" in Title, then the title in Title, the reason in Small dim, then the choices as buttons.
- **Text:** never wraps; too long = cut with "..." at the panel width.


## Desktop mode (LOCKED 2026-10-12, operator: "I do like the design. Lock it in"; the pictures are os/dock/design/*.png, drawn by os/dock/mockup.py from the tokens; a change to the pictures, the tokens or the default wallpaper is the operator's)
Desktop mode is not a left-column screen: it is a wallpaper, a strip and a few overlays on top of ordinary windows, so it keeps the palette, the type and the component rules and drops the identity column. Every size below is a token in `os/ui/wisp-ui.h` (`WISP_UI_DOCK_*`, `WISP_UI_MENU_*`, `WISP_UI_KEY_*`, `WISP_UI_QS_W` and the rest); the pictures are tested against them (`os/test_dock_design.py`). Corners are `WISP_UI_RADIUS`; pills, dots and switches are fully round.
- **Wallpaper** (`wallpaper.png`, `wallpaper-variants.png`; `os/ui/wallpaper.py`). A vertical gradient from `WISP_UI_WALL_TOP` to `WISP_UI_WALL_BOTTOM` with a soft glow of the accent near the upper left and a slight vignette, dithered so a dark gradient does not band on an 8-bit panel. Optionally the badge as a very faint negative space, whole, right of centre, above the strip: `carved` (darkened a few levels inside its outline, with a one-pixel shade and lift on the edges, as if cut into the surface; the default, 3 levels), `ghost` (its own colours added at 1.8 %) or none. Never more than 6 levels of 255 from the plain gradient (tested).
- **Dock strip** (`desktop-empty.png`, `desktop-window.png`). 44 px along the bottom edge on `WISP_UI_PANEL` at 236/255 opacity with a `WISP_UI_LINE` top line; it reserves its space. Items are 36 px tiles on a 44 px pitch, a well in the ground colour with a 22 px glyph (white tile and ground-coloured glyph when focused, the list-row rule); glyph colours are palette colours only (accent for the browser and Game mode, green for the terminal, glyph grey for the rest). Left to right: the Applications button (a 3 x 3 dot grid), a divider, the pinned items, a divider and the other open windows; a running application has a 4 px dot in `WISP_UI_DIM` under its glyph and the window in front a 14 x 3 accent bar. At the right: the Game mode item (the way back), a divider, the status group (Wi-Fi, volume, battery, clock) which is one focusable item and opens the quick settings.
- **Summoned dock and focus bar** (`dock-focus.png`). The strip gets a 2 px accent line; a 28 px bar appears above it with the focused item's name (Small bold) at the left and the buttons as key pills at the right ("A Open", "X Unpin", "Y More", "B Back").
- **Item actions** (`dock-more.png`). A list above the item: Open, Pin or Unpin, Move left, Move right, Close window; rows of 36 px, the selected row white, on the ground colour with a `WISP_UI_LINE` outline.
- **Application menu** (`apps-menu.png`). A full-screen overlay: the ground colour at 248/255 over whatever is behind, Title "Applications" and a Small dim line at the first tile's left edge, a grid of 4 columns of 150 x 128 cells with 72 px tiles and 44 px glyphs, the name in Small under each, the selected tile white, a small pin glyph on the tiles of pinned applications, the key-pill hints at `WISP_UI_HINT_Y`.
- **Quick settings** (`quick-settings.png`). A 316 px panel above the strip's right end on the ground colour: rows in `WISP_UI_PANEL` (a glyph, the name in Small, a value in Tiny dim at the right) as the setup screen's lists; switches are 40 x 22 pills (the OK green when on), sliders a 4 px track with a 6 px knob, the USB port a three-segment control (the active segment white) with "off at every boot"; the focused row is white; the hints sit at the panel's foot.
- **Keyboard** (`osk.png`, `osk-symbols.png`). The first-time setup keyboard, full width: keys 40 px high, 4 px apart, on a `WISP_UI_PANEL` surface; letter keys in the ground colour with Body text, the special keys in `WISP_UI_FIELD` black, an active modifier and Enter in the accent, the focused key white; the rows are the setup screen's (`KB_LOWER`, `KB_UPPER`, `KB_SYM` in `os/setup/kb.h`, tested equal to the picture's; on the setup screen's own symbols page `del` takes the place of `shift` among the special keys because ten symbols leave no room for it in their row), 60 px letter keys, a fifth row of specials (123, shift, ctrl, space, tab, esc, enter) and on the symbols page the arrows, backtick and tilde beside space, del and enter. It sits above the hints (`WISP_UI_HINT_Y`), the dock is hidden while it is open, and it has no field of its own: it types into the window that has the focus. Hints: "A Type", "B Delete", "X Shift", "Y Space", "Start Enter", "Select Hide".
- **Pointer.** A white arrow with a black outline; it hides after a few seconds without movement (sway: `seat * hide_cursor <ms>`), so a focused item and a hovered one are not both on screen.

## Boot-time screens (the one documented exception to the grid; operator, 2026-10-11)
The boot splash follows and precedes a black panel (bootloader, kernel, the compositor's first frames), so it uses a **black ground** (`WISP_UI_BOOT_BG`) and **one centred column** instead of the identity column and right panel: the badge (150 px tall, y = 36), "WispOS" in Title (y = 198), the round-dot spinner centred below it (centre y = 272, ring radius 14), the current stage in Body (y = 300), an optional Small dim detail line (y = 338), the key-pill hint centred at the bottom (y = 452), and on development builds a red "DEV BUILD" at the very top (y = 10). A failure replaces the spinner and the stage with the red "!" disc (radius 20), "Boot problem" in Body, the reason in Small dim. Everything else is the shared language: palette, Liberation Sans, the dot spinner, the key pill, the failure colour. Positions are the `WISP_UI_BOOT_*` tokens. Any other screen, including the first-run setup that follows the splash, keeps the standard ground and grid.

### Hand-over: boot to first-time setup (operator idea, 2026-10-11; plays once, only on the first run)
The one animated transition in the system, **3 seconds**, **fade out THEN fade in, never a cross-fade**: the ground goes from black to the setup screen's navy as one gradient across the whole 3 s; the boot elements (logo, name, spinner, stage, hint) fade OUT over the first **1.5 s** and are completely gone; then the first-time setup's elements fade IN over the last **1.5 s** (operator: 1.5 s out, 1.5 s in, 3 s in all). Curve (smoothstep s, t = elapsed / 3 s): ground = lerp(black, `#0e141b`, s(t)); boot layer = 1 - s(t / 0.5); setup layer = s((t - 0.5) / 0.5). **Owner: the setup program (SDL),** not the splash: the splash's last frame is black with the boot elements at full opacity; the setup program maps its window with the same first frame, the splash exits, and the setup program plays the 3 s itself (the boot elements are only a badge and three lines of text, and the gradient must run independently of the setup elements' opacity, which an overlay above a running setup window could not do). It plays only when the setup screen is started by the boot splash on a first run (a stamp file keeps it from replaying on later boots that need the setup screen again); the Steam path has no animation. Preview: `os/splash/preview_handover.py` (real boot frames from `wisp-splash`, the setup screen's own render).

## Rules for work
1. Look at `os/setup/design/*.png` and `wisp-setup.c` before drawing anything. A new screen is a new RIGHT PANEL on the same left column.
2. Constants come from `os/ui/wisp-ui.h`; a new colour or size is a change to the header and to this table, not a literal in a component.
3. Programs that cannot use SDL (the boot splash runs before Wayland exists) implement the same components with the same tokens, the same fonts (rendered from the same TTF files at build time) and the same badge image; the test checks the tokens against this document and the geometry against the grid.
4. **Gamepad-first, never gamepad-only** (operator, 2026-10-12: the UX must also make sense with a keyboard and a mouse, for example over VNC). Every desktop-mode component has a keyboard path and a pointer path as well as the pad's; pad-only conveniences appear only while the pad is the input; hover is a `WISP_UI_LINE` tile and a press the accent. The parity table is in docs/WISPOS-OS-DESIGN.md "Browsing with the gamepad".
