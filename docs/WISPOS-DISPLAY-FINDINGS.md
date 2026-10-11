# WispOS display findings: refresh rate and resolution (RG34XXSP panel)

Consolidated 2026-10-08 from the session notes (`WISPOS-NOTES.md` section 5 and 5b hold the history, including the mistakes). Every result says how it was measured and who/what judged it.
Tools: `os/dev/diag/disptest.py`, `kmsflip.py`, `paneltest.py` (dev only). Operator judgement means the operator looked at the physical panel.

## 1. The panel

- Driver path: `sun4i-drm`, panel node `anbernic,rg34xx-sp-v2-panel` / `panel-mipi-dpi-spi` (SPI is only used to send setup commands; the picture goes over a parallel RGB bus). Connector `DSI-1`, 71 x 48 mm.
- The panel advertises exactly one mode: **720x480 @ 60.00 Hz**: pixel clock 26 MHz, horizontal 720/767/771/813, vertical 480/513/517/533, negative hsync/vsync. `/dev/fb0` is 720x480, 32 bpp.
- Nothing on the device can read back what the panel shows (write-only link; no sensor, no camera). Software can verify what the display controller does; only a person (or a camera) can verify the physical picture.

## 2. Refresh rate: any whole rate from 31 to 60 Hz works

- **Method:** keep the panel's own 26 MHz pixel clock and horizontal timing and lengthen only the vertical blanking: `vtotal = round(26,000,000 / 813 / Hz)`, `vsync_end = vtotal - 16`, `vsync_start = vsync_end - 4`.
  Through sway: `swaymsg -- output DSI-1 modeline 26.00 720 767 771 813 480 <vss> <vse> <vtotal> -hsync -vsync` (the `--` is required: without it `swaymsg` parses `-hsync` as an option, prints its usage and applies nothing).
  Restore: `swaymsg output DSI-1 mode 720x480@60Hz`. Directly (no compositor): `os/dev/diag/kmsflip.py` / `paneltest.py` program the same mode with libdrm.
- **Display controller (measured):** the kernel's vblank counter and per-flip timestamps matched every request within 0.1 Hz for 59 down to 31 (for example 49.97, 48.02, 44.98, 39.98, 36.01, 30.99), with no kernel messages, and 60.00 Hz restored every time.
- **Frame alignment (measured):** `paneltest.py` draws a new frame only every (Hz/fps)th refresh and flips on exactly that refresh. Over 22 combinations (60, 50, 48, 45, 40, 36, 31 Hz, each with every whole frame rate >= 10 that divides it) the effective fps equalled the ideal and **0 frames missed their refresh**, in two runs.
- **Operator judgement:** all of those combinations looked right on the physical panel ("worked perfectly", 2026-10-08).
- **Not tested:** rates above 60 Hz; power/battery effect of a lower refresh; any of this through the compositor with a real client (Steam via Xwayland, a Wine game).
- **Why the first test showed skips (my fault):** `disptest.py pattern` drew about 60 frames per second through Xwayland and sway whatever the panel rate, so at 40 Hz one frame in three was dropped by construction. A client only stays aligned if it paces itself to the refresh.
  Consequence for the real system: **Steam's UI (Chromium under Xwayland) and Wine games do not pace themselves to a lower refresh; a frame limiter/pacer is needed so the frame rate matches or divides the Hz** (the operator's stated intent). Not built.

## 3. Resolution below 720x480

- **The panel itself has only its native mode.** A smaller mode cannot be programmed on the panel; a smaller picture has to be scaled up by the compositor or the display engine.
- **Xwayland `xrandr` does not work for this:** it lists emulated modes (640x480, 320x240, ...) but `xrandr --output DSI-1 --mode 320x240` fails with `BadMatch (RRSetScreenSize)`: Xwayland refuses a screen resize from the tool.
- **`swaymsg output DSI-1 scale S` works** (S = 1.5, 2, 3): X11 clients get a smaller screen (480x320, 360x240, 240x160) and sway scales it up with nearest-neighbour (`scale_filter` is already `nearest`).
  Measured with a fullscreen X11 test pattern captured by `grim`: the red border touched all four screen edges at every scale (99% of edge samples), border thickness scaled 3/4/6/9 px, diagonals reached the corners: the picture fills the panel correctly. This is a compositor-level check, not the physical panel.
- **CPU effect: none seen in the one test done** (idle Steam UI, one 15 s sample each): GPU process 49% -> 40%, renderer 25% -> 37%, system idle 66% -> 66%. So the idle load is not pixel-bound; it needs a test while navigating.
- **Not done:** the operator has not judged the scale steps by eye; whether the real Steam UI is readable and correctly laid out at 360x240 is unjudged (the one screenshot was too dark); a hardware-scaled path (display-engine plane scaling) is unexplored; gamescope would be the SteamOS-style scaler but needs Vulkan, which this Mali-G31 build lacks.

