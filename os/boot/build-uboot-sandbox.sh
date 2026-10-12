#!/bin/bash
# Build U-Boot's SANDBOX (U-Boot compiled as a program for this machine) from the SAME source tree as the handheld's bootloader (os/boot/build-uboot.sh), so the boot-trial script
# (os/boot/uboot/wisp-trial.txt) can be run by the real U-Boot command interpreter against a real FAT image: os/test_uboot_trial.py.
#
#   os/boot/build-uboot-sandbox.sh [WORKDIR]       -> $WORK/build/uboot-sandbox/u-boot   (run it: u-boot -D -c 'host bind 0 disk.img; fatls host 0:0 /')
#
# Needs $WORK/build/uboot (build-uboot.sh ran). The tree is copied (reflinks on the HDD's Btrfs: instant) and built there; the handheld build is untouched. Features that need host
# libraries this host does not have (SDL, EFI capsule tools, the unit-test suite that references EFI, UPL) are off; nothing the script uses is. It is a test tool: it never runs on a card.
set -euo pipefail
WORK=${1:-/mnt/hdd/wispos-staging/wispos-os-20261007}
case "$WORK" in /mnt/hdd/*) ;; *) echo "work directory must be on the HDD" >&2; exit 2 ;; esac
SRC=$WORK/build/uboot; DST=$WORK/build/uboot-sandbox
[ -f "$SRC/configs/sandbox_defconfig" ] || { echo "need $SRC (os/boot/build-uboot.sh)" >&2; exit 1; }
mkdir -p "$WORK/tmp"; export TMPDIR=$WORK/tmp PATH=$WORK/prefix/bin:$PATH
case "$DST" in /mnt/hdd/*/build/uboot-sandbox) rm -rf -- "$DST" ;; *) exit 2 ;; esac
cp -a --reflink=always "$SRC" "$DST"
M() { make -C "$DST" "$@"; }
M mrproper >/dev/null 2>&1
M sandbox_defconfig >/dev/null
"$DST/scripts/config" --file "$DST/.config" -d TOOLS_MKEFICAPSULE -d VIDEO_SANDBOX_SDL -d SOUND -d SANDBOX_SDL -d EFI_LOADER -d EFI_CAPSULE_ON_DISK -d EFI_CAPSULE_FIRMWARE_MANAGEMENT \
    -d EFI_SECURE_BOOT -d CMD_BOOTEFI -d BOOTMETH_EFILOADER -d BOOTMETH_EFI_BOOTMGR -d UNIT_TEST -d UT_BOOTSTD -d CMD_UT -d UT_DM -d UT_ENV -d UT_LIB -d UT_OPTEE -d UT_OVERLAY \
    -d CMD_UPL -d UPL -d UPL_READ -d UPL_WRITE
M olddefconfig >/dev/null
HOSTFLAGS="-DPyInt_AsLong=PyLong_AsLong -DPyString_FromString=PyUnicode_FromString -DPyString_AsString=PyUnicode_AsUTF8"
export SOURCE_DATE_EPOCH=$(date -u -d '2026-10-01 18:31:19' +%s)
M -j"$(nproc)" NO_SDL=1 HOSTCFLAGS="$HOSTFLAGS" >"$WORK/build/uboot-sandbox-build.log" 2>&1 || { tail -5 "$WORK/build/uboot-sandbox-build.log" >&2; exit 1; }
sha256sum "$DST/u-boot"
