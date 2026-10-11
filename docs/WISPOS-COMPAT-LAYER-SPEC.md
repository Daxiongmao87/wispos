# WispOS compatibility-layer builds: spec and constraints

Status: **binding spec, not yet implemented** (operator decision, 2026-10-10: "Let's lock this in as a spec and constraint. Removes the need for an extra dialog or chord-triggered dialog").
Authority: this file, `docs/WISPOS-NORTH-STAR.md` (standing rule), `docs/WISPOS-NOTES.md` (evidence). Where this file and a note disagree, this file wins until the operator amends it.

## 1. Why this exists (facts, measured 2026-10-10)

The H700 is a Cortex-A53 (ARMv8.0, no LSE atomics). The stock components that run games are not all built for it:

- Valve's FEX tool (Steam app 3127680, runs native Linux x86 games): `FEX` 257 LSE instructions, `FEXServerManager` 8, `FEXCompatTool` 8 ...; it exits with SIGILL (132) here, and the tool deletes `LD_PRELOAD`, so our SIGILL fallback cannot cover it. No native Linux x86 game starts on the stock stack.
- The community ARMv8.0 Proton we ship (Azkali, 11.0-1-beta5) carries FEX-2605, which has no DEP-disabled mode, and its Wine never notifies FEX: pre-NX 32-bit programs that run code from read/write memory die with `NoExec` (RollerCoaster Tycoon Deluxe, Carmageddon 2, Quake III's QVM JIT).
- Valve's own `proton_11.0` (FEX-2607 + a Wine that notifies) fixes the DEP gap but is built with LSE.
- The FEX project's own PPA ships ARMv8.0 packages (`fex-emu-armv8.0`, `fex-emu-wine`, release 2610): the Windows DLLs have 0 LSE instructions and the DEP export; the Steam-integration executables (`FEXCompatTool`, `FEXServerManager`) are not in them.

Operator rulings that frame the solution: no custom FEX builds that WE make and distribute ("I thought I said any custom fex builds goes against the point?"); adopting upstream binaries is the right solution when they exist; and, for the case where none runs on this CPU, **the device builds unmodified upstream source locally** (the same idea as Steam compiling shaders on the device).

## 2. Principle

Stock first, then official upstream binaries, then a local build of unmodified upstream source. We never ship a binary we built; we ship recipes pinned to upstream commits. Everything that is built is built for the CPU it runs on.

Decision order, evaluated per component (Valve FEX tool executables; FEX Windows DLLs in the Proton tool; any Wine DLL that must match them):

1. **The stock component runs on this CPU** (the CPU has LSE, or the file passes the census in 5.3): use it. Nothing is built.
2. **An official upstream ARMv8.0 binary of the matching version exists** (FEX project PPA package, community ARMv8.0 Proton rebuild): download it, pin its hash, use it. The OS image may carry such official binaries as a baseline (never binaries built by us).
3. **Otherwise: build locally** from the upstream source at the version Steam/Proton pins (section 4), on the device, automatically.

### 2a. No host of ours (operator, 2026-10-10: "Having to rely on external host is not preferrable")
We never run or pay for a server that devices must reach. The device fetches only from UPSTREAM origins, with hashes pinned in the repo, and assembles/builds locally: FEX source and release archives from the FEX project (GitHub), the FEX project's own package pool for the official ARMv8.0 files (`fex-emu-wine`), the llvm toolchain from its upstream releases, the FEX RootFS from the FEX project's server, Proton/runtimes through Steam's own content system, and the community Proton tool from its upstream release (already pinned in `steam/compat-tool.lock.json`). The image may carry a baseline of official upstream binaries. Step 2 of the decision order (official binary) therefore means "fetch from the upstream origin and verify the pinned hash", not "we host it".
The one component with no single-file upstream origin is Valve's LSE-free `wow64.dll` (the half that tells FEX the process has DEP disabled): it exists only inside Valve's `Proton 11.0 (ARM64)` Steam app (about 1.9 GB). VERIFIED 2026-10-10: that app is NOT anonymously downloadable (an anonymous Steam login gets `missing_token`, and AccessDenied for its depot key; the x86 runtimes are anonymous, Valve's ARM64 tool apps are not), so steamcmd cannot fetch it without the operator's login (docs/WISPOS-NOTES.md, last block). DECIDED (operator, 2026-10-10: "a sounds fine"): (a). It is a post-sign-in step with NO custom UI: the download is an ordinary Steam install, so Steam's own Downloads page shows its progress. Not yet verified: how to start it WITHOUT a dialog and that the tool app appears in the queue. Known: `steam -ifrunning steam://install/4628740` opens Steam's own install dialog (it waits for a press: not unattended; `os/dev/diag/steam-open.sh`). Candidate to test on the first fresh flash (where Valve's Proton app is not installed, because the seeded default tool is the community one): write `steamapps/appmanifest_4628740.acf` with `StateFlags` 2 (update required) before Steam starts, so Steam queues the download itself; its progress/StateFlags (1026 -> 4) are then the completion signal that `wisp-compat-gate` already reads. IMPLEMENTED 2026-10-11 as `wisp-wow64-valve` with the manifest candidate (the dialog route is not used); whether Steam actually downloads from the minimal manifest is the first thing to read on the first fresh flash. If it does not, fall back to a dialog-free alternative found then, or to Steam's own install dialog (one press). The alternatives stay as fallbacks. Options considered: (a) after the operator has signed in, let Steam install that app (it honours Steam's download settings) and copy the one file out, hash-checked (zero hosting, costs the download and about 2 GB of disk, Steam then keeps the app updated); (b) build `dlls/wow64` from Valve's Wine source at the pinned tag on the device (heavier: Wine's build tools plus a PE toolchain); (c) wait for the community Proton build to carry it. Leaning (a).

## 3. No extra UI

No extra first-run page, no options dialog, no chord. The feature is on by default and has no switches. A game that needs a layer that is not ready yet must not hang or fail silently. DECIDED (operator, 2026-10-10: "Failed launch state for now"): for a native Linux game, the launch wrapper checks the layer's ready marker and, if it is missing, exits non-zero at once with one line in its log naming the missing layer, so Steam shows its own failed-launch state within seconds. No toast, no dialog of ours, for now. Windows games are not gated (they run on the community tool's DLLs while the `wow64.dll` step is pending; only pre-NX 32-bit programs can fail, and that is a documented limit, not a message). Not yet verified on the device: how Steam's UI presents a non-zero wrapper exit. A toast (non-modal layer-shell line, auto-dismissed) was proposed and is deferred.

## 4. What is built, and which version

- The version to match is not "newest". Linux executables: the commit in the FEX tool app's `VERSIONS.txt` (`FEX describe` / `FEX bash`; today `FEX-2607-76-g37265b109`). Windows DLLs: the FEX version the installed Proton tool pins.
- Trigger: first run, and any change of the app's `buildid` in `steamapps/appmanifest_<appid>.acf` (3127680 for the FEX tool, the Proton tool's own app/lock for the DLLs). Steam updates these tool apps through its content system, separately from client updates (client package `steam_client_publicbeta_linuxarm64` is another mechanism).
- Steam overwrites the app's files when it updates them, putting the stock (LSE) binaries back. The build output therefore lives OUTSIDE Valve's folder, and a hook re-applies the last good build immediately after Steam changes the app, then builds the new pin. The last good build is never deleted before the next one is verified.
- Versions are pinned (commit hash, archive hash); no tracking of upstream HEAD.

## 5. When things run

### 5.1 Downloads (toolchain, source archives, packages, guest root)
Follow Steam's own download settings: the update window (`AutoUpdateWindowEnabled` and its times; on the device today only `AutoUpdateWindowEnabled = 0` is stored), the throttle and the "downloads during gameplay" setting when they are present (Steam stores only non-default values; read them from `config.vdf`/`localconfig.vdf`; verify the exact keys when Steam writes them). Absent keys mean Steam's defaults. No plug or battery requirement for downloads. Resumable, hash-verified.

### 5.2 The compile
Runs only when ALL hold:
- no game is running (no `reaper SteamLaunch` process tree);
- no input for 5 minutes (DECIDED, operator 2026-10-10 "Agreed to all"); "input" is what `wisp-screen-idle` already watches (the `/dev/input/event*` devices it does not ignore), so there is one authority for what counts as activity;
- no Steam download, install or content staging in progress (heavy SD writes coincided with Steam client assertion failures on 2026-10-10);
- battery rule: **plugging in is not required**, except below 25%: when unplugged at or below 25% the build pauses (resumes when charging again or when above 25%); a build only STARTS on battery at 50% or more, so it does not start and immediately pause (DECIDED).
"Plugged in" is read from the power supply's online flag, NOT from `status`: this device reports `Not charging` at 100% while on USB power.

While it runs: low priority, at most 2 jobs, memory capped (cgroup `memory.max`, about 600 MB; the heaviest compile step needs 269 MB), frozen (SIGSTOP/cgroup freezer) the moment a game starts or pad input arrives, resumable (incremental build tree). Thermal: the build pauses at 70 °C and resumes at 60 °C (DECIDED; which sensor is read, the SoC thermal zone, is to be verified on the device).

### 5.3 Verification before anything is activated
- Source archives and toolchain archives are hash-pinned; builds happen in a scratch directory.
- Census gate: the output is scanned for LSE/RCPC instructions (the scan in `tools/fex-linux-build.sh`: runtime-gated outline-atomic helpers and FEX's own hardware probe account for a handful; hundreds mean the flags were lost); failure = not activated.
- Smoke test before activation: the FEX executable runs a trivial guest program (`uname -m` in the guest root printed `x86_64` in the 2026-10-10 experiment).
- Atomic install, last good build kept, originals kept; a receipt records pin, flags, tool versions, hashes and date; failures log and fall back to the previous good build or the stock component.

## 6. Constraints that stay

- Steam Input is the only controller-mapping layer. No per-game changes. No Wine/FEX source patches; recipes build unmodified upstream at upstream pins.
- Official upstream binaries beat local builds (section 2); the request drafts in `docs/upstream/` stay the first line.
- Disk: about 3 GB transient (toolchain about 1 GB, build tree about 1.2 GB, source about 0.5 GB), removed after the build; steady state tens of MB. Steam's shader cache already uses disk without limits, which is the precedent.
- Measured/estimated numbers (host): FEX Linux build 14.5 CPU-minutes over 285 steps, slowest object 13.7 s; Windows DLLs about 6 CPU-minutes. Device time is an ESTIMATE of 1-1.5 hours at 2 jobs (not measured); battery cost per build is not measured.

## 7. Where the experiment's recipes are

`tools/fex-linux-build.sh` and `tools/fex-win-build.sh` (host-side, labelled NOT ADOPTED; they record the exact flags and the LSE census); `tools/check-upstream-builds.sh` (read-only watcher of upstream releases). Prerequisites on the device that do not exist yet: an aarch64 toolchain (clang/lld/cmake/ninja or llvm-mingw: prebuilt aarch64 releases exist), a way to unpack the FEX RootFS (the device has no `unsquashfs`; a temporary loop mount works), the guest root (FEX project RootFS, Ubuntu 24.04, 501 MB download, 1.9 GB unpacked).

## 8. Open items (not decided; do not guess)

1. DECIDED: idle time 5 minutes; input = what `wisp-screen-idle` watches.
2. DECIDED: start on battery at 50% or more; pause at 25% (section 5.2).
3. DECIDED: pause at 70 °C, resume at 60 °C (sensor to verify).
4. DECIDED for now (section 3): refuse the launch of a native Linux game whose layer is not ready, so Steam shows its failed-launch state; no toast. Still open: verify that presentation on the device, and revisit a toast if it proves unclear.
5. Toolchain source and hash pins; guest-root source and pin; the re-apply hook mechanism (path watcher on the app's files or on `appmanifest_<id>.acf`).
6. Valve's `wow64.dll`: source decided = (a), Steam installs Valve's Proton app after sign-in (section 2a). Still open: the unattended trigger, the pinned known-good hashes (the app may move past 11.0-2e), the smoke test, and what a Windows game needing the DEP fix does if it launches before the step has finished.
7. DECIDED (implemented 2026-10-11): the image carries the FEX project's official `fex-emu-wine` package files as the baseline: four files (`libwow64fex.dll`, `libarm64ecfex.dll`, and their unix `.so` companions, about 12 MB), MIT licence (the package's `copyright` file is carried too), UNMODIFIED, pinned by URL and sha256 in `steam/official-fex.lock.json` and fetched from the FEX project's package pool at image build time (the host caches the package; a pin whose URL has gone is re-pinned only after a retest). `wisp-fex-official` copies them into the community Proton tool before every Steam start (the originals are kept once under `.wisp-orig/`). The `fex-emu-armv8.0` package (Linux executables) is NOT carried: the Steam-integration executables are missing from it, so it cannot replace Valve's tool (section 1).
