<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/wispos-badge-dark.png">
    <img src="docs/brand/wispos-badge-light.png" alt="WispOS" width="160">
  </picture>
</p>

# WispOS

A single-purpose operating system for the **Anbernic RG34XX-SP** handheld (Allwinner H700, four Cortex-A53 cores, 1 GiB of RAM, a 720x480 panel): it boots into Steam's gamepad UI
and then into the games you launch from your library, and does nothing else. Steam and its games run through the stock, unmodified compatibility layers (FEX-Emu and Proton for
ARM64). The goal is *general* compatibility of older Steam titles (1990s first, then pre-2005) through a strong core system, never fixes for one particular game.

WispOS is **work in progress, not a release**. Nothing here has been published as a finished image yet.

## What is in this repository
| Path | What it is |
| --- | --- |
| `os/` | The OS image build: kernel/U-Boot/initramfs scripts (`os/boot/`), the root file system overlay and its build (`os/rootfs/`, `os/build-rootfs.sh`, `os/stage_rootfs.py`), the card image (`os/build-image.sh`), the first-time setup screen (`os/setup/`), the boot splash (`os/splash/`), the shared UI tokens (`os/ui/`), the SteamOS-compatibility daemon and update helper (`os/steamos/`), the Steam UI memory ceiling (`os/reclaim/`), the release tool (`os/make-release.py`) and development tooling (`os/dev/`) |
| `steam/` | Pinned inputs the build verifies by hash (lock files) and the small helpers around Steam's native ARM64 client |
| `docs/` | Design documents and the engineering notes with every measurement behind a decision (`docs/WISPOS-NOTES.md`, `docs/WISPOS-TIMINGS.md`) |
| `CHANGELOG.md` | The change ledger |

Start with `docs/WISPOS-NORTH-STAR.md` (goal and standing rules) and `docs/WISPOS-OS-DESIGN.md` (the design and what is done).

Some documents (the change ledger and the engineering notes in particular) mention scripts and layers from earlier stages of the project, or the author's own workflow tooling,
that are not part of this repository; those references are history, not missing files.

## Building and testing
The build needs `aarch64-linux-gnu-gcc`, `mksquashfs`/`unsquashfs`, `fakeroot`, `mke2fs`, `openssl`, `python3` and `curl`, and several gigabytes of free space; large inputs are
downloaded and checked against the hashes in `steam/*.lock.json` and in the scripts. By default everything is written under a work directory on a large disk
(`/mnt/hdd/wispos-staging/wispos-os-20261007` is the author's; every script takes another directory as its first argument). See `os/README.md` for the build order.

The tests need no hardware: `for t in os/test_*.py steam/test_*.py; do python3 -I $t; done`. Several of them build and run small programs against fake `/proc`, cgroup and D-Bus
setups, and `os/qemu-test.sh` boots the built image's userspace in QEMU.

## Updates
Installed systems update from signed releases of this repository: `os/make-release.py` builds the assets and the ed25519-signed manifest; `os/steamos/update-key.pub` is the key
every image trusts. See `docs/WISPOS-UPDATE-DESIGN.md`.

## Licence
MIT, see `LICENSE` (copyright 2026 Daxiongmao87). Third-party material keeps its own terms: the SteamOS interface definitions in `os/steamos/interfaces/` are MIT-licensed by their
authors (the notices are in the files), and the OS image built from this repository contains many other projects under their own licences.
