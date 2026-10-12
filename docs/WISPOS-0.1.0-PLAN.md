# WispOS 0.1.0: what is left, in what order, and what "ready to flash" means

Started 2026-10-12 on the operator's instruction "Work on everything needed for v0.1.0, let me know when the image is ready for flashing". This file is the working list; it is updated as items finish (each line says what proves it). Authority for scope: docs/WISPOS-OS-DESIGN.md "Remaining efforts before 0.1.0" and its "Decided (operator, 2026-10-12)" paragraph; for the look: docs/WISPOS-UI-LANGUAGE.md "Desktop mode" (LOCKED).

## In scope (operator decisions)
Game mode as it is (Steam gamepad UI, compatibility layer, update screen) + the boot splash and first-run setup; **OS updates** that work end to end; **desktop mode**: switch to desktop and back, dock, application menu, quick settings (SSH and USB toggles), terminal, one browser, the pad as pointer and keyboard (`wisp-pad`), the on-screen keyboard, the **downloads handler**; hardening; release mechanics (version, changelog, tag, reproducible build). Out: Flatpak (after 0.1.0), file-management suite, content-aware cursor snapping, anything marked "later" in the design doc.

## "Ready to flash" means
1. `os/build-image.sh` produces the release image and the dev image from a clean checkout of one commit, both pass `os/verify_image.py` and `os/check_rootfs.py`, and a second build gives the same hashes.
2. Every in-scope feature is built and tested to the extent the host and QEMU allow (unit tests, a headless-sway run with screenshots against the locked pictures, the QEMU boot test); what only the device can show is listed in the device checklist below, never claimed.
3. Game mode still boots exactly as before (the desktop pieces are additive; a failure in them falls back to Game mode).
4. Versioning: `manifest/lockfile.json` says 0.1.0, `CHANGELOG.md` has the 0.1.0 section, the release gate reports no unexplained gaps (the explained ones are listed in this file).
5. The operator is told, with the image path, hash and the checklist. I never flash.

## Tracks and status (update as items land)
**A. Desktop shell (`wisp-dock`: dock, menu, quick settings, popups, toasts, keyboard)**
- [x] A1 model (desktop entries, pins, windows, dock layout, menu logic): `os/dock/model.[ch]`, `os/test_dock_model.py`
- [x] A2 software renderer (FreeType + libpng, no Wayland) that reproduces the locked pictures: pixel comparison against `os/dock/design/*.png`
- [x] A3 Wayland shell (layer-shell, foreign-toplevel, pointer and keyboard input, launching, control socket): QEMU run under headless sway with screenshots
- [x] A4 icon set (PNG, drawn from the mockup glyphs), `.desktop` files for our apps, wallpaper asset
**B. `wisp-pad`** (pad as pointer and keyboard, layers, hysteresis): logic tests, uinput I/O, service. **DONE (host + qemu-user; commits in the log).**
**C. Session switching**: `wisp-session-select`, `steamos-session-select`, desktop service, sway runtime setup, fail-safe back to Game mode, QEMU test. **DONE (host tests; headless sway under qemu-user).**
**D. Browser**: first-use download (Firefox ESR, official aarch64), profile policy and prefs, start page, launcher, downloads folder. **DONE (host tests; the browser itself only runs on the device).**
**E. Downloads handler**: classification (AppImage, archives, Windows files), toast actions, `.desktop` writer, extraction. **DONE (host tests).**
**F. Updates end to end**: **REDESIGNED 2026-10-12 as two boot slots** (docs/WISPOS-UPDATE-DESIGN.md "Amendment": the rename plan had a moment with no KERNEL on the card). Built: `wisp-update apply/confirm/status` (slot-aware, commit = trial marker then one letter), the initramfs step `os/boot/wisp-boot.sh` (trial count, rollback, `FAILED-<id>`), `wisp-update-confirm.service`, `steamos-reboot`, the two-label `extlinux.conf`, `verify_image.py` checks. Tested: `test_wisp_update.py`, `test_boot_slots.py` (three shells incl. the initramfs's own busybox, a stop at every sync, mutants), `test_initramfs.py`, `test_update_confirm.py`; the real U-Boot code (qemu_arm64 build of our patched tree) follows the two labels, the nested path, the one-byte flip and the fallback. QEMU end to end: `os/qemu-update-test.sh`.
**G. Release mechanics**: version authority, changelog section, tag procedure, reproducibility check, public tree sync, release notes. **DONE except the public-tree sync and the tag:** `os/build-all.sh`, `os/repro-check.sh` (reproducible, proved on the final candidate; it found and fixed a directory name in the kernel's embedded config), `docs/WISPOS-RELEASE-NOTES-0.1.0.md`. The tag waits for the operator's device test.
**H. Loose ends**: UI memory ceiling default (DONE: `--freeze`, enabled, off switch), ROCKNIX leftovers (DONE: ACCEPTED in the release gate with reasons), setup screen symbols page (DONE: `os/setup/kb.h`), `--intro` hand-over (DONE) and the Wayland boot phase (`wisp-bootscreen`, DONE; the operator's splash requirement needed it), device checklist (docs/WISPOS-NOTES.md "Next device session")

## Decisions I am taking so the work can proceed (operator can reverse any)
- **Browser: Firefox ESR**, downloaded on first use from Mozilla (official aarch64 build, never shipped in the image), configured by `policies.json` and a profile `user.js`. ESR because it honours `xpinstall.signatures.required=false`, which our unsigned extension needs; the extension itself is not in the first image (the browser layer works without it).
- **Apps run as root**, like the rest of the session (ROCKNIX heritage; the Wayland socket and the runtime directory belong to root). A downloaded AppImage therefore runs as root. Dropping privileges is a post-0.1.0 hardening item; it is recorded as a residual risk.
- **Desktop mode runs at the panel's native 720x480** (`wisp-display native`); Game mode keeps the half-resolution scale.
- **No auto-show of the on-screen keyboard in the first image** (X shows and hides it, as locked); auto-show needs the input-method protocol and comes later.
- **ROCKNIX leftovers stay where removing them cannot be verified without the device** (volume and brightness keys, audio and display bring-up, power handling): `rocknix-autostart` and `input.service` stay for 0.1.0, listed as explained gaps in the release gate; what is clearly dead weight is removed.
- **`wisp-ui-reclaim` with `--freeze` is enabled by default** in the release image (the only combination that measured stable; its first real test is the operator's flash). It thaws on any doubt.

## Device checklist (what only the handheld can show; the operator runs it after flashing)
Written at the end of the work (docs/WISPOS-NOTES.md "Next device session"): splash and SELECT, setup screen, Steam UI and the update screen, Switch to Desktop and back, the pad layers, dock and keyboard, browser start, a download and its toast, USB `computer` mode, two unrelated games, memory with the reclaim daemon, power and rollback of an update.

## Residual risks stated up front
Nothing from this stretch has run on the hardware; the Wayland shell is verified only under a headless compositor in QEMU; the pad's real axes and buttons are read from the device tree, not the device; USB host mode ships behind the bench-test gate (not enabled for a release until the meter test is done).
