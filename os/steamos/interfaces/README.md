# Valve's SteamOS D-Bus interface definitions (vendored, unmodified)

Both files are MIT-licensed (the licence text is in each file's header) and are used as the test oracle for `wisp-steamos-manager` (`os/test_steamos_manager.py` compares the
daemon's introspection with them, member by member). They are data, not code we build from.

| File | Source | sha256 |
| --- | --- | --- |
| `com.steampowered.SteamOSManager1.xml` | https://gitlab.steamos.cloud/holo/steamos-manager/-/raw/main/data/interfaces/com.steampowered.SteamOSManager1.xml (fetched 2026-10-11) | `0c8c181501d1e24480d2394369c362770d32424e1d107014aa86639e185d405c` |
| `com.steampowered.Atomupd1.xml` | KDE Discover's copy of Valve's version-1 interface, https://lxr.kde.org/source/plasma/discover/libdiscover/backends/SteamOSBackend/com.steampowered.Atomupd1.xml (page text extracted, line numbers stripped; fetched 2026-10-11) | `552a4b64a6c657bb9007758a514764be4846493e181221d3b06155ad5eb470bc` |

Known gap: Valve's own atomupd repository is behind a login, so the CURRENT atomupd interface is not available. Steam calls `Atomupd1.DisableHttpProxy()` (seen in Steam's own log on a real device), which this version-1 file does not have; the daemon implements it and the test lists it as the one documented extra member.
