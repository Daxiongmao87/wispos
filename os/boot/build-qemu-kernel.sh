#!/bin/bash
# Build a TEST-ONLY kernel for booting the WispOS image in QEMU (virt machine) on this host.
#
#   os/boot/build-qemu-kernel.sh [WORKDIR]       -> $WORK/out/qemu/Image
#
# The handheld kernel (build-kernel.sh) has no virtio transport, so QEMU's virt machine cannot see
# a disk or a NIC with it. This copies that finished build tree (reflinks on the HDD's Btrfs, so
# it is fast and costs no space) and enables virtio PCI/MMIO/net on top of the identical source and
# configuration, then builds only the Image. It exists to exercise the initramfs, the root
# filesystem and systemd, not the H700 hardware; it is never written to a card.
set -euo pipefail
WORK=${1:-/mnt/hdd/wispos-staging/wispos-os-20261007}
case "$WORK" in /mnt/hdd/*) ;; *) echo "work directory must be on the HDD" >&2; exit 2 ;; esac
K=$WORK/build/linux-qemu; OUT=$WORK/out/qemu
[ -f "$WORK/build/linux/.config" ] || { echo "run os/boot/build-kernel.sh first" >&2; exit 1; }
case "$K" in /mnt/hdd/*/build/linux-qemu) rm -rf -- "$K" ;; *) exit 2 ;; esac
cp -a --reflink=always "$WORK/build/linux" "$K"
"$K/scripts/config" --file "$K/.config" -e VIRTIO_MENU -e VIRTIO_PCI -e VIRTIO_MMIO -e VIRTIO_NET
export KBUILD_BUILD_USER=wispos KBUILD_BUILD_HOST=wispos-qemu-test KBUILD_BUILD_VERSION=1
KM() { make -C "$K" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- HOSTCC=gcc "$@"; }
KM olddefconfig >/dev/null
KM -j"$(nproc)" Image >"$WORK/build/linux-qemu-build.log" 2>&1
mkdir -p "$OUT"; install -m 644 "$K/arch/arm64/boot/Image" "$OUT/Image"
sha256sum "$OUT/Image"
