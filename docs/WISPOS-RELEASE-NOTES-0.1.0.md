# WispOS 0.1.0: release notes (CANDIDATE, build 20261012.1)

**Status: a candidate.** Built from source and tested on the host and in QEMU. **Nothing in it has run on the handheld yet.** It is not released until the operator has flashed it, gone through the device checklist (docs/WISPOS-NOTES.md, "Next device session") and the `v0.1.0` tag is cut (docs/WISPOS-RELEASING.md). Nobody but the operator flashes.

## What it is
A single-purpose Steam appliance for the Anbernic RG34XX-SP (Allwinner H700, 1 GiB, 720x480): ROCKNIX's pinned userland and kernel recipe reduced to what a Steam gamepad session needs, with WispOS's own boot screens, first-run setup, two-slot OS updates and a desktop mode.

## What is in
- **Game mode:** Steam's gamepad UI and games through the stock compatibility tools (no custom FEX builds); the Steam UI's memory ceiling with freeze while a game runs (on by default, `touch /storage/.config/wispos/ui-reclaim-off` turns it off); the screen blanks when idle.
- **Boot and first run:** a boot splash from the first moment the kernel can draw, kept up through the compositor start by `wisp-bootscreen`, then the first-run Wi-Fi and Steam-download screens with a 3 s hand-over; SELECT shows the boot log. The setup keyboard can type every printable ASCII character.
- **OS updates:** the card holds two systems (slots a and b). An update is written into the other slot, verified (ed25519-signed manifest, sha256 of every file, read back from the card), and switched with one byte. The new version is on trial: if it does not come up well it is replaced by the previous one. **No hands needed** when the failure is visible to the initramfs (system will not mount / has no init: it restarts by itself; `panic=10`; not healthy after 10 minutes: restart). **A kernel that dies before its initramfs needs the person to power-cycle twice**; U-Boot counts those starts and the third starts the previous version. A bad build is remembered and not offered again.
- **Desktop mode:** switch from Steam's power menu; dock, application menu, quick settings, on-screen keyboard, terminal, one browser (Firefox ESR, downloaded on first use), downloads handler, the pad as pointer and keyboard (every action also has a keyboard and mouse path). Falls back to Game mode if the shell cannot stay up.
- **Hardening:** SSH is off by default (persistent toggle in quick settings; on by default on dev images); root has no password; no always-on shell; the release gate (`os/check_release.py`) reports the profile. Two items are the operator's decision (below).
- **USB-C port:** one three-way setting (off / computer / host), off at every boot.
- **Build:** `os/build-all.sh` builds everything in order; `os/repro-check.sh` proves reproducibility.

## What is not in 0.1.0
- Flatpak or any other package manager (after 0.1.0).
- Published updates: no release has been published and no update channel is live; `os/make-release.py` still refuses to produce update assets until the USB host-mode bench test is recorded (release gate).
- Anything that depends on the device and could not be tried here (list below).

## Operator decisions still open
1. The `wisp` account has the publicly known password `wisp`; reachable on the console, and over SSH only while SSH is turned on.
2. `PasswordAuthentication yes` while SSH is on.
3. The USB host-mode bench test (USB-C meter, current-limited source; never a wall PD charger) gates release assets.

## Tested on the host / in QEMU, not on the device
Unit and integration suites (`os/run-tests.py`), mutation-checked rules (each rule changed in turn must fail a test), a headless sway under qemu-user for the shells, the real U-Boot code in QEMU for the label logic, U-Boot's own command interpreter for the boot-trial script, the real initramfs and busybox for the boot steps, power cuts at every sync. Evidence and numbers: docs/WISPOS-NOTES.md, docs/WISPOS-TIMINGS.md.

## What only the handheld can show (residual risks)
- The H700 boot itself with the two-label `extlinux.conf`, and U-Boot's `fatwrite` to the SD card from the boot-trial hook.
- Steam's Restart button: which helper it calls (`steamos-reboot` is provided; the checklist says how to find out if it differs).
- Stick axes and polarity of the pad (read from the device tree, not the device); the freeze policy on an aged session and a second game; the browser's memory in desktop mode.
- SD card behaviour on a power cut at the sector level (assumed atomic, as for every FAT update).
- A kernel that stops before U-Boot's script runs (boot ROM, SPL, DRAM) is not part of an update.

## How to flash
Image: `out/wispos-rg34xxsp.img` in the work tree (1,124,073,472 bytes); the dev image `wispos-rg34xxsp-dev.img` additionally has the builder's SSH key and announces itself to the builder's machine: never publish it. Release image sha256 `edc4149e9a767d4adb71ed0eba92939d669711a2e31e7d57d6a42b83e5c49b21` (dev image `7372648107cc95ff48447c7e5f7f5107626933a0263cd30e63824443946504db`; all artifacts in docs/WISPOS-NOTES.md). Check the hash first, then write the **whole image** to the SD card with the tool you trust, choosing the card's device with care: everything on the card is replaced. The first boot grows the STORAGE partition to the card.

## How to get back
- Card in any computer: the first line of `extlinux/extlinux.conf` is `DEFAULT a` or `DEFAULT b`; change the letter to start the other slot. `wispos/README.txt` on the card says so.
- Re-flash the previous image (keep it).
