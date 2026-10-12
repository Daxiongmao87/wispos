#!/bin/bash
# Build a TEST-ONLY U-Boot for QEMU's arm64 "virt" machine from the SAME patched source tree as the handheld's bootloader (os/boot/build-uboot.sh), so the update tests
# (os/qemu-update-test.sh) boot a card through the real U-Boot extlinux/label code instead of a model of it.
#
#   os/boot/build-uboot-qemu.sh [WORKDIR]       -> $WORK/build/uboot-qemu/u-boot.bin  (use with: qemu-system-aarch64 -M virt -bios u-boot.bin)
#
# Needs $WORK/build/uboot (build-uboot.sh ran). The tree is copied (reflinks on the HDD's Btrfs: instant, no space) and cleaned there; the handheld build is untouched.
# The label logic under test (boot/pxe_utils.c: DEFAULT, handle_pxe_menu, boot_unattempted_labels) is the code the H700 build runs too: it boots through the `sysboot` command,
# this one through bootmeth_extlinux, and both end in pxe_process(). Host tool mkeficapsule needs gnutls headers this host does not have and nothing here uses capsules: off.
# It runs in QEMU only and is never written to a card.
set -euo pipefail
WORK=${1:-/mnt/hdd/wispos-staging/wispos-os-20261007}
case "$WORK" in /mnt/hdd/*) ;; *) echo "work directory must be on the HDD" >&2; exit 2 ;; esac
SRC=$WORK/build/uboot; DST=$WORK/build/uboot-qemu
[ -f "$SRC/configs/qemu_arm64_defconfig" ] || { echo "need $SRC (os/boot/build-uboot.sh)" >&2; exit 1; }
mkdir -p "$WORK/tmp"; export TMPDIR=$WORK/tmp PATH=$WORK/prefix/bin:$PATH
case "$DST" in /mnt/hdd/*/build/uboot-qemu) rm -rf -- "$DST" ;; *) exit 2 ;; esac
cp -a --reflink=always "$SRC" "$DST"
M() { make -C "$DST" CROSS_COMPILE=aarch64-linux-gnu- "$@"; }
M mrproper >/dev/null 2>&1
M qemu_arm64_defconfig >/dev/null
"$DST/scripts/config" --file "$DST/.config" -d TOOLS_MKEFICAPSULE
M olddefconfig >/dev/null
export SOURCE_DATE_EPOCH=$(date -u -d '2026-10-01 18:31:19' +%s)
M -j"$(nproc)" >"$WORK/build/uboot-qemu-build.log" 2>&1 || { tail -5 "$WORK/build/uboot-qemu-build.log" >&2; exit 1; }
sha256sum "$DST/u-boot.bin"
