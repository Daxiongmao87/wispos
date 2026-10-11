# Moving the live device changes into the image (written 2026-10-09; update as each line closes)

Many things were changed live on the dev handheld. A change counts as DONE only when it is in the repo, has a test, is in a rebuilt image, and has been seen working on the handheld from a fresh flash. Last images built: tree 998c8fa (release `dc4f71a9...`, dev `c4ab31c3...`), never flashed.

## A. In the image source already, not yet in a built image (rebuild + flash needed)
| Change | Where | Test |
|---|---|---|
| Steam on a fresh Xwayland (fixes the black/stalled UI) | `os/rootfs/usr/bin/wisp-steam-session` | yes; in the 998c8fa images |
| Wi-Fi never switched off at boot | `os/rootfs/usr/lib/autostart/common/080-network` | yes; in the 998c8fa images |
| Games fullscreen, no title bar | `os/rootfs/usr/share/sway/config.kiosk` | yes (6bd428f) |
| WineD3D + `WINEDEBUG=-all` as game defaults | `wisp-steam-session` | yes (4415a78) |
| Dev tools as services: flight recorder, memory watchdog, pad-triggered panel test (SELECT+START), diagnostic tools under `/usr/lib/wispos-dev` | `os/rootfs-dev`, `install_dev` | yes (dev images only; release must not contain them) |

## B. Device only: each needs a design decision and an implementation
| Live change | What the image needs |
|---|---|
| Gamescope stand-in (`os/dev/diag/gsstub.py`) + `GAMESCOPE_WAYLAND_DISPLAY` + Steam flags `-steamos3 -steamdeck` (drop-in `20-gs.conf`, `session-gs.sh`) | a real service started before Steam (language/memory cost to decide), the launcher passing the flags and env, tests; decide whether SteamOS mode is the default |
| Refresh applied from the Frame Limit slider (`applyhz.py`) | folded into the stand-in as `--apply`; plus the missing frame limiter |
| Output scale 2 + linear filter (sway config lines) | pending the operator's choice; would go in `config.kiosk` (ROCKNIX's `111-sway-init` appends output lines to it) |
| Steam UI scale (`config.vdf` `InstallConfigStore/UI/display/ScaleFactor` = 1.45 x auto), default compatibility tool (`CompatToolMapping` `"0"` = proton-stable), shader pre-caching off | seeded by the first-run step while Steam is not running; not built; format of the global tool entry still to be read from a config the operator changed |
| Pad visible to games (`PRESSURE_VESSEL_FILESYSTEMS_RO=/run/udev`) | unresolved: possibly linked to a black Steam start; needs 8+ starts before making it a default |
| Dev tools: `paneltest.py`/`kmsflip.py`, `gpuab.py`, `hudtest.sh`, `gamelaunch.sh`, `kill-game.sh`, watchdog, blackbox | dev image only: staged under `os/rootfs-dev`, started by units instead of the card's `030-dev-safety` hook, with `check_rootfs`/`test_dev` entries |
| zram: image still 768 MiB lz4; `zram1` hot-added and the swapfile removal are device only | sweet spot not found; image unchanged |

## B2. Runtime options to add to the image (operator request 2026-10-09: "ARM64 DOSBox and whatever alternative we used for Bit Trip Runner")
| Runtime | What it is today | What the image needs | Open |
|---|---|---|---|
| **Native ARM64 DOSBox (DOS games)** | not on the device. Doom's classic build runs the Windows x86 DOSBox Staging 0.81.2 through Proton + FEX: double emulation, DOSBox pinned at 99% of one core, frame-time p90 of 97-123 ms (2026-10-09). Official DOSBox Staging Linux releases are x86_64 only ([dosbox-staging.org/releases/linux](https://dosbox-staging.org/releases/linux)); aarch64 packages exist in distros (Chimera, Nix) but not glibc/our rootfs builds. ROCKNIX's sources only carry the libretro cores `libretro-dosbox-pure` and `libretro-dosbox` (RetroArch is pruned from our image). | build DOSBox Staging for aarch64 against our rootfs (cross-compile with a sysroot assembled from pinned arm64 packages; or a build inside an emulated arm64 VM), pin source + checksums, stage it under `/usr/lib/wispos/dosbox`, and register it so a DOS game can pick it (a Steam compatibility tool in the style of Boxtron, or a launch wrapper) | how Steam lets a game choose it: notes 3.5/128 record that a big custom tool in `compatibilitytools.d` hung the UI (a small tool is untested); SDL2/glibc versions on the device; sound and pad mapping; Doom's classic `Launcher.exe` hardcodes `-conf doom.conf -conf doom_single.conf` |
| **Windows runtime = Proton ARM64 + ARMv8.0 overlay + WineD3D** (what BIT.TRIP RUNNER runs on) | stock `Proton 11.0 (ARM64)` downloaded by Steam, then the community overlay `Azkali/proton-arm64-a57` release `11.0-1-beta5-v80` (`proton11-arm64-v80-a57.tar.zst`, 824 files) applied by hand over its `files/`; defaults now `PROTON_USE_WINED3D=1`, `WINEDEBUG=-all` (source only) | a first-run/each-start step that checks the overlay is present and re-applies it after Steam downloads or updates Proton (pinned URL + sha256, originals kept, never redistributed inside the image); make Proton Stable the global default tool | the overlay is an unpinned community binary; Steam updating Proton will overwrite it; version mismatch (overlay 11.0-1-beta5 on Valve 11.0-2) worked but is untested elsewhere |
| Native Linux games | nothing needed beyond Steam's own runtime | none | unmeasured |