## 3b. The real Steam UI at output scale 1, 1.5 and 2 (2026-10-08 00:18, SteamOS-mode UI, screenshots `ui-scale-*.png`)

The operator reported that Steam's "auto" UI scale makes everything too small to be comfortable at 720x480. Steam sizes its UI from the X screen in pixels, so a smaller X screen (via `swaymsg output DSI-1 scale S`) gives bigger elements:
scale 1 (720x480): about 6-7 tiles across, small text; scale 1.5 (480x320): clearly larger but soft (1.5x cannot map pixels evenly); **scale 2 (360x240): large, crisp, readable, about 3 tiles across**, and a quarter of the pixels to render. Judged from screenshots by me; the operator was shown scale 2 live afterwards.
Steam also has its own UI scale setting (labelled "auto" in its settings): not yet examined, and which is better (a fixed Steam setting or an output scale) is undecided.

**Operator's chosen UI scale (2026-10-09 00:29).** The operator adjusted Steam's own UI scale to taste; this is the value to keep. Steam stores it in `config/config.vdf` under `InstallConfigStore/UI/display`: **`ScaleFactor` = 0.842373907566070557**
(with `Current`: `MinScaleFactor` 0.2905, `MaxScaleFactor` 1.3920, `AutoScaleFactor` 0.5809 for this 720x480 screen). So the chosen scale is 1.45 x the "auto" value, at **output scale 1 (the full 720x480)**.
My earlier output-scale-2 experiment is NOT the operator's choice: it was reverted to 1 by a revert timer of mine, and the operator did not ask for it. To keep the setting on new installs, `ScaleFactor` has to be seeded into `config.vdf` (relative to the auto value, 1.45x, because the absolute number is only right for this screen); not built; on this device Steam keeps it across restarts.

## 3c. Does half resolution (output scale 2) reduce GPU load? (2026-10-09 02:00, `os/dev/diag/gpuab.py`, A/B/A, 30 s per phase, Steam UI home screen)

- **Idle UI (no input):** nothing to reduce: GPU clock 420 MHz (its lowest) for 100% of samples at scale 1, 2 and 1; graphics process, Xwayland and sway at about 0% CPU; system 7% busy.
- **UI animating (a virtual pad pressing down/up once a second):**
  scale 1: GPU clock mean **521 MHz** (45% of samples at 600 MHz, 33% at 420) and, in the repeat run, **539 MHz** (56% at 600 MHz);
  **scale 2: 420 MHz for 100% of samples** (the governor never left its lowest clock). **So half resolution clearly lowers the GPU load.**
  CPU did NOT drop: Steam's GPU process 60 / 57 / 56% of a core, other web helpers 79 / 77 / 52%, Xwayland 6 / 5 / 5, sway 5 / 4 / 4, steam 20 / 19 / 21, system busy 54 / 50 / 45% (the two scale-1 runs differ by as much as scale 1 vs 2: within noise).
  So UI navigation is CPU-bound (Chromium on the A53), not GPU-bound; scale 2 would mainly save GPU power/heat. Power and temperature were not measured.
- **Caveats:** GPU clock is a proxy (the ondemand governor's choice), not a load counter; no frame rate or latency was measured; at scale 2 Steam laid the UI out bigger (fewer tiles), so the animation was not identical. Not tested with a game running (BIT.TRIP RUNNER's slow menus use about 2 cores and the GPU driver only about 1% of CPU, so they are unlikely to be fill-rate bound; untested).

## 3d. Operator feedback on half resolution, and a correction (2026-10-09 02:10)

- At output scale 2 the operator reports the UI **"feels more responsive"** (subjective; the CPU measurements in 3c show no CPU change, only less GPU work, so the cause of the feel is not established). The look: **nearest-neighbour upscaling shows heavy aliasing** (exact 2x2 blocks of a UI rendered at only 360x240, where a glyph is a few pixels tall). Linear filtering (`swaymsg output DSI-1 scale_filter linear`) was switched on live for comparison; the choice is pending. sway also has `smart` (nearest at integer scales, linear otherwise).
- **Correction to my earlier suggestion:** Steam's quick-access Scaling Mode/Filter sliders apply to the GAME's picture only (gamescope scales the game layer; Steam's own UI is drawn at native resolution on top). They are not a control for how the UI looks. sway's `scale_filter` is per OUTPUT, so one setting affects both the UI and games; there is no per-window filter. Consequences:
  the UI's look/size must be our own setting (output scale + filter, and Steam's own UI `ScaleFactor`); a game-only filter would need a different mechanism, for example switching the output scale/filter when a `steam_app_*` window gains or loses focus (sway window events), so the UI could stay at half resolution while a game runs at native resolution. Not built.

## 4. What this means for the design

