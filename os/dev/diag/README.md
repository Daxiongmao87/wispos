# Device diagnostics for the Steam UI start (dev only, never in an image)

Copied to `/storage/wispos/dev/` on a dev device and run there over SSH (device has `python3`, `perf`, `readelf`, `lsof`; BusyBox `find` has no `-printf`).
They only observe; each restores the stock launcher/service afterwards.

| file | what it does |
| --- | --- |
| `xwins.py` | lists X11 windows on Xwayland with map state and size (ctypes/libX11, no packages) |
| `xgeom.py N` | samples top-level X window geometry at 10 Hz for N s and reports changes |
| `cdplib.py`, `cdp2.py` | stdlib Chrome DevTools client; attaches early to Steam's UI pages, keeps the console log, and on a stall samples the JS stack with `Debugger.pause` and prints source around the hot frames. Needs the marker file `/storage/wispos/runtime/steam/.cef-enable-remote-debugging` before Steam starts (remove it afterwards: it opens port 8080 on localhost) |
| `start-loop.sh N` | N plain Steam starts through a systemd drop-in, classifies each (visible / black / stalled) and profiles a stall |
| `replay_app.py APPID DIR EXE` | launches another Steam app by hand through reaper/Proton with logging (`REPLAY_WRAP=1` = through steam-launch-wrapper like Steam; falls back to Steam's own environment when no capture exists). Hides Steam-launch bugs: it does NOT use Steam's `LD_PRELOAD` |
| `replay_exact.py DUMP [SECS] [K=V..]` | re-runs the exact command + environment Steam gave a game, from a dump written by a wrapper launch option; override single variables to bisect (see the Steam-launch playbook in `docs/WISPOS-NOTES.md`) |
| `xin.py key NAME \| click X Y \| move X Y \| drag ..` | synthetic keyboard/mouse into the X display (XTEST) to drive a game's own menus over SSH. A test driver only, NOT an input mapping (controller mapping is Steam Input's) |
| `mem-guard.sh` | stops the Steam session if MemAvailable stays under 90 MB for ~9 s (unattended installs/tests) |
| `lse-bench.c` | cost of one emulated LSE atomic through the SIGILL fallback vs an LL/SC loop (build `-march=armv8.1-a`, run with `LD_PRELOAD=libwispos-ldadd.so`; time the process from outside, its own stdout is lost under the preload) |
| `launch-time.sh [APPID] [SCALE]` | press Play on an open game page and time the NEW game window (sway container id) with Steam's launch-task log; kills the game after |
| `launch-stages.sh [APPID] [SCALE]` | same, plus the first time each stage appears (pressure-vessel, locale compile, evaluator, proton, wineserver); refuses to run on a dirty start |
| `ui-reclaim-test.sh [SIZE]` | cgroup v2 experiment: move the steamwebhelper processes into their own memory cgroup, ask the kernel to reclaim SIZE (memory.reclaim), report what went to zram and what came back |
| `mem-sample.py [OUT] [PERIOD]` | every few seconds: MemAvailable, SwapFree, zram mm_stat and the top processes by anonymous+swapped memory (grouped by name); the tool for "who holds the memory when a game run dies" |
| `fps-run.sh` | the whole Worms fps procedure, detached from ssh (`systemd-run --unit=wisp-fps-run bash fps-run.sh`): Steam restart with the HUD and `/nointro` launch option, stall-aware wait for the UI, sampler, game page, `worms-fps.sh`; output in the unit journal (`journalctl -u wisp-fps-run -o cat`) |
| `game-first-launch.sh` | first launch of ANY Steam game (`APP=<appid>`) with the shipped defaults: press Play, output scale 1 after 3 s, wait for the new `steam_app_<id>` window, a screenshot every 15 s for 2 min, game left running; no game-specific input (detached: `systemd-run --unit=wisp-game-launch --setenv=APP=... bash game-first-launch.sh`) |
| `awake.sh [SECONDS]` | keep the panel on for a LIMITED time (default 900 s), then it blanks by itself (a transient timer removes the inhibit even if the calling script dies); `awake.sh 0` = blank normally. Dev scripts use this, never a bare `wisp-screen inhibit on` (the operator's backlight) |
| `steam-open.sh steam://...` | open a `steam://` URL in the running Steam (e.g. `steam://nav/games/details/<appid>` shows the game's page with Install/Play) using Steam's own environment; one pad press first if the screen is on the screensaver |
| `worms-fps.sh` | Worms Armageddon deathmatch frame rate from the Gallium HUD labels (launch option `GALLIUM_HUD=fps,frametime %command%`), one screenshot per menu step plus 12 HUD crops; drives the game's menus with `xin.py` (blind clicks: read the step shots) |

Findings from these are in `docs/WISPOS-NOTES.md` section 4d. Never bind-mount over `/usr/bin/wisp-steam-session` (shared mounts stack); use a drop-in.
