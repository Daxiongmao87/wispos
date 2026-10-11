# WispOS step durations (measured; read before waiting on anything)

One home for how long recurring steps take, so a wait is judged against a number instead of re-checked and second-guessed (operator, 2026-10-10: "you should be
recording how long each step takes so you're not second guessing every time"). **Rule:** before starting or waiting on a step, read its row; check at the expected time,
not continuously; investigate only when a step runs past about twice its norm or its stall line; after any new measurement, add or update the row (date, n, range, source).
Device clock is UTC, the host's is UTC-4. All rows are measured unless marked "estimate".

## On the handheld (H700, 1 GiB, 4x A53, Wi-Fi ~3-4 MB/s down, SD card writes slow)

| step | normal | tail / stall line | n, date, source |
|---|---|---|---|
| power-on -> network announce reaches the host (dev image) | services up in 11 s, hello with an address at uptime ~61 s | - | 1 fresh flash, 2026-10-10 (`devices.jsonl`) |
| first-run: Wi-Fi screen done -> our client + compat-tool download and unpack (`steam-install`) | **~13 min** (1.4 GB at ~4 MB/s; the download is nearly all of it) | no stall seen; resumable | 1, 2026-10-10 (04:49 -> 05:02:20, `wisp-setup state=done`) |
| first Steam start: Steam's own self-update (it is ahead of our pinned client) | **9 min 46 s**: download 666 MB 3:14, extract 6:03, install/clean/verify 0:24; then Steam restarts | the extraction phase is write-bound on the SD card (3-45 MB/s) and looks idle at 3% CPU; it is not stuck | 1, 2026-10-10 (`logs/bootstrap_log.txt` 05:02:27 -> 05:12:13) |
| Steam start -> UI-ready marker in the console log | **102-111 s** (6 of 8 restarts: 103, 102, 111, 104, 109, 111) | slow tail 270-355 s (about 1 in 5; splash spinner still turning); real stall (no window in sway, renderer 100% of a core for 8+ min) 1 of 8 | 8 restarts, 2026-10-10 (`stall-hunt.sh`); earlier runs 109-131 s |
| Steam restart after its self-update -> sign-in screen on the display | **<= 2 min 23 s** (restart 05:12:11, screen seen 05:14:34; no stall) | - | 1, 2026-10-10 |
| **whole first boot of a fresh flash: power-on -> Steam sign-in screen** | **~26 min** (04:48:31 -> 05:14:34: 13 min our installer, 9:46 Steam's self-update, ~2 min UI start, rest Wi-Fi setup) | dominated by two 4 MB/s downloads; Steam updating itself right after our install is expected and NOT chased (operator, 2026-10-10: a normal thing, pointless to chase) | 1, 2026-10-10 |
| watchdog action on a real stall | restarts at 7 min up + 4 checks of 15 s = **~473-560 s** | max 3 restarts per 30 min | 3 firings, 2026-10-10 (`session.log`) |
| Play -> game window (Worms, custom tool, container) | **114-136 s** (n=11: 126, 132, 121, 133, 131, 114, 127, 125, 132, 136, 125) | 162-184 s when memory was thrashing or `csmt=0`; never above 184 s | 2026-10-10 runs (`worms-fps.sh`) |
| **Play -> game window, FIRST launch on a fresh card** (Wine prefix made by this launch: its `version` file was written at +93 s; the launcher patch skips the install script) | **131 s**, no longer than a later launch | - | 1, 2026-10-10 05:34:23 -> 05:36:34 (`worms-fps.sh`); the earlier estimate of +3-4 min for prefix + install script was wrong |
| Worms Armageddon install after sign-in (975 MB: the game + Steam's Linux container runtime, at ~4 MB/s) | **~9 min** (about 05:15 -> 05:24 device clock) | write-bound like the Steam self-update; do not chase | 1, 2026-10-10 |
| **Play -> game window, Carmageddon 2** (second game) | **first launch 130 s** (made the prefix); **later launches 69 s and 73 s** (prefix present, Wine logging on) | the window is a black frame for a game that then crashes (see the notes: FEX NoExec), so this is "window appears", not "game runs" | 3 launches, 2026-10-10 05:55, 06:03, 06:12 |
| Steam restart -> UI-ready marker, series today after the dev image settled | 91 s (06:00:55 -> 06:02:26), 81 s (05:42:53 -> 05:44:14), 84 s (06:09:46 -> 06:11:10) | slow tail seen earlier today 270-355 s | 2026-10-10 |
| Carmageddon 2 install (app 282030: download 171.9 MB, staged 333.7 MB, Steam's runtime already present) | **1 min 56 s** (05:53:10 -> 05:55:06 device clock, `StateFlags` 1026 -> 4) | no dialog on Install; the Worms estimate of ~4 MB/s was too pessimistic for a small game | 1, 2026-10-10 |
| Steam's idle SCREENSAVER starts after a quiet period (looks like a broken, cropped UI) | seen at ~6 min after the last input on a restarted Steam (05:42:53 restart, seen 05:49:09) | one pad press ends it; `wisp-screen wake` does not; the panel also blanks ~80 s after a press without `wisp-screen inhibit on` | 1, 2026-10-10 (notes playbook item 6) |
| Worms' own first-run dialog on a fresh prefix ("running W:A for the first time under Wine ... WindowedMode_On") | waits for an answer, indefinitely, on the first frame | a driver that does not answer it stalls at a grey dialog (looks like a hang) | 1, 2026-10-10 (`s01-first-frame.png`); the game's own prompt, not an OS fault |
| Worms menu screen -> next screen (clicks) | 10-16 s (slow: the game renders its screens at 20-60 fps) | > 30 s: look at the screen, a click may not have registered | 2026-10-10 hand-driven runs; fresh card: 11-14 s |
| Worms Start -> match on screen (with `PAN_MESA_DEBUG=linear`) | < 12 s to a drawn world (HUD 7 fps while loading), **43.8 fps at +40 s** | **no frame for 3 s = the Mesa upload deadlock** (before the workaround: 7 of 7 hung; fresh card with shipped defaults: 0 of 1) | 2026-10-10; fresh card 05:39:55 |
| Worms 4-team deathmatch frame rate (HUD, 12 samples 4 s apart from +40 s) | **mean 46.2 fps, 36.9-52.9**; frame-time label 19.4-36.4 ms | below 30 fps = investigate | 1 match, fresh card, shipped defaults, 2026-10-10 05:40-05:41 (evidence `card-logs-20261010/worms-match-fresh-card/`) |
| zram / memory in a match | UI idle zram 261 MiB; match 417 MiB of the 450 MiB cap (old account); **fresh account: 254 MiB used at most (264 max), MemAvailable min 167 MB, PSI some max 11.4 / full 6.9 during the menus, 0.0 in the match, 0 GPU faults, 0 swap write-errors, 0 OOM** | cap reached = thrash (psi full > 10%) | 2026-10-10 (`mem-sample.py`); why the fresh account needs less is not established |
| **Quake III Arena (app 2200, 490 MB) install** | **121 s** (13:16:40 -> 13:18:41 device clock; about 4 MB/s) | no dialog on Install | 1, 2026-10-10 |
| **Quake III Play -> window** | **78 s** with both Steam dialogs answered 6 s and 13 s after Play (later launch, prefix present); first launch 149 s from the last dialog answer | the dialogs (CD Keys, On-Screen Keyboard) wait for a button press and block the launch without a timeout: 11 minutes lost while they sat behind the screensaver | 2 launches, 2026-10-10 13:19, 13:48 |
| Steam restart -> UI-ready, two more | 92 s (13:36:50 -> 13:38:22), 89 s (13:46:02 -> 13:47:31) | slow tail 270-355 s | 2026-10-10 |
| Panel off after the idle timeout (`wisp-screen-idle`) | **within 3 s of the next check once the inhibit flag was cleared** (`bl_power 4`, DPMS Off); the default timeout is 120 s of no real button press; the operator confirmed the panel was truly black | a held flag (`wisp-screen inhibit on`) keeps it lit forever: dev scripts use `awake.sh` (self-expiring) | 1, 2026-10-10 13:42 |
| **Half-Life (70, 667 MB) install** | **212 s** (14:38:21 -> 14:41:53) | no dialog on Install | 1, 2026-10-10 |
| First native-Linux launch: Steam fetches Valve's FEX tool (6 MB) + SLR soldier (192 MB download, 677 MB) + SLR scout | **~3.5 min** (14:42:28 -> 14:45:43); the launch that started it is dropped, press Play again | one-time per device | 1, 2026-10-10 |
| Steam restart -> UI-ready, more | 87 s (14:36:21), 103 s (14:33:38, shipped DLL restored). **No window past 8 min: 14:05:42, 14:13:42, 14:21:47, 14:26:49 (all with the patched libwow64fex.dll deployed) and 13:53:34 (before it)** | the watchdog restarts at ~473 s, at most 3 per 30 min | 2026-10-10 |
| FEX WOW64 DLL build (`ninja libwow64fex.dll`, llvm-mingw, 12 jobs on the 32-core host) | **36 s** (configure 5 s) | the build tree is ~1 GB, HDD only | 1, 2026-10-10 |
| **Valve's Proton 11.0 (ARM64) install** (app 4628740, 471 MB download, 2.0 GB on disk) | **314 s** (15:12 -> 15:17:50 device clock) | needs a fresh Steam: with a bloated Steam (swap 1.1 GB) the device stopped answering SSH for ~2 min | 1, 2026-10-10 |
| **Quake III Arena Play -> window** (windowed timedemo run, both dialogs answered) | **77 s** (16:05:22 -> 16:06:39); `CL_InitCGame` 20.2 s; demo runs 43.7 s | - | 1, 2026-10-10 |
| **Quake III `demo four` frame rate, 640x480 windowed, Mali-G31/Panfrost, shipped Mesa** | **1260 frames, 43.7 s = 28.9 fps** (target 30, floor 20) | below 20 = investigate | 1 run, 2026-10-10 16:06 (qconsole.log) |
| Carmageddon 2 Play -> window on FEX-2607 + Valve wow64 | **71 s** (the game then runs but renders black: nGlide) | - | 1, 2026-10-10 |
| Worms Play -> window on FEX-2607 + Valve wow64 (regression check) | **97 s** including the On-Screen Keyboard notice (answered 29 s after Play) | norm 114-136 s earlier | 1, 2026-10-10 |
| FEX-2607 build, both Windows DLLs (wow64 + arm64ec), 12 jobs | **under 2 min** (wow64 ~40 s) | needs ~1.2 GB free on the HDD | 1, 2026-10-10 |
| **Half-Life (70) Play -> engine start-up video, first native-Linux launch** (container built from scratch, rebuilt FEX, Ubuntu guest) | **~2.5 min** (Play 18:30:11, SLR log shows the video at 18:32:43) | later launches not yet measured | 1, 2026-10-10 |
| Blue Shift (287 MB) install / Play -> window | **102 s** install; **134 s** Play -> window | - | 1, 2026-10-10 |
| Day of Defeat (373 MB) install / Play -> menu | **63 s** install; menu on screen ~3 min after Play | - | 1, 2026-10-10 |
| FEX-2607-76 (Valve's tool commit) Linux cross build, 274 targets, 14 jobs, clang 18 + lld | **a few minutes** (configure 7 s) | needs ~1 GB free on the HDD | 1, 2026-10-10 |
| FEX RootFS Ubuntu 24.04 download on the device (501 MB, ~8 MB/s) / unpack to a directory (1.9 GB, loop mount + cp -a) | **62 s** / **~3 min** | the device has no unsquashfs | 1, 2026-10-10 |
| **Quake III Play -> window, stock official FEX DLLs + Valve wow64** | **87 s** | - | 1, 2026-10-10 21:01 |
| **Quake III `demo four` frame rate, stock official FEX 2610 DLLs + Valve wow64.dll** (640x480 windowed, Mali-G31) | **1260 frames, 47.7 s = 26.4 fps** (own FEX-2607 build earlier: 28.9) | below 20 = investigate | 1 run, 2026-10-10 21:03 (qconsole.log) |
| Half-Life 2 (13.4 GB) install through Steam | **~98 min** wall (19:03:28 -> 20:41), with a client stall, a pause and two restarts; 6.1 GB download at 1.3-5 MB/s, 11.1 GB staged | the client then thrashes memory/SD for ~15 min: restart Steam | 1, 2026-10-10 |

## On the host

| step | normal | note | source |
|---|---|---|---|
| image chain (`os/image-chain.sh`: release + dev build, verify, QEMU, dev-test) | **16 min 40 s** (release 2:17, dev 2:14, QEMU 8:33, dev-test 3:36) | needs ~25 GB free on the HDD | 2026-10-09 23:32 -> 23:49 host time |
| card backup (`os/dev/card-backup.sh`, 62.5 GB card -> 9.4 GB) | **42 min** (imaging 37:52 at ~26 MB/s card read; verify 4:20) | compressed size grows from ~3 GB to 9.4 GB; it looks stalled in the sparse middle but dd reads at a steady 26 MB/s (check `rchar` of `dd` on the reader PC) | 2026-10-09 23:58 -> 00:40 |
| card flash (`os/dev/card-flash.sh`, 1.1 GB write + read-back) | **1 min 22 s** | refuses unless the card's partitions are unmounted (the reader PC's desktop auto-mounts them) | 2026-10-10 00:42 -> 00:43 |
| stall hunt cycle (`stall-hunt.sh`) | ~3 min per Steam start | 8 starts = 28 min | 2026-10-10 |
| `test_steam_install.py` (26 tests) | 26-60 s | the host is slower while a build runs | 2026-10-10 |
| Format a 119 GiB SD card in the TF2 slot (GPT + one ext4, `tf2-card format`) | **53 s** wall (card held a ROCKNIX install, 201 MB used) | the card is a GD2S5 SDXC; the mount followed in <1 s | 1, 2026-10-10 |
| Sequential write to that card (`dd` 256 MB, `conv=fsync`) | **14.5 MB/s** (17.7 s) | slow for installs: a 13 GB game is ~15 min of write alone at this rate | 1, 2026-10-10 |
| Steam stop -> edit library folder -> UI ready (`steam-ensure-ui.sh`) | **110 s** (start 23:55:16) | Steam accepted the new library folder (2 libraries) | 1, 2026-10-10 |
| Release rootfs build (`build-rootfs.sh`, unsquash + prune + mksquashfs) | **1 min 37 s**; needs about **4 GB** of free disk at its peak | dev rootfs about the same | 1, 2026-10-10 |
| Image build + `verify_image` (`build-image.sh`, 28 checks) | **9 s** + **11 s** | 1.07 GB image; the old image is deleted at the start of the build | 1, 2026-10-10 |
| `qemu-test.sh` (boot the release flows under TCG) | **8 min 48 s** | the notes said ~4 min before; the host was busy | 1, 2026-10-10 |
| First-run Steam download on a fresh flash (client ~1 GB + tool 405 MB, unpack, `READY`) | **14 min 20 s** (02:43:42 -> 02:58) | ~6 MB/s; Steam then downloads its own 265 MB update and restarts (03:10-03:14) | 1, 2026-10-11 |
| Valve's Proton 11.0 (ARM64) app from the minimal manifest after sign-in (471 MB download, 2.0 GB staged) | **12 min 24 s** (03:32:52 -> 03:45:16) | download+staging 8 min 29 s, verify staged 3 min 43 s; the earlier warm-client install took 5 min 14 s | 1, 2026-10-11 |
| Quake III Arena (489.6 MB) install through Steam's Install button, boot card | **4 min 40 s** | into library 0; state 2 -> 1026 -> 4 | 1, 2026-10-11 |
| Quake III Arena first launch, shipped defaults (prefix creation, CD Keys + interstitial dialogs answered) | **5 min 9 s** (rungameid 04:29:15 -> `quake3.exe` 04:34:24) | second launch, prefix present: about 2 min 15 s | 1, 2026-10-11 |
| Quake III `demo four` timedemo, 640x480 windowed, delivered stock FEX set | **28.1 fps** (1260 frames in 44.8 s) | 26.4 fps with the same files copied in by hand, 28.9 with a custom build (ruled out) | 1, 2026-10-11 |
| Boot-splash QEMU test (`os/boot/qemu_splash_test.py`: boot, first-boot resize reboot, sshd up, status/ready through the FIFO), TCG | **1 min 44 s** | splash on the virtual panel at 9.6 s, sshd at 90 s; the test kernel build with the patched initramfs took a few minutes on 28 cores | 1, 2026-10-11 |
| Release rootfs rebuild with the SteamOS daemon (aarch64 compile + link) and the update helper | **90 s** and **98 s** (two builds) | the daemon adds under 1 s; same 4 GB peak disk | 2, 2026-10-11 |
| `os/boot/make-initramfs.sh` (assets, static splash build, patch the reference archive) | **2 s** | output 8.2 MB cpio | 1, 2026-10-11 |
| Host test suites: `test_steamos_manager` (34), `test_wisp_update` (27), `test_rootfs` (69), `test_splash` (16), `test_dev` (38) | **6.4 s**, **21.7 s**, **1.4 s**, **10.9 s**, **9.0 s** | the update helper tests run a local HTTP server with real downloads | 1, 2026-10-11 |
| Test kernel with the patched initramfs and the virtual GPU driver (`make -j28 Image`) | **a few minutes** (not timed to the second) | 33.8 MB Image | 1, 2026-10-11 |
| H700 kernel build with the patched initramfs (`os/boot/build-kernel.sh`, from the pinned sources, 28 cores) | **475 s** (7 min 55 s) | Image 33.7 MB; the embedded initramfs is byte-identical to the patched cpio | 1, 2026-10-11 |
| `qemu-test.sh`, 27 checks (userspace boot of the release SYSTEM under TCG, incl. the bus-activation checks) | **about 9 min 30 s** | splash test kernel, port 2232 | 3, 2026-10-11 |
| Quake III timedemo run through `perf-sweep.sh` (settle, launch, 1260 frames, teardown) | **about 8 min per run** | six runs back to back plus settles: 50 min | 6, 2026-10-11 |

## Not measured yet (estimate or unknown)
- A frame rate for any 3D game other than Quake III (Carmageddon 2 gets past the FEX crash but its GL path shows a black screen, see the notes).
- A match longer than ~3 min, and repeated match starts on the fresh card (n=1 so far there).
- Cost of `PAN_MESA_DEBUG=linear` on 3D games (only a 2D game has run with it).
