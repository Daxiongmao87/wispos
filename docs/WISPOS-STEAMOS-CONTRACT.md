# The SteamOS contract: what Steam's UI asks the OS for, and what WispOS will answer

Written 2026-10-11 (host-only research, nothing here has run on the device yet). Serves four of the 0.1.0 efforts at once (docs/WISPOS-OS-DESIGN.md, "Remaining efforts before 0.1.0"): OS updates, switching between Game mode and a desktop mode, the Steam-side format button, and Steam's own performance menu.

## Sources (public; not copied into this repository)
- Valve's `steamos-manager` README and D-Bus interface `com.steampowered.SteamOSManager1`: https://gitlab.steamos.cloud/holo/steamos-manager (`data/interfaces/com.steampowered.SteamOSManager1.xml`).
- The update interface `com.steampowered.Atomupd1` (Valve's `steamos-atomupd`), read from KDE Discover's copy: https://lxr.kde.org/source/plasma/discover/libdiscover/backends/SteamOSBackend/com.steampowered.Atomupd1.xml (Valve's own repository asks for a login).
- Steam's own log on our device with `-steamos3 -steamdeck` (`logs/steamui_steamos.txt`), see docs/WISPOS-NOTES.md "Game-performance audit".

## The key fact
Valve's README: SteamOS Manager "abstracts Steam's interactions with the operating system ... The interface may be fully or partially implemented. The Steam client will check which features are available at startup and restrict the settings it presents to the user based on feature availability." So WispOS ships a SUBSET, honestly reported, and the UI shows exactly what works. Nothing has to be faked.

## `com.steampowered.SteamOSManager1` (interfaces, grouped by what they buy us)
| Interface | Methods / properties | WispOS answer | Effort it serves |
| --- | --- | --- | --- |
| `Storage1` | `FormatDevice`, `TrimDevices` | the existing `tf2-card` formatter (the TF2 slot only); the `steamos-polkit-helpers` scripts stay for older Steam | SD format (done as helpers; this is the newer path) |
| `SessionManagement1` | `SwitchToDesktopMode`, `SwitchToGameMode`, `SwitchToLoginMode`, `SwitchToDesktopSession`, `ValidDesktopSessions`, `DefaultDesktopSession`, `DefaultLoginMode` | stop Steam's session, start the desktop session, and back (Steam logged: "SteamOSManager version predates session management interface") | desktop mode (effort 3) |
| `CpuScaling1` | `AvailableCpuScalingGovernors`, `CpuScalingGovernor` | cpufreq governors (cpufreq policy0) | Steam's performance menu |
| `GpuPerformanceLevel1` | `AvailableGpuPerformanceLevels`, `GpuPerformanceLevel`, `ManualGpuClock`, `ManualGpuClockMin/Max` | the GPU devfreq node (min/max/governor; 420-648 MHz here) | performance menu ("Set manual gpu clock" was attempted by Steam) |
| `PerformanceProfile1`, `GpuPowerProfile1`, `TdpLimit1` | profiles, TDP limit | profiles = governor/frequency caps; TDP = a CPU frequency ceiling (measure first) | performance menu ("invalid profile" was logged) |
| `WifiPowerManagement1`, `WifiBackend1`, `WifiDebug1` | power-management state, backend name | NetworkManager with the iwd backend (reports its real state) | Wi-Fi settings |
| `BatteryChargeLimit1`, `FanControl1`, `CpuBoost1`, `CpuScheduler1`, `LowPowerMode1`, `HdmiCec*`, `ScreenReader*`, `FactoryReset1`, `UpdateBios1`, `UpdateDock1`, `AmbientLightSensor1`, `FirmwareDebug1` | | NOT implemented (the UI hides them). `ScreenReader*` and `FactoryReset1` are candidates later | none for 0.1.0 |
| `Manager2` | `ReloadConfig`, `DeviceModel` | a fixed model string (identifies the handheld) | all |

## `com.steampowered.Atomupd1` (the OS update interface)
`CheckForUpdates(options a{sv}) -> (updates_available a{sa{sv}}, updates_available_later a{sa{sv}})`, `StartUpdate(id s)`, `PauseUpdate`, `ResumeUpdate`, `CancelUpdate`, `SwitchToVariant(variant s)`; properties `UpdateStatus u`, `ProgressPercentage d`, `EstimatedCompletionTime t`, `UpdateBuildID s`, `CurrentVersion s`, `CurrentBuildID s`, `Variant s`, `KnownVariants as`, `UpdatesAvailable`, `UpdatesAvailableLater`, `FailureCode s`, `FailureMessage s`. Steam also called `YldSetAtomUpdateProxyConfig` and `steamos-mandatory-update check` (exit 127 here, "command not found"), and `jupiter-initial-firmware-update` (127): those two commands need to exist and say "nothing to do".
- WispOS answer: an `Atomupd1` service whose "updates available" comes from a signed manifest on an upstream origin we do not operate (GitHub Releases of this project; BINDING: "No host of ours"), whose `StartUpdate` downloads the SYSTEM image, verifies its pinned hash and signature, writes it NEXT TO the running SYSTEM on the boot partition (the old file is kept) and reports progress through the properties. Rollback = the kept file plus a boot-attempt counter (the bootloader side does not exist yet). Variants = release channels (stable only for 0.1.0).
- Not yet known: how Steam asks for a reboot after an update (capture once the daemon answers), and the full current member list of Valve's atomupd interface (the public file is version 1; `DisableHttpProxy` exists beyond it).

## Which bus, which activation (measured on the device 2026-10-11, docs/WISPOS-NOTES.md "Steam's SteamOS D-Bus calls")
- `SteamOSManager1`: the SESSION bus (Valve's README: "a public DBus API on the session bus"), started by `StartServiceByName` -> `/usr/share/dbus-1/services/com.steampowered.SteamOSManager1.service`. In our image Steam runs under `dbus-run-session`, so the daemon starts inside Steam's session as Steam's user.
- `Atomupd1`: the SYSTEM bus (Steam sends `Atomupd1.DisableHttpProxy()` at start-up), `/usr/share/dbus-1/system-services/com.steampowered.Atomupd1.service` + `wisp-steamos-manager.service`. `DisableHttpProxy` is missing from the public interface file; the definitions we have: `os/steamos/interfaces/` (Valve's `SteamOSManager1` XML from gitlab.steamos.cloud/holo/steamos-manager, MIT, and the version-1 `Atomupd1` XML as KDE Discover carries it, MIT). Update dictionary keys (now known): `variant s`, `version s`, `estimated_size t`, `update_type u` (0 minor, 1 major), and `requires s` for updates available later.
- One binary, two roles (`--atomupd BUS`, `--manager BUS`), one process per bus. Measured on the HOST (x86-64, not the device): about 3.1 MB resident, of which 0.25 MB private (the rest is shared library pages), one thread; the device figure is still to be taken.

## Corrections from running Steam against our daemon (device, 2026-10-11, docs/WISPOS-NOTES.md "The SteamOS integration with our daemon answering")
- **The UI's update path is SCRIPTS, not `Atomupd1`.** Settings > System > Software Updates runs `/usr/bin/steamos-polkit-helpers/steamos-update` (`--supports-duplicate-detection` -> 0; `check` -> 0 = available / 7 = none; install -> `NN%` progress lines), then "system restart required" and a Restart button. `Atomupd1` was called once, at start-up, for `DisableHttpProxy`. Our `os/rootfs/usr/bin/steamos-polkit-helpers/steamos-update` implements that contract over `wisp-update`; the `Atomupd1` daemon stays for other clients (KDE Discover) and for the proxy call. Steam's binaries name many more helper scripts (`steamos-reboot`, `steamos-select-branch`, `steamos-session-select`, `steamos-devkit-mode`, `steamos-mandatory-update`, ...): see the notes for the list; not yet implemented.
- **The manager is looked up ONCE, at Steam's start-up**, and then Steam calls `ObjectManager.GetManagedObjects` at `/` on it. So the manager role must already own its name when Steam starts (`wisp-steam-launch` does that inside the session bus) and must answer at `/` (the daemon now does); D-Bus activation alone is too late.
- **`-steamos3` is the switch; `-steamdeck` is not needed.**

## How to build it (decision for the operator, my recommendation first)
1. **One small C daemon on `sd-bus`** (libsystemd is already in the image; no Python D-Bus binding is; a Python daemon would also cost ~15 MB resident, the size of the watchers we are trying to remove). Two bus names, one process. Cross-compiled in the image build with the existing aarch64 toolchain against systemd's public headers; unit-tested on this host by running the same source for x86-64 on a private bus (`dbus-run-session` + `busctl`, both present here).
2. D-Bus policy and activation files in the image (system bus), `wisp-steamos-manager.service`, started on demand.
3. A property/method-level test table generated from Valve's XML so a mismatch fails the build.
Alternatives: Python with ctypes over libdbus (a vendored binding, heavier, slower); Valve's own Rust `steamos-manager` built from unmodified source for aarch64 (allowed by our rules: the ban is on custom FEX builds; but it needs a Rust toolchain in the image build and its helper scripts and policy files assume SteamOS's own hardware-support packages, so it is the heavier road and the interface subset would still need our own backends).

## Verification still to do on the device (not while a measurement is running)
- That Steam really enables each setting when the matching interface appears, one interface at a time (start with `SessionManagement1` and `Storage1`, then the performance group).
- The object paths and bus names Steam connects to (from Valve's README and the XML; confirm with `busctl monitor` while Steam starts with `-steamos3`).
- Whether Steam's `-steamos3`/`-steamdeck` switches are needed for the UI to use these at all (the settings gear with a warning mark appeared with them; the shipped command line has neither), and what else those switches change.
