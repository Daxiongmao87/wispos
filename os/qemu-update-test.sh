#!/bin/bash
# The two-slot update scheme end to end in QEMU (os/boot/qemu_update_test.py explains what is real and what is a model).
#
#   os/qemu-update-test.sh [WORKDIR] [scenario ...]    scenarios: update rollback nosystemd cut-staged cut-pending cut-switched badkernel (default: all, in parallel)
#
# Needs: the test kernel with the PATCHED initramfs ($WORK/out/qemu/Image: os/boot/make-initramfs.sh, os/boot/build-kernel.sh, then os/boot/build-qemu-kernel.sh), the built release
# SYSTEM ($WORK/out/SYSTEM), and U-Boot for QEMU ($WORK/build/uboot-qemu/u-boot.bin: os/boot/build-uboot-qemu.sh; WISP_QEMU_UBOOT=none falls back to the stand-in, which cannot
# run `badkernel`). Builds its own copy of the card (a dev QEMU card: SSH on, the QEMU kernel as its KERNEL, a throwaway key made here: no real credential is involved) and never
# modifies it. Logs and the verdict are in $WORK/qemu-update/run/<scenario>/. Exit status is non-zero if any check fails.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${1:-/mnt/hdd/wispos-staging/wispos-os-20261007}; [ $# -gt 0 ] && shift
case "$WORK" in /mnt/hdd/*) ;; *) echo "work directory must be on the HDD" >&2; exit 2 ;; esac
KERNEL=${WISP_QEMU_UPDATE_KERNEL:-$WORK/out/qemu/Image}
[ -f "$KERNEL" ] || { echo "need $KERNEL (os/boot/build-qemu-kernel.sh)" >&2; exit 1; }
export WISP_QEMU_UBOOT=${WISP_QEMU_UBOOT:-$WORK/build/uboot-qemu/u-boot.bin}
if [ "$WISP_QEMU_UBOOT" != none ] && [ ! -f "$WISP_QEMU_UBOOT" ]; then echo "need $WISP_QEMU_UBOOT (os/boot/build-uboot-qemu.sh), or WISP_QEMU_UBOOT=none for the stand-in" >&2; exit 1; fi
D=$WORK/qemu-update; mkdir -p "$D" "$WORK/tmp"; export TMPDIR=$WORK/tmp
KEY=$D/test-key; if [ ! -f "$KEY" ]; then ssh-keygen -q -t ed25519 -N '' -C wispos-qemu-update-test -f "$KEY"; fi
rm -rf "$D/run"
WISP_DEV=1 WISP_QEMU_CARD=1 WISP_KERNEL_IMAGE=$KERNEL WISP_SYSTEM=$WORK/out/SYSTEM WISP_SYSTEM_MD5=$WORK/out/SYSTEM.md5 WISP_SSH_KEY=$KEY.pub WISP_IMAGE_NAME=qemu/update-card.img \
    bash "$HERE/build-image.sh" "$WORK" >"$D/card-build.log" 2>&1 || { echo "could not build the test card"; tail -5 "$D/card-build.log"; exit 1; }
python3 -I "$HERE/boot/qemu_update_test.py" "$KERNEL" "$WORK/out/qemu/update-card.img" "$D/run" "$KEY" "$@"
