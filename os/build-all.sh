#!/bin/bash
# Build every artifact of a WispOS release candidate from THIS checkout, in dependency order, into one work directory (docs/WISPOS-RELEASING.md, step 1):
#
#   os/build-all.sh [WORKDIR]            WORKDIR defaults to /mnt/hdd/wispos-staging/wispos-os-20261007 and must be on the HDD
#
#   1. bootloader    os/boot/build-uboot.sh      -> out/u-boot-sunxi-with-spl.bin
#   2. initramfs     os/boot/make-initramfs.sh   -> build/wispos-initramfs.cpio   (its typeface comes from the pinned ROCKNIX SYSTEM: the SYSTEM built in step 4 is checked to carry the same files)
#   3. kernel        os/boot/build-kernel.sh     -> out/kernel/ (Image with that initramfs inside, device trees, modules)
#   4. SYSTEM        os/build-rootfs.sh          -> out/SYSTEM; with the dev inputs also out/variants/SYSTEM-dev
#   5. card images   os/build-image.sh           -> out/wispos-rg34xxsp.img; out/wispos-rg34xxsp-dev.img only with the dev inputs
#   6. checks        os/verify_image.py on every image
# Dev inputs: WISP_SSH_KEY (a PUBLIC key file) and WISP_DEV_ANNOUNCE (HOST:PORT), both or neither. The release steps run without them, so a release artifact can never pick up a key or an
# address; the dev steps run with WISP_DEV=1 and both.
# Writes out/ARTIFACTS (the names, relative to out/) and out/ARTIFACTS.sha256 (their hashes: what os/repro-check.sh compares) and out/build-all.timings (seconds per step).
# Every pinned source is fetched into WORKDIR/dl on first use and hash-checked; the pinned ROCKNIX SYSTEM comes from $ROCKNIX_SYSTEM and is checked against the lock file. The first failing
# step stops everything: nothing downstream is built from a failed upstream. The host is not modified.
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
WORK=${1:-/mnt/hdd/wispos-staging/wispos-os-20261007}
mountpoint -q /mnt/hdd || { echo "build-all: /mnt/hdd is not mounted" >&2; exit 2; }
case $(realpath -m "$WORK") in /mnt/hdd/*) ;; *) echo "build-all: WORKDIR must be on the HDD" >&2; exit 2 ;; esac
DEV=
if [ -n "${WISP_SSH_KEY:-}" ] || [ -n "${WISP_DEV_ANNOUNCE:-}" ]; then
    [ -n "${WISP_SSH_KEY:-}" ] && [ -n "${WISP_DEV_ANNOUNCE:-}" ] || { echo "build-all: a dev image needs both WISP_SSH_KEY and WISP_DEV_ANNOUNCE" >&2; exit 2; }
    [ -f "$WISP_SSH_KEY" ] || { echo "build-all: WISP_SSH_KEY $WISP_SSH_KEY is not a file" >&2; exit 2; }
    DEV=1
fi
mkdir -p "$WORK/tmp" "$WORK/out"; export TMPDIR=$WORK/tmp
TIMINGS=$WORK/out/build-all.timings; : >"$TIMINGS"
CUR=; LAST=$(date +%s)
end_step() { [ -z "$CUR" ] || { local d=$(( $(date +%s) - LAST )); printf '%-40s %5d s\n' "$CUR" "$d" | tee -a "$TIMINGS"; }; CUR=; }
step() { end_step; CUR=$1; LAST=$(date +%s); printf '\n=== %s  (%s)\n' "$1" "$(date -u +%H:%M:%S)"; }
# The release steps see no development knob at all; the dev steps see WISP_DEV=1 and the two dev inputs (the key and the announce target) and nothing else that could change what is built.
release() { env -u WISP_DEV -u WISP_SSH_KEY -u WISP_WIFI_CONF -u WISP_DEV_ANNOUNCE -u WISP_QEMU_CARD -u WISP_ROOTFS_VARIANT -u WISP_IMAGE_NAME -u WISP_KERNEL_IMAGE -u WISP_SYSTEM -u WISP_SYSTEM_MD5 "$@"; }
dev() { env -u WISP_WIFI_CONF -u WISP_QEMU_CARD -u WISP_ROOTFS_VARIANT -u WISP_IMAGE_NAME -u WISP_KERNEL_IMAGE -u WISP_SYSTEM -u WISP_SYSTEM_MD5 WISP_DEV=1 "$@"; }

step "bootloader"
release bash "$HERE/boot/build-uboot.sh" "$WORK"

step "typeface of the boot screens (from the pinned ROCKNIX SYSTEM)"
SQ=${ROCKNIX_SYSTEM:-/mnt/hdd/wispos-staging/rocknix-hardware-reference-20261003/SYSTEM.squashfs}
FONTS=$WORK/build/fonts-pinned; mkdir -p "$WORK/build"; rm -rf -- "$FONTS"
unsquashfs -q -d "$FONTS" "$SQ" usr/share/fonts/liberation/LiberationSans-Bold.ttf usr/share/fonts/liberation/LiberationSans-Regular.ttf >/dev/null
FONTS=$FONTS/usr/share/fonts/liberation

step "initramfs"
release env WISP_UI_FONTS="$FONTS" bash "$HERE/boot/make-initramfs.sh" "$WORK"

step "kernel"
release bash "$HERE/boot/build-kernel.sh" "$WORK" "$WORK/build/wispos-initramfs.cpio"

step "SYSTEM (release)"
release bash "$HERE/build-rootfs.sh" "$WORK"
for f in LiberationSans-Bold.ttf LiberationSans-Regular.ttf; do                       # the boot screens were drawn with these; the SYSTEM must carry exactly them
    cmp -s "$FONTS/$f" "$WORK/rootfs/tree/usr/share/fonts/liberation/$f" || { echo "build-all: $f in the SYSTEM is not the file the boot screens were drawn with" >&2; exit 1; }
done
if [ -n "$DEV" ]; then step "SYSTEM (dev)"; dev bash "$HERE/build-rootfs.sh" "$WORK"; fi

step "card image (release)"
release bash "$HERE/build-image.sh" "$WORK"
if [ -n "$DEV" ]; then step "card image (dev)"; dev bash "$HERE/build-image.sh" "$WORK"; fi

step "checks"
release python3 -I "$HERE/verify_image.py" "$WORK/out/wispos-rg34xxsp.img" "$WORK"
if [ -n "$DEV" ]; then dev python3 -I "$HERE/verify_image.py" "$WORK/out/wispos-rg34xxsp-dev.img" "$WORK"; fi

end_step
cd "$WORK/out"
{ echo u-boot-sunxi-with-spl.bin; echo kernel/Image; echo SYSTEM; [ -z "$DEV" ] || echo variants/SYSTEM-dev; echo wispos-rg34xxsp.img; [ -z "$DEV" ] || echo wispos-rg34xxsp-dev.img; } >ARTIFACTS
sha256sum $(cat ARTIFACTS) | tee ARTIFACTS.sha256
