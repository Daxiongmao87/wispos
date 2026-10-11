# WispOS north star

Read this first in every session. It is short on purpose. Detail lives in `WISPOS-NOTES.md` (findings and how-tos), `WISPOS-TIMINGS.md` (how long each step takes: read before waiting on anything), `WISPOS-OS-DESIGN.md`
(design decisions), `NATIVE-ARM64-PLAN.md` (gated plan for Steam, Proton and a game), `../TODO.md` (open items).

## What this project is for (operator, 2026-10-10: read this before anything else)

**A strongly compatible core system for Steam games on this device. Not a collection of per-game fixes.** "We aren't here to apply game-specific patches when Steam has a library of millions of games. It is to ensure general game compatibility with a strongly compatible core system." Worms Armageddon, BIT.TRIP RUNNER and any other title we run are *test vehicles* that reveal where the core is weak; the deliverable is the core (OS image, compatibility tool and its defaults, FEX/Wine configuration, Steam seeding, memory and display management). Test every change by asking "would this help a game I have never seen?" If the honest answer is no, it is a diagnostic, not a fix. Details: Standing rules, "General compatibility, never per-game patches".

## The goal (operator's words, condensed)

WispOS is an **extremely lightweight "Bazzite"** for the **Anbernic RG34XXSP** (Allwinner H700, 4x Cortex-A53 = ARMv8.0 with no LSE,
1 GiB RAM, 720x480 DSI panel). It boots straight into **Steam**, built from ROCKNIX's open recipes (own bootloader, kernel, rootfs and
image; no overlay, no ROCKNIX frontend, no fallback). It is a single-purpose OS, not a distro and not a desktop.

**The UI target is the SteamOS UI, not plain Big Picture.** The SteamOS UI is the Deck-style gamepad UI with OS integration: Wi-Fi
management, volume wired to the device's volume keys, the "M" button (BTN_MODE 0x13c) as the Steam menu (and a modifier when held), and
the TF2 slot's SD-card formatting helper. Today's session runs plain Big Picture (`-gamepadui`); that is a stepping stone.

**Public-release credibility.** It will be released to the public. So: no default password and no SSH key baked into a release; SSH off by
default in a release; the Steam client is **downloaded on first run** (like Bazzite), never redistributed; secrets never enter the repo or an
image. Everything private or developer-only is behind `WISP_DEV=1` and is checked absent from releases by `check_rootfs.py` and `verify_image.py`.

## The core principle (operator, 2026-10-08: locked in)

