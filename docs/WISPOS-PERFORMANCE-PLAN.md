# WispOS performance plan: aggressive optimisation without sacrificing UX

Written 2026-10-09 at the operator's request ("aggressive performance optimization without sacrificing UX"). This runs alongside every other gate, not after them. Numbers marked [hw] were measured on the handheld; budgets are PROPOSED until the operator approves them.

## Rules
1. **Measure first, one change at a time, at least 8 runs** for anything that can shift the Steam UI's intermittent stall (docs/WISPOS-NOTES.md 4d). Overclaiming a fix after 2 good runs happened twice.
2. **Every optimisation has a UX check next to its speed number:** legibility, input latency, stability (stalls, freezes), game feel. A speed gain that costs any of those is rejected or made a setting.
3. Every change is reversible by one switch and recorded in the notes with its numbers; defaults only change in the image with a regression test.
4. Never trade away the operator's stated bar: 30 fps target, 20 fps floor (only if more responsive), 24 fps at 48 Hz forgivable; 90s games first, pre-2005 next.

## Proposed UX budget (to approve)
| Metric | Today | Proposed budget |
|---|---|---|
| Power button to usable Steam UI | about 3 min after a power-cut (80 s of it Steam's file verification); about 4 min on a clean restart | under 90 s from boot, with the splash showing progress |
| UI input to visible response | not validly measured yet | under 150 ms for navigation, no multi-second stalls |
| UI frame rate while navigating | CPU-bound: GPU process 56-60% and renderers 50-80% of a core [hw] | steady 30 fps |
| Game launch to playable | 2+ minutes once Wine tracing is off [hw]; much more with tracing | under 60 s for a 90s title |
| Game frame rate | BIT.TRIP RUNNER gameplay 27-46 fps, menus 0.6-4 fps [hw] | at or above 30 fps in menus and play for the chosen era |
| Memory headroom while a game runs | 60-300 MB available, Steam stack holds about 930 MB [hw] | at least 150 MB available, no swap thrash (PSI full under 5%) |
| Whole-system freeze or UI vanish | one hard freeze, one UI loss under load | none in a 2 hour soak |

## What the measurements already say [hw]
- **Emulation dominates the game's CPU:** FEX runtime 26% + translated code 30% (game thread 15%, Wine's DirectSound mixer thread 14%); Mesa's GPU driver under 1%; kernel time inside the game about 3%.
- **My own tracing was a big cost:** `WINEDEBUG=-all` took BIT.TRIP RUNNER's load from over 4.5 minutes (unfinished) to about 2.
- **Steam's UI is the memory problem:** about 930 MB of 976 MB; the main renderer alone 291 MB resident + 177 MB swapped.
- **Chromium runs on two cores only:** Steam's wrapper pins it with `taskset 0x7c` (CPUs 2-6, written for the Deck); this SoC has CPUs 0-3.
- **Half resolution cuts GPU work, not CPU:** the GPU stays at its 420 MHz floor while the UI animates (600 MHz about half the time at full resolution); operator reports it feels more responsive; first-run screens need native resolution to stay legible.
- **Idle is cheap:** the idle UI is about 0% CPU with the GPU at its floor; heavy loads come from installs, navigation and games.
- **Install load starves the UI:** a 987 MB install pushed load to 12 and knocked the UI out.

