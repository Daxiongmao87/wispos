#!/usr/bin/env bash
# Build the WispOS initramfs: ROCKNIX's reference initramfs with the boot splash swapped in (docs/WISPOS-SPLASH-DESIGN.md) and the boot-slot step added (docs/WISPOS-UPDATE-DESIGN.md).
#   os/boot/make-initramfs.sh [WORKDIR]      -> WORKDIR/build/wispos-initramfs.cpio, to be passed to os/boot/build-kernel.sh as its second argument
# Steps: render the UI assets (glyph atlases and the boot badge) from the image's own Liberation Sans files and the setup screen's badge; build wisp-splash static for aarch64;
# patch the reference archive (os/boot/patch_initramfs.py: the splash entry replaced, the splash binary and os/boot/wisp-boot.sh added, two lines added to init).
# Everything under WORKDIR (the HDD staging tree); nothing on the host root.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
WORK=${1:-/mnt/hdd/wispos-staging/wispos-os-20261007}
mountpoint -q /mnt/hdd || { echo "make-initramfs: /mnt/hdd is not mounted" >&2; exit 2; }
case $(realpath -m "$WORK") in /mnt/hdd/*) ;; *) echo "make-initramfs: WORKDIR must be on the HDD" >&2; exit 2;; esac
REF=$(ls "$WORK"/ref-boot/ref.cpio.* 2>/dev/null | head -1); [ -f "$REF" ] || { echo "make-initramfs: no reference initramfs in $WORK/ref-boot" >&2; exit 2; }
B=$WORK/build/splash; mkdir -p "$B"
# The typeface is the one the setup screen loads on the device: take it from the built SYSTEM image (WISP_UI_FONTS=DIR overrides, for a test).
FONTS=${WISP_UI_FONTS:-}
if [ -z "$FONTS" ]; then
    [ -f "$WORK/out/SYSTEM" ] || { echo "make-initramfs: no $WORK/out/SYSTEM to take the fonts from (or set WISP_UI_FONTS)" >&2; exit 2; }
    rm -rf "$B/sysfonts"
    unsquashfs -q -d "$B/sysfonts" "$WORK/out/SYSTEM" usr/share/fonts/liberation/LiberationSans-Bold.ttf usr/share/fonts/liberation/LiberationSans-Regular.ttf >/dev/null
    FONTS=$B/sysfonts/usr/share/fonts/liberation
fi
[ -f "$FONTS/LiberationSans-Bold.ttf" ] || { echo "make-initramfs: no Liberation Sans in $FONTS" >&2; exit 2; }
python3 -I "$ROOT/os/ui/gen_assets.py" --fonts "$FONTS" --badge "$ROOT/os/setup/assets/wispos-badge.png" --out "$B/wisp-ui-assets.h"
aarch64-linux-gnu-gcc -O2 -Wall -Wextra -Werror -static -I "$ROOT/os/ui" -I "$B" -o "$B/wisp-splash" "$ROOT/os/splash/wisp-splash.c" -lm
aarch64-linux-gnu-strip "$B/wisp-splash"
python3 -I "$ROOT/os/boot/patch_initramfs.py" "$REF" "$WORK/build/wispos-initramfs.cpio" "$ROOT/os/splash/rocknix-splash.sh" "$B/wisp-splash" "$ROOT/os/boot/wisp-boot.sh"
sha256sum "$WORK/build/wispos-initramfs.cpio" "$B/wisp-splash" | sed "s#$WORK/##"