- Refresh and frame rate must be chosen together: pick a refresh in 31-60 Hz and run content at that rate or a whole divisor of it (60: 10, 12, 15, 20, 30, 60; 50: 10, 25, 50; 48: 12, 16, 24, 48; 45: 15, 45; 40: 10, 20, 40; 36: 12, 18, 36; 31: 31).
- Quick-access-menu slider: Steam's gamescope interface has `active_display_info(valid_refresh_rates[])` (slider range) and `set_app_target_refresh_cycle(fps, flags)`; a small WispOS stand-in can answer them and apply the mode change above (notes section 5.2). Not built; Steam's behaviour without real gamescope is unobserved.

## 5. Open items (also in `WISPOS-NOTES.md` section 5b)

1. A frame limiter/pacer for Steam's UI and for games so frame rate = Hz or a divisor.
2. The gamescope stand-in service (slider range and requests).
3. Scale steps and the real Steam UI at 1.5x/2x/3x judged by eye; CPU while navigating at each scale.
4. Rates above 60 Hz; power effect of lower refresh.
5. Put the dev tools in the dev image build (they currently live under `os/dev/diag` and `/storage/wispos/dev`, started by the `030-dev-safety` autostart hook).

## 6. Per-game resolution through Steam's own settings (operator question, 2026-10-09)
**Observed need:** Worms Armageddon (640x480 window) is cropped to the top-left 360x240 at the half-resolution default and fits at native scale 1 (screenshots, 2026-10-09 06:10); a 1990s game wants the native output while the UI is happier at half. Output scale is per OUTPUT in sway, so something has to switch it when a game gains or loses focus.
**Design (operator's idea: use SteamOS's built-in per-game settings and let sway honour them, so the user picks what fits):**
- Steam already stores per-game profile values (`config.vdf` `InstallConfigStore/.../Perf/GameProfiles/App/<appid>`, numeric keys, meaning not decoded) and, on SteamOS, talks to the compositor over the gamescope interface (`gamescope_control`: refresh/frame-limit events; for an external display the root properties `GAMESCOPE_DISPLAY_MODE_LIST_EXTERNAL` and the `GAMESCOPE_MODE_SAVE_FILE` + `GAMESCOPE_DISPLAY_MODE_NUDGE` hand-shake, sections 4 and `os/dev/diag/gsmodes.py`).
- A small compositor-side stand-in (dev prototypes exist: `gsstub.py`, `gsmodes.py`, `applyhz.py`) would answer that interface and apply the user's choice to sway: on focus of a `steam_app_<id>` window, set the output scale (1 native, 2 half), filter and refresh from the saved choice for that app; on return to the UI, restore the UI's own setting. The user changes it where SteamOS users expect to (Steam's per-game and quick-access settings), not in a WispOS-specific screen.
- **Unverified (must be observed on the device before building):** whether Steam shows a per-game Resolution/scaling option with only this stand-in (the Display Resolution option may only appear for "external" displays; the per-game scaling options may be gamescope-only), and what Steam writes for a per-game choice. If it shows none, the fallback is a WispOS-owned per-app table keyed by appid, kept deliberately small, and the Steam setting stays the preferred path.
- Input is unrelated to this: mapping stays Steam Input's (`memory: controller-mapping-is-steam-input`).

### 6b. Can a game be made to fit the half-resolution screen without game-specific config? (operator, 2026-10-09: "the goal is UX consistency")
- **Stated goal:** one resolution and look across the UI and games, chosen by the user; no visible switch between UI and game.
- **Worms Armageddon has no sub-640x480 mode** (sources: worms2d.info readme/Windows 8 pages list 640x480, 800x600, 1024x768, and beta updates add any OS-reported mode; I found no page saying "nothing lower", so that is an absence of evidence). On a 360x240 X screen it cannot go fullscreen: with output scale 2 it printed "Unable to initialise graphics using the required video mode... try windowed mode?" (or runs as a cropped 640x480 window).
- **sway cannot shrink an X11 window.** The generic way to show a bigger game on a smaller logical screen is a compositor with its own virtual screen (gamescope: needs Vulkan, which this Mali-G31 build lacks) or Wine's Wayland driver (the game's mode is emulated and the compositor scales the surface). **`PROTON_ENABLE_WAYLAND=1` was tried on 2026-10-09: no effect, the game stayed an `xwayland` window; this `Proton 11.0 (ARM64)` ships only `winex11` (no `winewayland.so/.drv`).**
- Options for consistency, none built: (A) everything at native 720x480 (the UI at Steam's own UI scale, the operator's original 0.84 setting; games fit as-is; loses only the GPU-clock saving of half-res, which measured 0 CPU effect); (B) everything at half-res, with Wine rebuilt with the Wayland driver in our own overlay build (the overlay is already a custom ARMv8.0 build of Wine: whether wayland/xkbcommon sysroot libs are available for it is unknown) so games larger than 360x240 are scaled down by sway; (C) a visible per-game output-scale switch on focus (not consistent, but small). Not decided.
