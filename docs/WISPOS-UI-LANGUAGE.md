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

## Boot-time screens (the one documented exception to the grid; operator, 2026-10-11)
The boot splash follows and precedes a black panel (bootloader, kernel, the compositor's first frames), so it uses a **black ground** (`WISP_UI_BOOT_BG`) and **one centred column** instead of the identity column and right panel: the badge (150 px tall, y = 36), "WispOS" in Title (y = 198), the round-dot spinner centred below it (centre y = 272, ring radius 14), the current stage in Body (y = 300), an optional Small dim detail line (y = 338), the key-pill hint centred at the bottom (y = 452), and on development builds a red "DEV BUILD" at the very top (y = 10). A failure replaces the spinner and the stage with the red "!" disc (radius 20), "Boot problem" in Body, the reason in Small dim. Everything else is the shared language: palette, Liberation Sans, the dot spinner, the key pill, the failure colour. Positions are the `WISP_UI_BOOT_*` tokens. Any other screen, including the first-run setup that follows the splash, keeps the standard ground and grid.

### Hand-over: boot to first-time setup (operator idea, 2026-10-11; plays once, only on the first run)
The one animated transition in the system, **3 seconds**, **fade out THEN fade in, never a cross-fade**: the ground goes from black to the setup screen's navy as one gradient across the whole 3 s; the boot elements (logo, name, spinner, stage, hint) fade OUT over the first **1.5 s** and are completely gone; then the first-time setup's elements fade IN over the last **1.5 s** (operator: 1.5 s out, 1.5 s in, 3 s in all). Curve (smoothstep s, t = elapsed / 3 s): ground = lerp(black, `#0e141b`, s(t)); boot layer = 1 - s(t / 0.5); setup layer = s((t - 0.5) / 0.5). **Owner: the setup program (SDL),** not the splash: the splash's last frame is black with the boot elements at full opacity; the setup program maps its window with the same first frame, the splash exits, and the setup program plays the 3 s itself (the boot elements are only a badge and three lines of text, and the gradient must run independently of the setup elements' opacity, which an overlay above a running setup window could not do). It plays only when the setup screen is started by the boot splash on a first run (a stamp file keeps it from replaying on later boots that need the setup screen again); the Steam path has no animation. Preview: `os/splash/preview_handover.py` (real boot frames from `wisp-splash`, the setup screen's own render).

## Rules for work
1. Look at `os/setup/design/*.png` and `wisp-setup.c` before drawing anything. A new screen is a new RIGHT PANEL on the same left column.
2. Constants come from `os/ui/wisp-ui.h`; a new colour or size is a change to the header and to this table, not a literal in a component.
3. Programs that cannot use SDL (the boot splash runs before Wayland exists) implement the same components with the same tokens, the same fonts (rendered from the same TTF files at build time) and the same badge image; the test checks the tokens against this document and the geometry against the grid.