**Get lightweight and older games running at playable speeds, locally, on this device.** We are not pushing for AAA.
- **Step 1: 1990s games.** **Step 2: games from before 2005.** Later steps only after these are solid.
- Playable bar (operator's words, same day): **30 fps is the target; 20 fps is the bare minimum, and only if it makes the system more responsive;** a 48 Hz panel at 24 fps is forgivable.
  Refresh and resolution may be lowered to buy speed (31-60 Hz and 1.5x-3x scale-up both work in the compositor, notes 5b).
- Everything is judged against this: a feature or optimisation earns its place by making these games start, run and feel better on 1 GiB / 4x A53. Streaming (Steam Link / Remote Play) is a possible extra, never a replacement for local play.
- BIT.TRIP RUNNER (2010) is the Windows-pipeline proof, not the target class; the real test set must be 1990s then pre-2005 titles. How those run (Proton/WineD3D with DirectDraw and early Direct3D, Steam's own DOSBox packaging, native Linux builds) is **not yet measured; do not assume**.

## The final goal (operator, 2026-10-09)

**Run a game as demanding as vanilla World of Warcraft 1.12.1 on this handheld, even at 24 fps with minimum graphics settings** ("I would like to see this run that one day"). 24 fps pairs with the 48 Hz panel mode. Every performance and runtime decision is checked against it. Feasibility is open until the optimisation work has been done and measured (all numbers so far come from an unoptimised stack): see the ladder in `docs/WISPOS-PERFORMANCE-PLAN.md`.
The client and any server are the operator's to supply; WispOS provides the runtime.

### Stretch goal (operator, 2026-10-09): Project 1999 on the handheld
**Project 1999** (the free EverQuest emulated server) running on the device: "that'd be something to see". Facts checked 2026-10-09 (wiki.project1999.com/Titanium_Edition, CodeWeavers' compatibility page, VOGONS thread): it uses the **EverQuest Titanium** client (a 2005 release, newer than the 1999 original) with Project 1999's modifications, and Titanium has an older DirectX 7 renderer path that a GeForce 2-class card handled. It is a heavier, online, mouse-and-keyboard MMO, so it sits at or above the WoW rung: ladder rung 5 in `WISPOS-PERFORMANCE-PLAN.md`. Same ground rules as the final goal: the client files and account are the operator's to supply, feasibility is open, and nothing here changes the order of work (older, lighter games first). Input stays Steam Input's job (no OS-level pad-to-mouse mapping).

### Pilot test (operator, 2026-10-10): Final Fantasy XI as the NON-STEAM game
Operator: "Final fantasy 11 would be an interesting pilot game as well since it was released in 2003, now has a free to play, and has controller support, and allows us to test the process and feasibility adding a nonsteam game, and how we can streamline that process to minimize need of external accessories assuming we have the assets on device. Add that as one of our tests."
**What it tests:** (1) getting a game that is NOT in the operator's Steam library onto the handheld using only the handheld's own controls: how it enters Steam's library (Steam's "Add a Non-Steam Game" picker driven by the pad, versus the OS seeding a `shortcuts.vdf` entry and the compatibility-tool mapping from a drop-in on the data partition) and gets Proton as its tool, with the assets assumed to be on the device already (SD card / data partition); (2) a 2003 online game through Proton on the A53; (3) text entry with the controller (Steam's on-screen keyboard) for the account login: **the operator signs in, I never enter credentials**; (4) the game's own gamepad support (community reports: it must be enabled in FFXI's own configuration, XInput) next to Steam Input, which stays the one input mapping layer; (5) first-run and launch time costs (record them in `docs/WISPOS-TIMINGS.md`). The outcome to design for is a streamlined, repeatable "add a non-Steam game" path that needs no keyboard, mouse or PC beyond putting the files on the device, and that is general (no FFXI special case).
**Facts checked 2026-10-10 (web search, not yet hands-on):** Square Enix made the free trial unlimited in time with a level cap of 75 (announced May 2026, September 2026 rollout), through the Windows client from the Square Enix store, which needs a Square Enix account; one third-party download page lists the EU client at about 7.4 GB (2021, may be stale); requirement lists found are old (Windows XP/Vista/7, DirectX 8.1, 128 MB RAM minimum); the Linux community uses Lutris/Wine or an older/GE Proton under Steam (BG-Wiki "Linux Installation Guide", the HorizonXI Linux installation repo); ProtonDB has a page for the game (app 230330, mixed reports, not read). **Status: queued, not started.** Needs from the operator: the client files on the device (or a way to put them there) and their own account sign-in. Run it after the current second/third Steam games; do not let it jump the queue ahead of the FEX DEP gap (it is an online MMO of the heavier class, next to the WoW rung in `WISPOS-PERFORMANCE-PLAN.md`).

## The user's path (first boot)

Forced first-run screen (gamepad driven): continuous Wi-Fi scan, pick a network, on-screen keyboard for the password, cannot be dismissed until
online and Steam's servers answer; then the Steam download (progress, resumable); then Steam's own sign-in; then everything works. Match Steam's
design language so the hand-off between our screen and Steam looks like one system. Built and verified on hardware (2026-10-07).

## Definition of done (gates, in order; do not skip)

1. Boots on the handheld from our own image. **Done.**
2. First-run Wi-Fi screen, Steam download, sign-in. **Done on hardware.**
3. **Playable older games (the core principle): a 1990s Windows game, then a pre-2005 one, run end to end on the A53 at playable speed** (launch, play, exit, return to the UI) with measurements. Proof of the pipeline so far: a Windows game runs end to end through Proton on the A53 (launch, play, exit, return to the UI) with measurements (launch latency, frame
   times, memory, zram, return latency), controls and audio. Test game: **BIT.TRIP RUNNER** (Steam app 63710, Windows only, owned via Family
   Sharing), using the **ARM64 Proton** builds (Valve's Proton 11.0-x (ARM64), and the community ARMv8.0 rebuild `Azkali/proton-arm64-a57`). **In progress: the game now
   RUNS on the handheld (title screen, 2026-10-08).** It needed four stacked fixes (notes 3.4b). Still open: input into the game, a measured frame rate, and making the setup
   repeatable without hand edits (offline d3dx9/d3dcompiler payloads, Proton Stable ARM64 + WineD3D defaults, `/run/udev` shared into the container).
4. Memory and CPU are sustainable on 1 GiB (see "Resources" below). **Open; first measurements exist.**
5. SteamOS-UI integration (Wi-Fi, volume, M button, SD format). **Not started.**
6. Release hardening (no default password, SSH off, update path, repo audit). **Not started.**

## MVP success KPI (operator, 2026-10-10: binding)
"I think we call this a success if we can reach general compatibility parity with fex-verified games for our targeted pre-2005 goal. That should be our MVP success KPI." Measure: of the games that the FEX project's own wiki (`wiki.fex-emu.com`, `Category:Game_Type_Application`, rated "Playable") and Valve's ARM64 lists show working, the ones in the pre-2005 target class (1990s first, then up to 2005) must also work on this handheld at the performance bar (30 fps target, 20 floor) with no per-title changes. Where a FEX-verified title fails here, the cause is OUR environment (Proton/FEX build choice, Mesa, memory, display modes, Steam seeding) and the fix is general. The operator's preference (same day): use games known to work on FEX as the controls, and do not patch FEX/Wine ourselves ("search for known working games on fex instead of patching fex yourself"); a FEX/Wine build change is for the operator to decide, not a default. **Ruled out by the operator (2026-10-10 ~19:30): any custom FEX build** (including unmodified upstream source recompiled for our CPU): "I thought I said any custom fex builds goes against the point?" Finding that follows: stock Valve FEX executables use LSE and do not start on the H700 (docs/WISPOS-NOTES.md, ruling block), so parity for the native-Linux games is blocked on a stock build that runs on ARMv8.0. Data: 311 wiki game pages parsed with the Infobox fields in `/mnt/hdd/projects/active/wispos-fex-win/compat/pages.json`; 55 of them are in the operator's library (list in `docs/WISPOS-NOTES.md`, "FEX-verified games the operator owns").

## Standing rules (operator's, binding)

- **Compatibility-layer builds (operator, 2026-10-10; full spec `docs/WISPOS-COMPAT-LAYER-SPEC.md`):** stock first, then official upstream binaries (FEX project packages, community ARMv8.0 Proton), then the DEVICE builds unmodified upstream source locally for its own CPU; we never ship binaries we built. On by default, no extra dialog, no chord. Downloads follow Steam's own download settings; the compile runs only when no game is running, there is no pad input for N minutes and no Steam download/install is in progress; plugging in is not required except that it pauses below 25% battery; pinned versions that MATCH what Steam/Proton installed, last good build kept, LSE census gate before activation.

- The operator's stated facts and instructions win over my priors. State a concern once, then do what was asked. (`AGENTS.md`, top.)
- **General compatibility, never per-game patches (operator, 2026-10-10: "we aren't here to apply game-specific patches when Steam has a library of millions of games; it is to ensure general game compatibility with a strongly compatible core system").** A game is a test vehicle, not the target. A fix ships only if it lives in the core (the OS image, the compatibility tool and its defaults, the FEX/Wine configuration, Steam seeding) and is justified by a cause that is not particular to one title. A per-game prefix edit, launch option or script is allowed as a DIAGNOSTIC to find the cause, is labelled as such in the notes, and is never shipped. A core fix is validated on at least one second, unrelated game before it counts; game-specific test tooling (menu clicks, HUD reading, `/nointro`) stays under `os/dev/` and never ships.
- **A weird screenshot after idle is Steam's screensaver until a pad press says otherwise (operator, 2026-10-10: "Its the fucking screensaver dude... Push a fucking button" / "you have been spinning wheels because you don't note").** Cropped rotating game art, an "Away" pill, black elsewhere. First action: one `wisp-dev-pad press b` and a new screenshot, then logs. Playbook item 6 in `WISPOS-NOTES.md`. And: write each lesson into the notes the moment it is learned, not at the end.
- **Never claim something is safe or verified without the output read in the same session.** Device-level destructive operations (PCI
  remove, driver unbind, resets) are never proposed. Say what was NOT verified.
- **Keys, credentials and personal addresses never go in the repo or in a release image.** `AGENTS.md` itself holds a private key and LAN
  addresses and must never be published. Dev access is a separate dev build (`WISP_DEV=1`), see `os/dev/README.md`.
- Storage: images, downloads, work trees and temp files go on the HDD (`/mnt/hdd/...`), never root or `/tmp`. Wispos image work lives under
  `/mnt/hdd/wispos-staging/`; other scratch under `/mnt/hdd/projects/active/<project>/`.
- Host safety (`HOST-SAFETY.md`): no apt/dpkg changes on the host; no privileged containers; fail closed when unsure of the target.
- **On the handheld write only under `/storage/wispos`** (the boot partition is separate FAT; avoid writing it from the device). No kernel,
  firmware or partition changes from the device. Reflashing goes through the reader PC at `agent@192.0.2.200` with the CID/size checks
  recorded in `STEAM-DEBUG-WORKFLOW.md`; recover logs from the card first.
- Never run overload tests unattended without hard limits (cgroup limits, a watchdog, the operator awake). A past unattended test locked the
  device. A memory watchdog (`/storage/wispos/dev/steam-watchdog.sh`) must be running before anything heavy; it does not survive a reflash.
- Never enter credentials. The operator signs in; a saved login token is kept off-repo with `os/dev/steam-session.sh`.
- Tests prove behaviour; a bug fix gets a regression test that was red first; every behaviour change gets a changelog entry; commits use the
  required trailers; a dirty tree is not a release.
- Do not publish or share a dev image. Do not edit what the operator edits (the badge artwork).

## Where things are

- Build: `os/build-rootfs.sh`, `os/build-image.sh`, `os/verify_image.py`, `os/qemu-test.sh`, `os/dev-test.sh`; work tree
  `/mnt/hdd/wispos-staging/wispos-os-20261007/` (`out/`, `rootfs/`, `tmp/`).
- Dev device loop: `os/dev/README.md` (listener, announcer, SSH, `steam-session.sh`, `wisp-dev-pad`).
- Memory notes for the assistant: `~/.claude/projects/-home-agent-projects-wispos/memory/` (index `MEMORY.md`).

**Known gaps (what is NOT verified) live in `docs/WISPOS-NOTES.md` section 5b; read it before claiming anything works.**

Display results (refresh 31-60 Hz verified, resolution scaling, open items): `docs/WISPOS-DISPLAY-FINDINGS.md`.

Performance (aggressive optimisation without sacrificing UX; budgets, levers, rules): `docs/WISPOS-PERFORMANCE-PLAN.md`. It runs alongside every gate.