## Levers, by expected UX gain per risk (none shipped unless marked)
| # | Lever | Evidence / idea | Status |
|---|---|---|---|
| 1 | **Debug off everywhere:** `WINEDEBUG=-all`, no `PROTON_LOG`, Steam's Vulkan shader pre-caching off, no tracing in the image | measured | game defaults in source (4415a78); shader pre-caching seeding unbuilt |
| 2 | **CPU placement:** UI on all 4 cores when idle; while a game runs, give the game its cores (cgroup `cpu.weight`, nice, or `SCHED_IDLE` for the UI; or pause the web helpers) | UI uses 1-1.5 cores navigating; paused-UI test removed memory stalls but not the game's CPU-bound load | `taskset -a -p 0xf` live test not run; pause test done (inconclusive for speed) |
| 3 | **Chromium footprint:** low-end device mode, drop site isolation, in-process GPU, no smooth scrolling, raster threads; via a wrapper around `steamwebhelper.sh` (unknown if Steam's verification restores it) | memory and CPU | untried |
| 4 | **Memory policy:** zram size/RAM cap/algorithm/swappiness/page-cluster as one dev lever; ROCKNIX `rocknix-memory-manager` and `chksysconfig` interplay; no swap on the SD | 768 MiB vs 2048 MiB+cap: bigger delays the Steam kill but doubles swap traffic | sweet spot not found |
| 5 | **Emulation tuning:** FEX settings for weak CPUs (TSO emulation, x87 precision, code caching if available), keep Wine's mixer off the hot path, WineD3D options, persistent shader/code caches | FEX+JIT is 56% of the game's CPU | untried; correctness risks, each one A/B |
| 6 | **Display and pacing:** half resolution default (done in source), refresh 31-60 Hz with frame rate at Hz or a divisor, a frame limiter/pacer for the UI and games | Chromium and Wine do not pace themselves to a lower refresh | limiter unbuilt |
| 7 | **Boot and start:** boot-time config rsync of `chksysconfig`, ROCKNIX autostart framework, failing udev rules, unclean-stop recovery (Steam re-verifies about 80 s after a power cut), warm caches during the splash | userspace boot is 14 s; first Steam start 2-4 min | not started |
| 8 | **Install/update isolation:** throttle or defer installs so they cannot starve the UI; auto-recover a vanished UI | Doom install episode | not started |
| 9 | **I/O:** SD mount options, journal/log placement, avoid writes during play | SD is slow (15-25 MB/s) | not started |
| 10 | **SoC:** CPU/GPU governors, thermal margin, power per Hz | SoC reached 62 C in play; no cooling | governor test showed no stall effect; power not measured |

## Harness gaps to close first
- **A valid input-latency measure** (the first one was invalid: the screen is never static): timestamp inside Steam via DevTools, or ambient-free regions.
- **A repeatable scenario script:** boot, wait for UI, navigate N steps, launch the test game, play a scripted stretch, exit; recording frame times (Gallium HUD dump), per-process CPU (perf or /proc), PSI, temperature, and the flight recorder.
- **A results table in the notes** per lever (before/after, runs, UX verdict).
- **Soak test:** 2 hours without a freeze, with the recorder at its 5 s cadence (it ran at about 20 s because `top` and the process scan are slow under load).

## Order
Baseline harness, then levers 1-2 (cheap, reversible), 3 and 5 (largest unknown gains), 4 (needs the game scenario), 7-8 (boot, robustness), 6 (needs the stand-in), 9-10 last.

## The ladder to vanilla WoW 1.12.1 (final goal; each rung is a measured stepping stone, not a commitment)
**Caveat (operator, 2026-10-09): every number in this file is from an UNOPTIMISED stack** (Wine tracing was on for most game runs, no FEX tuning, Chromium on 2 cores and competing with the game, UI memory not reduced, no emulator alternatives tried, zram untuned, no frame pacing). They show where the cost is, NOT what is achievable; none of them is evidence that WoW is out of reach.
WoW 1.12.1 (2006 client: 32-bit Windows, single-thread-heavy, Direct3D/OpenGL, MPQ archives of several GB, 1.12-era Lua UI) is a 2004-2006 title whose published minimum (2004, per the Warcraft wiki system-requirements page and press coverage) was Windows 98/ME/2000/XP, an 800 MHz or faster CPU, 256 MB RAM, a 32 MB 3D card with hardware transform and lighting (GeForce 2 class) and 4 GB of disk: a 1999-2000-era PC, a deliberately low-spec game. (An earlier version of this page said 128 MB RAM: wrong.) Under x86 emulation on an in-order Cortex-A53 the CPU cost is still the question.
**What decides feasibility (all unmeasured for WoW):** (1) single-thread emulated x86 speed on the A53 (FEX now; Box64 as an alternative runtime to benchmark); (2) memory: the game needs a few hundred MB while Steam's stack holds about 930 MB, so the Steam UI must be frozen or reduced while a game runs, or the game must start outside Steam; (3) WineD3D/OpenGL speed on the Mali-G31 (Panfrost, GL 3.1, 244 MB reported video memory; half resolution and minimum settings help the GPU, not the CPU);
(4) keyboard and mouse UX on a handheld (gamepad-to-mouse mapping, on-screen keyboard); (5) Wi-Fi latency and disk (MPQ reads from the SD card).
| Rung | Example titles (era) | Why it is a step |
|---|---|---|
| 0 | a light 2D or early 3D Windows game (the operator is choosing one) | first clean end-to-end run: launch, play, exit, return to the UI |
| 1 | 1990s DirectDraw / software-rendered games | tests emulation without 3D cost |
| 2 | 1998-2001 Direct3D 6/7 3D (Quake III, Half-Life era) | tests WineD3D/OpenGL on the G31 and emulated CPU together |
| 3 | 2002-2004 heavier 3D (Warcraft III, Morrowind, Neverwinter Nights era) | the CPU and memory profile closest to WoW below WoW |
| 4 | **WoW 1.12.1 at minimum settings, 24 fps at 48 Hz** | the goal |
| 5 | **Project 1999** (EverQuest Titanium client with the P99 patch, DirectX 7 renderer option) | stretch goal (operator, 2026-10-09): an online 2005-era MMO client; same emulation, memory and input questions as WoW, more so |
Two proxy measurements before climbing: an x86 CPU benchmark under FEX (and under Box64) against native ARM for the same work, to get the emulation overhead factor; and a RAM budget with the Steam UI frozen. If either number is far off, the plan changes before more is built.

## Boot and memory analysis of the running dev image [hw, 2026-10-09 04:55, uptime 25 min, Steam UI idle]
- **Boot:** `systemd-analyze`: kernel 5.8 s + userspace 14.2 s = **20 s to `wispos.target`**; `wisp-setup` at 14.0 s and `wisp-steam` at 14.18 s. So the 2-4 minutes from power to a usable UI are almost all Steam (bootstrap, the 80 s file verification after an unclean stop, UI load), not the OS.
- **Critical chain inside the OS part:** `systemd-hwdb-update.service` **2.4 s on every boot** (the hardware database is rebuilt because it is not baked into the image: pre-generate it at build time); `rocknix-autostart.service` **6.4 s** (ROCKNIX's autostart framework running its scripts; the biggest single item: audit which scripts a Steam appliance needs); device waits of about 4 s for the SD partitions; `NetworkManager` 1.9 s, `rocknix-automount` 1.6 s, `hdmi-hotplug` 1.1 s, `wisp-bootlog` 0.8 s.
- **ROCKNIX leftovers still running:** `input.service` with **7 `input_sense` processes (13 MB)**, `powerstate`, `batteryplus`, `hdmi-hotplug`, `debug-shell.service` (kernel cmdline still has `systemd.debug_shell=ttyS0`: a RELEASE-HARDENING item), `rocknix-automount` (mounts and scaffolds any inserted card with ROCKNIX game folders), `systemd-timesyncd`.
- **Memory by non-Steam process (RSS):** python3 x2 15 MB (the dev panel-test watcher and the new screen-idle watcher: a C or shell version would be lighter), `input_sense` x7 13 MB, sway 13 MB, Xwayland 12 MB, sshd sessions 11 MB, pipewire-pulse 10 MB, systemd 9 MB, wireplumber 7 MB, systemd-resolved 7 MB. System: MemAvailable 332 MB, cached 375 MB, slab 61 MB, swap in use about 420 MB of 768 MiB.
- Candidate savings (unmeasured until done): pre-built hwdb (-2.4 s), a trimmed autostart (-up to 6 s), dropping `input_sense` (-13 MB), the Python watchers as small C/shell programs (-10 MB), removing debug-shell (security), replacing `rocknix-automount`.

## Game-performance audit (operator, 2026-10-11): "be absolutely sure that we have optimized our OS as much as possible for full game performance"

**Why now:** Quake III Arena (1999) runs at 28.1 fps on the delivered stack; WoW 1.12.1 (the final goal, 24 fps at minimum settings) is a 2004-2006 client. If a 1999 game is already at the edge, the whole stack has to be as fast as it can be before climbing the ladder.
**What "sure" can mean:** nobody can prove a negative, so the claim is made by process: (1) a profiler that says where a frame's time goes, (2) every lever below either measured or ruled out with a reason, (3) each adopted change confirmed by a second, unrelated game, (4) the unmeasured levers listed, never silently dropped. This section is that list; results go in the notes and the status column here.
**Binding rules for this audit:** (a) *No per-game configuration* (operator 2026-10-11: "Doing per-game fex configs is not maintainable"): FEX and Wine settings are tested through ONE global mechanism, adopted only as a global default that is safe for every game, and a setting that breaks any title stays off instead of getting a carve-out. Per-game launch options and per-game config files are test conveniences, never shipped. Valve's launcher already takes per-title FEX settings from Steam's own app data (`STEAM_FEX_TSOENABLED`, `STEAM_FEX_MULTIBLOCK`); that data is Valve's to maintain and we inherit it. (b) No custom FEX or Wine builds (operator ruling 2026-10-10); settings of the stock builds are fine. (c) The card is written as little as possible: profiles live in RAM or on the host.

**The profiler (os/dev/perf-run.sh; its own cost is measured against the unprofiled timedemo):**
| Layer | What it shows | Cost to check |
|---|---|---|
| `stream` (1 Hz) | per-core load and clock, GPU clock, game vs Steam UI CPU, busiest threads by name, memory pressure | a Python sampler, about 7% of one core at idle |
| `stat` (1 Hz) | IPC, branch and cache misses from the A53's PMU | counters are free; the reader is small |
| `record` (499 Hz, RAM) | CPU time by command, by component (kernel, FEX runtime, Wine, Mesa, translated code) and by symbol: the "what is eating the frame" table | sampling, expected well under 5% |
| per-frame times | not built yet: Gallium HUD frame times or a swap-interval timestamp, to line a slow frame up with the layers above | to be measured |
Facts found while building it [hw 2026-10-11]: `perf` 7.2.0 is on the device with the Cortex-A53 PMU (cycles, instructions, branch-misses, cache-misses, L1d misses) and records to RAM (`/run`). FEX has its own sampling statistics (JIT time, self-modifying-code events, soft-float x87 events, unaligned-access faults; FEX-2503 release notes), but they need Mangohud and a FEX built with the option: not available in the stock set, so `perf` is the tool.

**Lever checklist (status: untested unless a number is given):**
| # | Lever | Why it might matter | Status |
|---|---|---|---|
| A | CPU governor `ondemand` -> `performance` while a game runs (general rule, not per game) | cores sit at 1008-1416 MHz depending on load history; mainline notes say dynamic governors were unstable on some H700 silicon while `performance` was stable (lkml 2024-11 "Update H700 opp values") | **no effect on Q3** (29.1 vs 28.8 fps, n=2; cores already at 1416 MHz under load); keep ondemand |
| B | GPU devfreq `simple_ondemand` -> `performance` (420-600 MHz; 648 MHz is listed but capped by `max_freq`) | tells directly whether the GPU is the limit; the governor only raises the clock above ~90% load | **no effect on Q3** (same sweep); the GPU is not the limit at 640x480 (floor clock, no GPU waits in the thread samples) |
| C | FEX `X87ReducedPrecision` (global) |  FEX emulates x87 in software for 80-bit precision; 1999-2005 games use x87 for floating point (the Box86 author notes OpenArena, a Quake III engine, is mostly x87); off by default; risk: visible/numeric errors in games that need 80-bit | **no effect on Q3** (29.0 vs 28.8, n=2); leave off |
| D | FEX `TSOEnabled` 0 (global) | x86 memory-ordering emulation costs barriers; FEX developers suggested disabling it for speed on Arm; risk: breaks multithreaded games that rely on ordering (Wine's mixer thread, game threads) | **inconclusive**: 31.5 and 26.1 fps (mean 28.8 = baseline); re-test in a controlled memory state with n >= 4 |
| E | FEX `Multiblock` | default on since FEX-2503 (JIT time halves with it); the shipped native-Linux config sets it explicitly, Windows games use FEX's default | check the default of the shipped 2610 modules |
| F | Steam UI competition while a game runs (nice/`SCHED_IDLE`, cgroup `cpu.weight`, pause the web helpers) | the UI uses 1-1.5 cores navigating; idle it is near 0; the memory plan (cgroup + recycle) is the same mechanism | `stream` shows `ui` CPU during the game |
| G | Output path: game window -> Xwayland -> sway (scale 2) | extra copies and composition for a windowed game; fullscreen could scan out directly | untested |
| H | SIGILL fallback for LSE instructions (`libwispos-ldadd.so`) | 7.4 M traps in 3.4 min were measured for the Steam client; any LSE code in a game's process pays this; shows as high `sys` next to `user` | `stream` game user/sys split |
| I | Wine: wined3d vs native OpenGL path, esync/fsync, `WINE_CPU_TOPOLOGY` | Q3 is OpenGL (no wined3d); Direct3D games pay wined3d; WoW 1.12.1 has its own OpenGL renderer (`-opengl`) | per API, later |
| J | Memory: zram CPU cost, swappiness 180, page-cluster 3 (should be 0 for zram) | kswapd and compression take cores when the game is near the memory floor | `stream` swap rates and PSI |
| K | THP/KSM/hugepages, `vm.*`, scheduler (HZ 300, PREEMPT), irq affinity | small, cheap to try | untested |
| L | Thermal headroom | 39 C idle, 62 C seen in play, no cooling: sustained clocks could be throttled | `stream` temp and clocks |
| M | Emulator alternatives | Box64 cannot run 32-bit; Box86 + x86 Wine is a different stack (no Proton); FEX is the only 32-bit-capable option here | ruled out for now, reason recorded |
| N | **Steam UI rendering while nobody sees it**  (panel blanked, or a game on screen) | measured 2026-10-11: ~95% of a core at idle with the panel OFF (screensaver); 16-49% of a core behind a running game; the UI's memory grows ~75 MB per game launch | `ui-freeze` experiment queued in the sweep; a product rule (freeze when the panel is off or a game owns the screen) is the likely fix |
| O | **Page cache starvation** (the game's data files re-read from the SD card) | measured 2026-10-11: with Steam's UI grown after two launches, the Q3 main thread spent ~8% of the demo blocked in `readv`/block IO and the run fell from 29.5 to 27.8 fps; the same decompression cost shows up as zstd in the kernel profile (squashfs refaults) | follows from N and the memory plan |
| P | **UI page-out as a stabilizer** (prototype of the locked memory plan) | measured 2026-10-11, n=4 each: baseline 24.3/29.4/28.1/23.8 (mean 26.4, sd 2.8), UI paged out 28.1/27.1/27.4/27.8 (mean 27.6, sd 0.4): the slow mode disappears | build it as the memory plan (cgroup + recycle); then re-run the FEX levers on top, n >= 4 |
