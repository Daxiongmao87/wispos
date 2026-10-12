# WispOS as a single-purpose OS: design and plan

Recorded 2026-10-07. Operator intent: a very light "bazzite for the RG34XXSP": boots
into Steam Big Picture ("GameMode UI"), can switch to a lightweight gamepad-friendly
desktop and back, supports all the device's hardware, can add non-Steam games, and
ships with SSH, the `wisp` user and `vi`. This document records what the evidence
supports, the decisions taken, the open unknowns, and the build phases. Nothing here
has run on the handheld yet (it was unreachable when this was written); every claim
below is labelled as measured, documented, or untested.

## Principle: WispOS is its own OS, built from ROCKNIX's open recipes

Revised 2026-10-07 at the operator's direction: no overlay, no fallback to the ROCKNIX
frontend. ROCKNIX is open source and its H700 boot chain is fully described by recipes
(U-Boot with its DRAM and PMIC patches and defconfig, Arm Trusted Firmware, the kernel
configuration and device trees, the initramfs). WispOS builds each stage from source with
those recipes and replaces everything above them with its own userland. The earlier failed
boot-chain attempts improvised instead of following these recipes; following them is the
method. Each stage is verified before the next is built on it:

1. **Bootloader**: U-Boot v2026.01 + BL31 built from ROCKNIX's recipe. **Done**:
   `os/boot/build-uboot.sh`, hash-pinned sources, reproducible, structurally equal to the
   bootloader on the handheld (not yet booted).
2. **Kernel + initramfs + device trees**: from ROCKNIX's `linux.aarch64.conf`, patches and
   `dts`. Next.
3. **Root filesystem**: WispOS's own (`os/rootfs/` baked in), starting from the same
   userland binaries ROCKNIX uses (no apt on this host) pruned to what the single purpose needs.
4. **Image**: assembled and written to the card; booted on the handheld.

A full from-source rebuild of every ROCKNIX userland package would need its Docker/apt build
environment, which `HOST-SAFETY.md` forbids on this host without explicit per-operation approval,
so userland pieces are fetched as hash-pinned binaries or built with the host's existing cross tools.

## Requirements, decisions and evidence