## C. Stopgaps to delete from the card after flashing a rebuilt image
`/storage/.config/autostart/010-wifi-on`, `020-fresh-xwayland`, `030-dev-safety`, `040-games-fullscreen`; `/run/systemd/system/wisp-steam.service.d/*.conf` (rebuilt at boot by the hooks); the sway config lines; `session-fresh.sh`/`session-gs.sh`.

## D. Order of work
1. For each line in B: write the test first (red), implement, changelog entry. 2. Rebuild release and dev images with `os/build-rootfs.sh` / `os/build-image.sh`; run `check_rootfs`, `verify_image`, `qemu-test.sh`, `dev-test.sh`, and the baseline checker (`/mnt/hdd/projects/active/wispos-proton-a57/baseline/check.sh`).
3. Back up the card (`os/dev/card-backup.sh`), flash the dev image, delete the stopgaps. 4. On the handheld from that fresh flash: several boots (Wi-Fi, 8+ Steam starts), a game with NO launch options (fullscreen, WineD3D, pad), the stand-in connecting, the quick-access slider, a Doom run. 5. Record in the notes and move rows from B to A.
Unverified until step 4: everything above works from a fresh flash (first boots differ from later ones).

**Native ARM64 DOSBox: feasibility look (2026-10-09, nothing built).** DOSBox Staging v0.82.2 source (tag `f8c24f8`) is cloned at `/mnt/hdd/projects/active/wispos-dosbox/src`. Its meson build vendors most dependencies as wraps (iir, libpng, speexdsp, sdl2_net, zlib-ng via cmake, optionally slirp/fluidsynth/mt32emu), uses SDL2 (our rootfs has SDL2 2.32.10; headers staged in `rootfs/sdlinc`), opus, and optional OpenGL (`use_opengl=false` avoids desktop GL).
The rootfs has glibc 2.41; the host cross compiler is aarch64-linux-gnu-gcc 13.3 (glibc 2.39 sysroot: binaries run on 2.41; link libstdc++/libgcc statically). Still to find out: a meson cross file and the rootfs SDL2 (and opus) as link targets, how the wraps behave offline/cross, and whether the DOSBox dynamic core works well on the A53.

**Resolution choice in Steam's display settings (operator request 2026-10-09: half resolution by default, native 720x480 selectable).** Default: done in source (`config.kiosk`: `output DSI-1 scale 2`, linear). The choice: `os/dev/diag/gsmodes.py` (host-tested logic; the X11 part untried) follows gamescope's own contract, read from its source (`steamcompmgr.cpp update_mode_atoms`, `DRMBackend.cpp get_saved_mode`):
it publishes `GAMESCOPE_DISPLAY_IS_EXTERNAL=1` and `GAMESCOPE_DISPLAY_MODE_LIST_EXTERNAL="720x480@60 360x240@60"` on the X root window; Steam saves the chosen mode in the file named by `GAMESCOPE_MODE_SAVE_FILE` (line `description:WxH@R N`) and sets `GAMESCOPE_DISPLAY_MODE_NUDGE`; the piece re-reads the file and sets the sway output scale (1 or 2).
To try on the device: start Steam with `GAMESCOPE_MODE_SAVE_FILE=/storage/wispos/config/gamescope-modes` in its environment, run `gsmodes.py` next to the stand-in, open Steam's Display settings. Unknown until seen: whether Steam shows a Resolution option for a display it is told is external while `active_display_info` says internal (the stand-in sends the internal flag), which description it writes, and whether it restarts anything on change.
