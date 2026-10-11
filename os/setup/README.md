# wisp-setup: the forced first-run Wi-Fi screen

Before Steam can be downloaded the device must be online, and the handheld has no keyboard. So the first boot shows
this screen and does not go away until the device is connected and Steam's servers are reachable. Then it hands over
to the Steam download, and then to Steam's own sign-in.

The flow (operator's specification, 2026-10-07):

1. A list of networks, scanned continuously, picked with the D-pad.
2. A password field that opens an on-screen keyboard driven by the gamepad.
3. Connecting, then a check that Steam can really be reached (a joined network with no internet is not enough).
4. Download Steam, then sign in (Steam's own UI).

## What exists

`wisp-setup.c` is SDL2 + SDL2_ttf + SDL2_image (all in the image) and currently implements the screens and a demo mode:

    wisp-setup --demo list|password|connecting|checking|failed|nointernet|download --out frame.bmp [--badge PNG] [--fonts DIR]

The look follows Steam's own UI, read from its stylesheets: ground `#0e141b`, surface `#23262e`, accent `#1a9fff`, focus as a white fill with dark text, 2 px corners, Steam's blue gradient on progress. Steam names Motiva Sans but does not ship it, so on this device it falls back to Liberation Sans, which this screen uses too.

`design/*.png` are renders of every state, made by running this program in the emulated VM (`os/qemu-vm.sh`) with the dummy
video driver, so they are what the device will draw (720x480). `assets/wispos-badge.png` is the badge, loaded from a
file so it can be swapped for the final artwork without touching the code.

## Build and render

    # headers only from SDL2, SDL2_ttf and SDL2_image releases (hash-pinned in the build); libraries are the image's own
    aarch64-linux-gnu-gcc -std=gnu11 -O2 -Wall -Wextra -I<headers> -o wisp-setup wisp-setup.c \
        <rootfs>/usr/lib/libSDL2-2.0.so.0 <rootfs>/usr/lib/libSDL2_ttf-2.0.so.0 <rootfs>/usr/lib/libSDL2_image-2.0.so.0 -lm
    os/qemu-vm.sh start; os/qemu-vm.sh put wisp-setup /tmp/; os/qemu-vm.sh ssh 'SDL_VIDEODRIVER=dummy /tmp/wisp-setup --demo list ...'

(SDL programs crash at start under user-mode QEMU, an emulator limitation; they run correctly in the full-system VM.)

## What works now (tested)

The program is real, not just a demo. `nm.h` (no SDL) holds the nmcli side and is unit-tested natively (`os/test_setup.py`);
`flow-test.sh` drives the real binary in the emulated VM against a fake `nmcli`/`curl` and checks 13 things: right and wrong
password (and that a failed attempt's saved profile is deleted), an open network, a captive portal (joins, but the Steam check
fails, so it says so), a hidden network (name, then password, `hidden yes`), no Wi-Fi adapter, "already online" (exits at once),
passwords under 8 characters refused, and the on-screen keyboard (movement, typing, one-shot shift, delete, back).

- Scans continuously in the background (every few seconds, or on X); the cursor stays on the same network across refreshes.
- Never uses a shell: the SSID and password are separate arguments, and a test proves shell syntax in them is inert.
- "Online" means Steam's client manifest returns 200 and starts with `"linuxarm64"`; a captive portal's HTML page does not pass.
- Exit code 0 only when that is true. Runs as `wisp-setup.service`, before `wisp-steam.service`, restarting if it ever crashes.
- Input: game controller (SDL names; A is the south button), plain-joystick fallback, keyboard for testing.

## Not verified on the device

- Which physical buttons the RG34XXSP's pad reports (the mapping comes from ROCKNIX's controller database, which the unit points at).
- Starting under Sway: the unit asks for `SDL_VIDEODRIVER=wayland,x11`; only the dummy driver has been exercised.
- The real `nmcli` output and error texts (the parsing follows nmcli's documented terse format and the common error strings).

## Still to build

- The Steam download step behind this screen, then Steam's own sign-in handoff.
- Captive-portal sign-in (it is detected and explained, not completed).
- Open networks that ask for a login page, and enterprise (802.1X) networks.