| Want | Decision | Evidence / status |
| --- | --- | --- |
| Boot into Steam BPM | `wisp-steam.service` is the one session in the WispOS rootfs; no other frontend | Measured: Steam BPM signs in and runs on the Sway/Xwayland stack. Unit written, not yet in an image |
| GameMode / gamescope | **Not first.** Provide the GameMode *experience* (session switching, update flow) with a SteamOS-compatibility layer on Sway; investigate gamescope later | gamescope needs Vulkan. Mesa docs: PanVK covers "Bifrost and later" but where support is experimental "the driver refuses to load by default" (`PAN_I_WANT_A_BROKEN_VULKAN_DRIVER=1`); ROCKNIX's Mesa 26.2.2 on the device has **no Vulkan driver** (no ICD files). Software Vulkan (SwiftShader) is too slow to composite on four A53 cores (not measured) |
| "Switch to Desktop" in the UI | Implement the SteamOS integration points Steam already calls | Steam's binaries contain a `SteamOSManager` client (`SwitchToDesktop`, `SetDefaultDesktopSession`, `ApplyMandatoryUpdate`), call `/usr/bin/steamos-polkit-helpers/*`, `steamos-update`, `login1` power calls; community reports the menu calls `steamos-session-select`. **Unknown:** what makes Steam use the real controller instead of its stub, and the exact UI condition. To be found empirically on the device with screenshots |
| Lightweight desktop mode | Sway (already resident, about 27 MB) + a gamepad-first shell: left stick as mouse via a small `uinput` daemon, on-screen keyboard, simple launcher menu; "Return to GameMode" entry | Design only. SDL3 ships in Steam's runtime, so a small SDL3 shell has no new dependencies; `wvkbd` is a candidate keyboard. Not built |
| Non-Steam games | Desktop-mode Steam (the same `steam` binary without `-gamepadui`) with "Add a Non-Steam Game"; native ARM64 programs run directly | Same client binary, so feasible; memory about the same as BPM, so only one mode runs at a time |
| Windows games (incl. pre-2005) | **Proton, via Steam's own compatibility-tool mechanism**, not box86/`wine` (those are ROCKNIX's emulation stack and are dropped from the base) | Steam's ARM64 client contains the compatibility-tool manager (list/specify/register tool) and the "Steam Play" settings, so a tool registered in `compatibilitytools.d` should appear in the compatibility window, globally and per game (to verify on the device). **Unknown:** which tools Steam offers on ARM64, and whether Proton runs on this ARMv8.0 CPU: Valve's ARM64 Proton source targets ARMv8.2-A (repo plan, section 5) and needs a rebuild or the SIGILL fallback; FEX speed on four A53 cores is unmeasured. Proton defaults to DXVK (Vulkan, which this device lacks); old DirectX 9-and-earlier titles can use `PROTON_USE_WINED3D=1` over OpenGL (panfrost exposes GL 3.1 / GLES 3.1 per Mesa's docs). Untested |
| All hardware supported | Build ROCKNIX's kernel (7.2 + their 41 patches and config), embedded firmware (Wi-Fi, Bluetooth, display panels), device trees and out-of-tree drivers from source; keep its device services | Recorded drivers include `rtw88_8821cs` Wi-Fi, RTL8821CS Bluetooth, panfrost (Mali-G31), AXP717 power; services `batteryplus`, `powerstate`, `input`, `led-poweroff`, `hdmi-hotplug`. A hardware matrix to verify on the device is below |
| SSH + `wisp` user | `wisp` account (uid 1000, password `wisp`, the operator's choice) baked into the image's `/etc` by `os/accounts.py`; SSH always on (own `sshd` unit without ROCKNIX's condition), password login allowed, root by SSH key only | `/etc/passwd` is in the read-only image and `/etc/shadow` is a runtime symlink in ROCKNIX; WispOS ships real files. Written and tested offline (`os/test_rootfs.py`) |
| `vi` | Keep BusyBox `vi` | `/usr/bin/vi` is present in the ROCKNIX image |
| Lightweight init/daemon system | **Keep systemd, trimmed** | Measured on the device (PSS including swapped): systemd 5 MB, journald 1, udevd 2, resolved 3, timesyncd 1, logind about 1: roughly 13 MB for the whole family. An alternative (dinit, s6, runit) would save perhaps 10 MB but needs eudev, elogind, polkit and D-Bus glue, and Steam's SteamOS helpers, PipeWire/WirePlumber and cgroup/oomd integration all assume systemd. Not worth the risk now; revisit if 10 MB ever decides something |

## Memory and robustness (lessons from 2026-10-06)

- Stripping the ROCKNIX frontend and extras freed about 100 MB (measured); Steam itself
  stays about 700-790 MB, and the tried Steam switches did not reduce it.
- Hard-killed Steam leaves `/dev/shm/u0-Shm_*` files (about 26 MB each, 355 MB seen):
  the session unit clears them at start (`wisp-shm-clean`, in the rootfs).
- An overload froze the device (kernel up, userspace starved). A single-purpose device
  needs an out-of-memory guard: `systemd-oomd` is present in the image; wire it to
  cgroup slices (`steam-ui.slice` protected/pressured, `game.slice`), as the whitepaper
  proposes. Not enabled in v0 because it was not tested.
- zram: 768 MiB lz4 now. The sizing test was not completed; do not change it without
  the safe test procedure in `OVERNIGHT-REPORT-20261006.md`.

## Build structure

`os/boot/` builds the bootloader; `os/rootfs/` holds files baked into the root filesystem;
later `os/` gains the kernel and image builders. All work trees and outputs live under
`/mnt/hdd/wispos-staging/`; every input is hash-pinned; builds must be reproducible.

## Phases and acceptance

| Phase | Content | Done when |
| --- | --- | --- |
| 1. Boot chain | Bootloader (done), kernel, initramfs, device trees from ROCKNIX's recipes | A card with only our chain boots to a login prompt/SSH on the handheld |
| 2. Rootfs and session | WispOS rootfs: `wisp` user, SSH, `vi`, systemd trimmed, Steam BPM as the only session, shm cleanup | Boots to the signed-in Steam home screen with at least 300 MB available |
| 3. Session switching | `steamos-session-select` and SteamOS-manager compatibility so BPM can offer "Switch to Desktop"; desktop session (pad-as-mouse, on-screen keyboard, menu); return path | Operator can go BPM to desktop and back using only the gamepad |
| 4. Robustness | oomd + cgroup slices, zram sizing by a safe test, hardware matrix pass | Overload kills the game, not the device; matrix passes |
| 5. Graphics | PanVK on the G31 (build Mesa with Vulkan, experimental flag) and gamescope feasibility | Decision recorded with measurements |
| 6. Games | Non-Steam flow; Proton (ARMv8.0 build, wined3d/OpenGL for old titles) as a Steam compatibility tool | A pre-2005 title runs from the library, chosen in Steam's compatibility window |

## Hardware matrix to verify on the device (none verified yet by this plan)

Display (DSI panel v2, backlight), HDMI out, built-in controller (buttons, sticks,
triggers), audio out (speaker, headphone) and mic if any, Wi-Fi (`rtw88_8821cs`),
Bluetooth (RTL8821CS) and its controllers, battery/charge status and LEDs, lid
(suspend/resume), rumble, both TF slots, USB-C (host/device, charging), RTC, volume and
brightness keys, sleep/power button behaviour.

## Risks and open decisions

1. **Session user.** The session runs as root for now, as in every working run; moving Steam and Sway to `wisp` needs seat, device-node and runtime-directory permissions worked out (phase 2). `wisp` is a member of `wheel`, `video`, `audio`, `input`, `render`, `netdev` and `users` already.
2. **Default password.** `wisp`/`wisp` on an always-on SSH is publicly guessable; this is the operator's explicit choice. Root has no password login (key only).
3. **GameMode ambition.** If real gamescope is a hard requirement, phase 4 gates the whole
   plan on PanVK working acceptably on a single G31 core; that may not be achievable.
4. **Steam updates.** Updates changed binaries and dependencies once already (missing
   `libva`). A compatibility gate (dependency check, instruction-coverage scan, emulator
   tests, SSH sign-in smoke test) should run before any new client version is adopted;
   pinning with `steam.cfg` is an option, untested here.
5. **Untested.** Nothing built here has run on the handheld yet.

## Steam UI integration targets (operator, 2026-10-07)

If the Steam UI runs well, WispOS should let it own the device-level settings instead of shipping its own
tools: Wi-Fi management, volume (mapped to the hardware volume keys), the "M" button as the Steam button
and, held, as a modifier, and formatting a card in the second TF slot. Status of each, from what is known:

| Want | What the evidence says | Unknown / to verify on the device |
| --- | --- | --- |
| Wi-Fi in the Steam UI | Steam's client links `libnm` (it is in `native-libs.lock.json`); the image runs NetworkManager with the iwd backend, which is what such a page talks to | Whether Steam's network page works on this stack; `wispos/wifi.conf` stays only as a first-connection fallback and for headless setups |
| Volume keys | The device tree has a `gpio-keys-volume` node (KEY_VOLUMEUP, 0x73, and a down key); audio is PipeWire with the pulse layer, which Steam's volume control uses | Who consumes the key events (ROCKNIX's input service, sway bindings, or Steam Input) and that the slider and keys stay in sync |
| "M" button = Steam menu, held = modifier | The joypad node `GPIO BTN_F` emits code 0x13c, which is `BTN_MODE`, the code Steam Input treats as the Guide/Steam button | Which physical key that is (check with `evtest`); the hold-as-modifier needs a Steam Input layout or a small daemon; SDL mapping lives in ROCKNIX's `mkcontroller`/controller database |
| Format the TF2 card from the UI | The device tree enables the second SD controller (`mmc@4022000`, with a card-detect GPIO and its own supply). Steam's binaries reference `/usr/bin/steamos-polkit-helpers/*` and the SteamOS manager, which is where SteamOS implements formatting | Kernel enumeration of a card there (needs the device); the helper must be written by us, must act only on the TF2 block device, and must refuse the boot card, and a library folder needs auto-mounting |

All of these depend on Steam's UI actually running on this display and input stack, which has not been shown on
this image yet. They are also independent of gamescope: they are D-Bus services and helper programs, not
compositor features, though Steam may gate some of them on detecting SteamOS (to be found empirically).

## Remaining efforts before 0.1.0 (operator, 2026-10-11)

Order the operator set: first the game-performance audit and the Steam UI memory plan (docs/WISPOS-PERFORMANCE-PLAN.md, docs/WISPOS-NOTES.md "Steam UI memory: decisions"; the Definition of done in AGENTS.md names the reclaimable UI), THEN the four efforts below, THEN the clean repository and the 0.1.0 release. Each effort starts with a design note and a measurement, not code.

| Effort | The operator's wish (2026-10-11) | What is known | To decide / verify first |
| --- | --- | --- | --- |
| **1. Remove the ROCKNIX DNA** (boot splash LOCKED 2026-10-11: docs/WISPOS-SPLASH-DESIGN.md) | Our own boot splash with a spinner by default; the text boot log toggleable with a sensible button press; volume left to Steam (it already recognizes the rocker); everything else ROCKNIX-shaped goes | Leftovers seen running: `input.service` with 7 `input_sense` processes (13 MB), `powerstate`, `batteryplus`, `hdmi-hotplug`, `rocknix-autostart` (6.4 s of boot), `debug-shell` on the kernel command line (`systemd.debug_shell=ttyS0`), a rebuilt hwdb on every boot (2.4 s), `paste` linked to `pastebinit`. Boot to `wispos.target` is 20 s; the splash has to cover the 2-4 minutes until the Steam UI | Splash tech: Plymouth (its Esc key toggles theme and details; the script plugin can draw a spinner; it needs a key event, so a pad button would need a tiny evdev-to-key helper, and it adds initramfs size) versus a small purpose-built DRM/framebuffer splash with an evdev button read (smaller, ours); which button; what shows between the bootloader and the first frame. Who consumes the volume keys today (ROCKNIX `input_sense`, sway, or Steam Input) and that Steam alone keeps the slider and the keys in sync, before anything is removed |
| **2. On-device OS updates** | Updates through the SteamOS UI's own update screen (Bazzite does the same: Steam menu, Settings, System, Check for updates) | The Steam UI talks to SteamOS-manager / `steamos-atomupd` (D-Bus `com.steampowered.Atomupd1`) and the `steamos-update` script; Bazzite ships a shim over its own updater and (Bazzite 44) a separate controller-friendly updater. A third-party man page gives exit code 0 = update available or applied, 7 = none: UNVERIFIED. Our shape is natural for an image update: SYSTEM is a squashfs file on the FAT boot partition, so a verified new file next to the old one with the old kept is an A/B with rollback (the bootloader fallback logic does not exist yet) | Read Valve's `steamos-atomupd` and Bazzite's shim sources and capture what the UI calls on this device (`steamos-update` log, as for the card formatter). "No host of ours" (binding): the image must come from an upstream origin we do not operate (GitHub Releases of this project) with a pinned hash and a signature checked against a key baked into the image; the writes to the card happen once per update and only then |
| **3. Desktop mode ("a gamepad-friendly desktop") and the way back to Game mode** | A light modern-web-capable browser; Steam OFF by default in desktop mode (resources); return to Game mode; later, game management on the device without Steam, non-Steam games included | Sway is resident (27 MB). Steam calls the SteamOS manager for `SwitchToDesktop` and `SetDefaultDesktopSession` and runs `steamos-session-select`: implementing those is the "Switch to Desktop" item, and a desktop entry that calls the same script is the way back. One mode at a time (memory). The browser: Cog / WPE WebKit is built for embedded boards of 1 GiB or less (per-process memory caps, cache options) but has leak reports in long sessions; Firefox and Chromium are heavier and the published comparisons are old. Nothing measured on this device | Measure the browser candidates (resident memory, start time, three real pages, video) on the device; where the aarch64 binaries come from (upstream packages or built in the rootfs build, hash-pinned); gamepad desktop pieces: pad-as-mouse (uinput daemon, Steam Input conventions apply), on-screen keyboard, launcher; whether Steam's desktop mode is the only non-Steam path for 0.1.0 (it is the cheap one: "Add a Non-Steam Game") |
| **4. Release hardening** | (from the north star, item 6) | No default password and SSH off by default (the operator chose `wisp`/`wisp` and always-on SSH for development; a release image must not), debug shell off, update path, repo audit | The operator's call on what a 0.1.0 image ships with; dev/release separation already exists (`WISP_DEV`) |

**Status of the four efforts (2026-10-11, end of the session; "host-tested" = unit tests plus QEMU, "device" = nothing flashed yet):**
| Effort | Done | Open |
| --- | --- | --- |
| 1. Remove the ROCKNIX DNA | boot splash (framebuffer phase) built into a patched initramfs, host- and QEMU-tested; `os-release` identity (NAME/ID/VERSION_ID/BUILD_ID); the always-on tty9 root shell pruned and forbidden by the checker | splash on the device (SELECT code, real timing); the black gap after sway starts (Wayland phase) and the first-run hand-over (`--intro`); `input_sense`, `powerstate`, `batteryplus`, `rocknix-autostart` (need the device and the operator: volume and brightness ownership); `led-poweroff` is NOT removable as it stands (its ExecStop turns LEDs off) |
| 2. On-device OS updates | the SteamOS D-Bus daemon (Atomupd1 on the system bus, Manager2 + CpuScaling1 on the session bus; measured bus layout; 34 tests against Valve's XML); the helper `wisp-update` (signed-manifest check, resumable verified staging; 27 tests, mutation-checked); QEMU proves the buses activate it in the real image | the initramfs apply step and rollback (designed in docs/WISPOS-UPDATE-DESIGN.md); three operator decisions (hosting and signing key; rollback depth; Steam command line); what Steam's UI really shows (device) |
| 3. Desktop mode | notes and constraints only; a search found no published memory figures for Cog/WPE/Epiphany on a 1 GiB Panfrost board, and Ladybird has no alpha yet | measure the browser candidates on the device; uinput pad-as-mouse and an on-screen keyboard; `SessionManagement1` and the way back to Game mode |
| 4. Release hardening | tty9 shell; the checker has a `FORBIDDEN` list | `os/check_release.py` (the operator's release profile first); SSH toggle; default password; `systemd.debug_shell` on release; OTG toggle (spike S-USB phase 2 needs a USB-C meter and the operator) |

**Effort 1 inventory: ROCKNIX-origin units enabled in the built SYSTEM (read from the image, 2026-10-11; costs are from earlier device notes, to be re-measured idle).**
| Unit / program | What it is (read from the files) | Verdict for a Steam appliance |
| --- | --- | --- |
| `rocknix-autostart.service` -> `/usr/bin/autostart` | runs ROCKNIX's user autostart scripts framework; 6.4 s of the 20 s boot | remove |
| `input.service` -> `input_sense` ("Volume button service") | an 18 KB **bash script**, run as 7 processes (13 MB); handles volume, brightness, radio and LED keys with `evtest` matching, key repeat and `volume`/`brightness` helpers | remove; the operator's rule: Steam handles the volume rocker (verify on the device that the Steam slider and the keys stay in sync, and that brightness has an owner) |
| `powerstate.service` -> `powerstate` ("AC/Battery Performance Service") | a **bash loop, every 2 s**, reads the battery status, sets the GPU performance level ("auto" or the `system.gpuperf` setting) and the charging LED | remove; GPU/CPU policy moves to the SteamOS-manager subset (docs/WISPOS-STEAMOS-CONTRACT.md) |
| `batteryplus.service` (`batteryplus-init`, a 68 KB binary) | ROCKNIX's battery helper, configured through `/storage/.config/batteryplus` and the `system.batteryplus.enabled` setting (default on) | decide after reading what it does (it writes its config on the card at start: an SD write); likely remove |
| `led-poweroff.service` | `ExecStart=/bin/true` (does nothing at start) but **`ExecStop=/usr/bin/ledcontrol poweroff`: it turns the LEDs off at shutdown** (corrected 2026-10-11: this row first said "a no-op", read from ExecStart alone) | keep until LED ownership is decided; the shutdown behaviour is real |
| `debug-shell.service` enabled in `sysinit.target.wants` (found 2026-10-11) | an early **unauthenticated root shell on virtual console 9**, on in every boot (`ConditionPathExists=/dev/tty9`); reachable with any keyboard, e.g. a USB keyboard once the OTG port is a host | **pruned** (the enablement link only; `os/rootfs-prune.txt`); a dev build still gets one through `systemd.debug_shell=` on the kernel command line |
| `hdmi-hotplug.path` + `.service` -> `handle-hdmi-hotplug` | handles HDMI plug events | keep until the HDMI-out behaviour with sway is decided (hardware matrix) |
| `rocknix-automount.service`, `rocknix-memory-manager.service`, `debug-shell.service`, `bluetooth*`, `systemd-timesyncd` | present in the unit directory; automount is replaced by our udev rule and `tf2-card` (pruned in part) | audit each: keep Bluetooth and time sync, drop the rest |
| `network-base.service`, `iwd`, `NetworkManager` | Wi-Fi and network (NetworkManager with the iwd backend) | keep |
| `shell.service` (`textmode.target`) | ROCKNIX's text-mode shell | remove |
| `/etc/os-release` | still `GIT_ORGANIZATION="ROCKNIX"`, repository `distribution`, branch `next` | WispOS identity |
Also found: **on this OS `paste` is a symlink to `pastebinit`** (a ROCKNIX leftover that already broke one of our scripts): audit the other multi-call links.

**Effort 4 inventory (read from the sources 2026-10-11; nothing changed yet).** What a RELEASE image ships today, and a proposed release profile (the operator decides; the current values were his explicit development choices):
| Item | Today (source) | Proposed for a release | Notes |
| --- | --- | --- | --- |
| Account `wisp` | password `wisp`, in `wheel`, `/bin/sh` (`os/accounts.py`, "chosen by the operator") | operator's call (see the sshd row: with SSH off by default the default password is only reachable on the console) | `wheel` also grants whatever sudo/su rules the image has: audit |
| `root` | no password, key only (`*` field) | unchanged | |
| sshd | **DECIDED and BUILT (operator, 2026-10-11): off by default, a persistent toggle, on by default on dev images.** `sshd.service` starts only while `/storage/.config/wispos/ssh-enabled` exists; `wisp-ssh on\|off\|toggle\|status` is the switch the desktop-mode menu will call (it shows the host name and addresses when on); an empty file named `ssh` on the boot partition seeds the flag once at first boot (the Raspberry Pi convention; development images carry it), after which the user's choice persists. Password authentication while on stays the operator's call (`wisp`/`wisp`; root keys only) | unchanged | tests: `SshSwitchTests` |
| Kernel command line | `console=ttyS0,115200 console=tty0 systemd.debug_shell=ttyS0` (`os/build-image.sh` APPEND) | **DECIDED (operator, 2026-10-11): keep, as ROCKNIX does** (its reference `extlinux.conf` ships exactly these on release images); anyone holding the card can edit `extlinux.conf` on a PC anyway, so the UART shell adds no access that physical possession does not already give | the always-on **tty9** shell (a different thing: ROCKNIX enabled `debug-shell.service` in every boot, reachable from a plain USB keyboard once OTG is a host) stays pruned |
| ROCKNIX leftovers in the boot graph | `rocknix-autostart.service` (6.4 s of boot), `input.service` + 7 `input_sense`, `powerstate`, `batteryplus`, `hdmi-hotplug`, `rocknix-automount` (pruned in part) | removed or replaced (effort 1) | each is also an attack surface and a memory cost |
| Dev tools | already behind `WISP_DEV=1`; `check_rootfs.py` and `verify_image.py` refuse them in release | unchanged | |
| Build identity | `os-release` still names ROCKNIX (`GIT_ORGANIZATION="ROCKNIX"`, `GIT_REPO="distribution"`, build branch "next") | WispOS identity, our repository and version | part of effort 1 |
A release checker (`os/check_release.py`, not written yet) would fail the build on any of the rows above, the way `check_rootfs.py` already fails a release tree that contains a dev file.

**Efforts 2 and 3 share one mechanism, written up in docs/WISPOS-STEAMOS-CONTRACT.md** (the two public D-Bus interfaces Steam's UI calls, `SteamOSManager1` and `Atomupd1`, may be implemented partially, and the UI shows exactly what works; that document also covers Steam's performance menu and the format button). **The update half is designed in docs/WISPOS-UPDATE-DESIGN.md** (signed manifest on an upstream origin, a staged copy next to the running SYSTEM, an apply step in our initramfs, a rollback marker; three decisions for the operator are listed there). The D-Bus daemon for both interfaces exists and is tested (`os/steamos/`).

**Then 0.1.0:** a clean repository (decide: fresh history or kept; remove build leftovers and labelled experiments such as `tools/fex-*-build.sh`, drafts under `docs/upstream/`), `manifest/lockfile.json` version, an annotated `v0.1.0` tag, a `CHANGELOG.md` release section, a clean-checkout build that reproduces the image hashes, and the Definition of done from AGENTS.md (an image booting on the device, two unrelated titles, no title special-cased).
**Scope note (assistant's recommendation, the operator decides):** "addressed" should mean decided and minimally working for 1 to 4 (the splash, the update path end to end, the session switch both ways), while the long-term features (game management on the device without Steam, non-Steam game launcher, richer desktop) are designed and deferred to 0.2 or later; otherwise 0.1.0 never closes.

## USB-C: power and data together (operator question 2026-10-11; read from the device tree and kernel config on the host, NOT yet tried on the hardware)
**What the board has.** The RG34XXSP's device tree wires the port to the H700's OTG controller (`musb`, `dr_mode = "otg"`) with an **ID-detect GPIO** (not a Type-C controller: there is no `typec`/PD node), a **GPIO-switched 5 V regulator** (`usb0-vbus`) that supplies VBUS when the board is the host, and the **AXP717** as the VBUS input/charger (`input-current-limit` 1.5 A, matching Anbernic's 5 V / 1.5 A). The kernel already has everything in software: MUSB dual-role, the gadget stack with configfs (NCM, ECM, RNDIS, EEM, mass storage), USB Ethernet adapters as modules (RTL8152, AX88179, CDC), USB storage/UAS/HID, the AXP20x charger.
**1. Charging while connected to a computer (device mode): feasible.** Plugged into a PC the port is a sink: VBUS charges the battery and the handheld can present itself as a USB Ethernet adapter (NCM works on Windows 11 and Linux; RNDIS for older Windows), so a computer can reach it by SSH/SFTP with no Wi-Fi at all, and the TF2 card can be offered as mass storage. Needs only userspace: a configfs gadget setup, an address (link-local or a tiny DHCP), and a desktop-mode toggle next to SSH. To verify on the device: the role switch happens when the cable is inserted, the charge current limit with a PC port (500 mA expected), and that the SSH toggle covers the USB interface.
**2. Host mode (a USB Ethernet adapter, a controller, a disk) while charging: NOT possible safely with the port as wired, without a hardware accessory and a software check.** In host mode the board drives VBUS itself from the 5 V regulator, so feeding external 5 V into the same pin (a charging hub or an OTG-plus-charge adapter) would back-feed that regulator: a kernel patch review for the sibling RG35XX 2024 board warns that the host controllers keep the 5 V enabled and that plugging into another power source can blow the board's regulator. A charge-through hub also relies on CC/PD signalling, which this port has no controller for. A safe version needs (a) a self-powered or charge-through hub, (b) the 5 V regulator off whenever external VBUS is present, which the PHY driver may already do (it reads the PMIC's VBUS-present state; NOT verified in the driver source), and (c) a bench test with a USB-C power meter. Until then: peripherals in host mode run on the battery, and charging uses the plain cable.
**3. Charging itself:** two reviews report cable trouble on this line (C-to-C cables, chargers); an adapter with a 5.1 k pull-down on the device side is the known remedy, which fits a port with no CC controller. Document "use the supplied A-to-C cable or a known-good one".
**Proposed work (after the sweep frees the device):** a `wisp-usb-gadget` oneshot (configfs NCM + a link-local address, started with the SSH toggle), then the bench tests above. Decision for the operator: whether USB networking is part of 0.1.0 (it is cheap) or later.

**DECIDED, GATED BY A SPIKE (operator, 2026-10-11: "the hard constraint is a spike; depending on its results we go forward or not"): a USB host (OTG) toggle in desktop mode's menu, built the safe way, next to the SSH toggle.** Nothing below is built until the spike (next section) passes. The safe way, as rules the implementation and its tests must satisfy:
1. **Off at every boot** (the state lives in RAM, not on the card). With it off the port is peripheral-only: the board never drives VBUS, so an OTG adapter or a charger plugged in by mistake cannot make it source 5 V. (Needs a way to force the role from software: the sunxi MUSB `mode` attribute or the PHY's forced mode is the candidate; NOT yet verified on this kernel.)
2. **On is refused while external power is present** (the AXP717 USB supply reports online): the menu says "unplug the charger first", it does not try.
3. **Auto-drop:** while host mode is on, if external VBUS appears (a charger or a charge-through hub is connected) the 5 V regulator is switched off at once and the port returns to peripheral; the toggle shows off again. A watcher on the power-supply uevent, not a poll.
4. **Battery floor:** host mode switches itself off below a battery level (proposed 15%), since peripherals run on the battery.
5. **No charge-through promises** until the bench test passes: a self-powered hub with passthrough charging, a USB-C power meter, and the back-feed check from item 3; the result goes in this document before the toggle ships in a release.
6. Tests: the rules above are a pure decision function (like `wisp-compat-gate`) with a table test; the on-device checks (forced role, regulator off on VBUS, meter readings) are gates for the release, not for the table test.
Together with the USB-networking gadget above (device mode: power and data from a computer), this gives both directions of USB use; host mode never charges, by design.

### Spike S-USB: can the host role be made safe on this board? (go / no-go for the OTG toggle)
**Question:** can software force the port into peripheral-only, refuse host mode while external power is present, and drop the 5 V output the moment external VBUS appears, with no moment where the board drives VBUS into a charger or a PC?
**Time box:** half a day of work once the device is free, in three phases; stop at the first failed criterion.
- **Phase 0 (host only, no hardware risk):** read the 7.2 kernel sources of record for `phy-sun4i-usb` (ID and VBUS detection, when the `usb0-vbus` regulator is enabled, what it does when the AXP717 reports VBUS present), `musb_sunxi` (the role/`mode` control) and the AXP717 USB power-supply driver (the `online` property and its uevent). Output: written answers to the three rules (force peripheral, refuse on external power, drop on VBUS), or "the kernel cannot do X".
- **Phase 1 (device, nothing plugged in):** read the live state: the MUSB `mode` attribute, `/sys/class/udc`, the regulator's state, the power-supply `online` flags; flip the role with nothing connected and read what changes. No cable, no risk.
- **Phase 2 (bench, the operator present, a USB-C power meter inline, a power bank or a current-limited source, never a wall PD charger):** T1 forced peripheral with an OTG adapter: the meter shows no VBUS output. T2 host on with a keyboard: 5 V and the device enumerates. T3 host on, then external power appears (a powered hub): the output drops within the time the rules require, no back-feed, the board stays cool. T4 external power present, then try to enable host: refused. T5 battery floor. Each of T1-T4 repeated for at least 10 plug/unplug cycles.
**Pass:** Phase 0 shows the kernel can do all three; T1-T4 pass every cycle; nothing warm or smelly. **Then:** build the toggle exactly as the six rules say. **Fail (any of: the role cannot be forced, the output stays on with external VBUS, a single back-feed in T3):** the toggle is dropped, and the finding is written down: today's hardware behaviour (an OTG adapter flips the port to host by itself) stays, with a documented warning never to combine it with a charging hub. That is also a result worth having, because that risk exists today with or without our toggle.
**Stop rule for Phase 2:** the first unexpected reading ends the session; no retries "to see".

#### Spike S-USB, Phase 0 results (read from our patched kernel source, `phy-sun4i-usb.c` with ROCKNIX's 0151 patch, which `kernel/patches/0151-phy-fix-OTG-host-mode.patch` applies; 2026-10-11, host only)
- **The 5 V output follows the ID pin.** On this SoC (`phy0_dual_route`) the role is decided only by the ID-detect GPIO: ID low = host (the PHY is routed to EHCI/OHCI and the `usb0-vbus` regulator is ENABLED), ID high = peripheral (routed to MUSB, regulator DISABLED). Patch 0151 makes `phy_set_mode` a no-op for dual-route SoCs ("the PHY mode is fully determined by the selected mux route"), so **there is no software knob to force peripheral at runtime** through the PHY or MUSB `mode`. **Rule 1 ("off" means the port cannot source 5 V) cannot be met by userspace on this kernel.**
- **Rule 3 (auto-drop when external VBUS appears) already exists in the kernel:** `sun4i_usb_phy0_id_vbus_det_scan` disables the internal VBUS regulator whenever the PMIC reports VBUS PRESENT (`usb0_vbus_power-supply` = the AXP717 USB supply), and the driver registers a power-supply notifier (`sun4i_usb_phy0_vbus_notify`) so a change on that supply schedules the scan: event-driven, no polling. Whether the AXP717 emits that event promptly is bench test T3. **Rule 2 (no 5 V while external power is present) is therefore already enforced below userspace**, whatever the toggle says.
- **The open physical question:** when the board itself is sourcing VBUS in host mode, does the AXP717 report VBUS PRESENT because of the board's own 5 V? If it did, the scan would switch the regulator off again right after enabling it, and host mode could never stay on. ROCKNIX ships host mode for the sibling RG35XX 2024 board, so it presumably works (the PMIC's input comparator may be isolated from the board's own 5 V), but that is a bench test (T2), not an assumption.
- **What that means for the toggle:** the kernel already protects against the dangerous case (back-feed). What it cannot do is make "off" real: an OTG adapter (ID grounded) powers the port as a host with or without our toggle. Options, each with its price: (A) a small PHY patch of ours adding a gate (a sysfs attribute that keeps the regulator off and treats ID as high), carried with the other ~20 kernel patches and offered upstream; (B) two device trees and a reboot to switch (safe, awkward); (C) no gate: keep stock behaviour, drop the toggle, document it. **Decision after the bench tests**, not before. If the spike says host mode is safe as it is, (C) is the honest outcome and the toggle becomes a non-feature.

#### Spike S-USB, Phase 1 results (device, 2026-10-11 ~17:50 device clock; a CHARGER was plugged in, so the host direction was NOT tried; read-only apart from one harmless write)
- State with a charger attached: `extcon0` (the PHY) `USB=1 USB-HOST=0`; the regulator `usb0-vbus` **disabled** (no users; its supply `boost` also disabled); `axp20x-usb` power supply `online=1 present=1`, VBUS 4.944 V; the UDC `musb-hdrc.5.auto` exists and the kernel mounts configfs (a gadget can be built); MUSB `mode` reads `b_idle`, `vbus` reads "Vbus on". The ID line is the GPIO `usb0_id_det` (gpio-260, active low, pulled up); the regulator's enable is gpio-272 (low).
- **Forcing the role from software does nothing:** `echo peripheral > .../musb-hdrc.5.auto/mode` is accepted (rc 0) and changes nothing (mode, extcon, regulator, supply all identical); `otg` restored it. This matches Phase 0 (`phy_set_mode` is a no-op on this SoC). **Rule 1 stays unmet in userspace.**
- The device's own DT comment says the AXP717 "has USB type-C role switch functionality, not yet described by the binding": the ID signal may come from the PMIC's Type-C logic, not from a bare OTG-adapter ID pin. That changes what a "host" cable looks like to the port and is the first thing T2 (host with a keyboard) must establish.
- Still needed from Phase 2 (operator + meter): what a host-side cable/adapter does to `usb0_id_det`, whether `usb0-vbus` turns on and stays on (the PMIC reporting the board's own 5 V as VBUS), and the back-feed cases T3/T4.
- Side finding for the splash: the device's input node `event4` "H700 Gamepad" exposes `BTN_SELECT` (314), `BTN_START` (315), `BTN_MODE` (316, the M button) and the D-pad as buttons (544-547): the splash's default toggle key exists; that the physical SELECT button emits 314 is still to be confirmed by a press.

