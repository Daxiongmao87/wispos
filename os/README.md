# WispOS image sources

See `docs/WISPOS-OS-DESIGN.md`. WispOS is its own OS image, not a layer over ROCKNIX: the boot
chain and kernel are built from source with ROCKNIX's open recipes, and the userland starts from
ROCKNIX's pinned binaries pruned to the one purpose. For the first hardware test see
`docs/WISPOS-FIRST-BOOT-TEST.md`.

## Build, in order (everything lands under `/mnt/hdd/wispos-staging/wispos-os-20261007/`)

| Step | Script | Produces | Verified by |
| --- | --- | --- | --- |
| 1 | `boot/build-uboot.sh` | `out/u-boot-sunxi-with-spl.bin` (U-Boot v2026.01 + BL31) | reproducible; structural match with the booting bootloader |
| 2 | `boot/build-kernel.sh` | `out/kernel/` (Image with embedded initramfs, dtbs, DDR3 overlay, 52 modules, firmware) | 19/19 dtbs and the initramfs byte-identical to ROCKNIX's build; same module list |
| 3 | `build-rootfs.sh` | `out/SYSTEM` (squashfs) | `check_rootfs.py` (ELF, unit and link closure); reproducible |
| 4 | `build-image.sh` | `out/wispos-rg34xxsp.img` | `verify_image.py` (25 checks); reproducible |
| 5 | `boot/build-qemu-kernel.sh`, `qemu-test.sh` | boots the image headless in QEMU | 14 checks on the userspace boot flow |

Stage-isolation variants (see the test document): `build-image.sh` takes `WISP_KERNEL_IMAGE`,
`WISP_SYSTEM`, `WISP_SYSTEM_MD5`, `WISP_BOOT_MIB` and `WISP_IMAGE_NAME`; `build-rootfs.sh` takes
`WISP_ROOTFS_VARIANT=modules-only`. Credentials (`WISP_WIFI_CONF`, `WISP_SSH_KEY`) are accepted only with `WISP_DEV=1`; the script
refuses them otherwise. Dev images are named `...-dev.img`, marked `wispos/DEV-BUILD.txt`, and never published.

## What is here

| Path | Purpose |
| --- | --- |
| `rootfs-prune.txt` | what is removed from ROCKNIX's userland, and why |
| `stage_rootfs.py` | prunes, installs our modules and firmware, overlays `rootfs/`, adds the `wisp` account, makes `wispos.target` the default |
| `rootfs/` | files baked into the image: `wisp-steam.service` (the one session), `wispos.target`, `sshd.service`, `wisp-firstboot` (Wi-Fi and SSH keys from the FAT partition), `wisp-bootlog` (boot report), tmpfiles |
| `accounts.py` | the `wisp` account (`wisp`/`wisp`, the operator's choice); root has no password (key only) |
| `fatwrite.py` | fills a FAT32 volume without root or mtools |
| `check_rootfs.py`, `verify_image.py` | static checks on the staged tree and on the finished image |
| `test_rootfs.py`, `test_fatwrite.py` | offline tests (`TMPDIR` must be on the HDD for the FAT tests) |

Run the tests with `TMPDIR=/mnt/hdd/wispos-staging/wispos-os-20261007/tmp python3 -I os/test_rootfs.py`
(and `test_fatwrite.py`).

## Status

Everything above builds, is reproducible and boots to SSH in emulation (a generic arm64
machine with a test-only virtio kernel). It has not run on the handheld. The Steam runtime is
not in the image.
