#!/bin/bash
# Build the WispOS kernel (Image, device trees, modules) from source using ROCKNIX's own H700
# recipe: Linux 7.2, the 41 patches in the order ROCKNIX's scripts/unpack applies them, its
# device trees, its .config, and its built-in firmware (Wi-Fi, Bluetooth, panels, regulatory db).
#
#   os/boot/build-kernel.sh [WORKDIR] [INITRAMFS_CPIO]
#
# WORKDIR defaults to /mnt/hdd/wispos-staging/wispos-os-20261007 (same tree as build-uboot.sh).
# INITRAMFS_CPIO is the uncompressed cpio archive embedded in the kernel; it defaults to the one
# unpacked from ROCKNIX's reference KERNEL (ref-boot/ref.cpio.*), until the initramfs is rebuilt
# from source. Outputs in $WORK/out/kernel/: Image, dtbs/, modules/, config, System.map, SHA256SUMS.
#
# Needs only tools already on the host (aarch64 cross gcc, make, bc, flex, bison, perl, patch,
# git, rsync, openssl and libelf headers, depmod). Nothing is installed. Every tarball is hash-checked
# against the pins in ROCKNIX's recipes, which are read from the pinned ROCKNIX checkout.
set -euo pipefail
WORK=${1:-/mnt/hdd/wispos-staging/wispos-os-20261007}
case "$WORK" in /mnt/hdd/*) ;; *) echo "work directory must be on the HDD" >&2; exit 2 ;; esac
INITRAMFS=${2:-$(ls "$WORK"/ref-boot/ref.cpio.* 2>/dev/null | head -1)}
[ -f "$INITRAMFS" ] || { echo "initramfs cpio not found: '$INITRAMFS'" >&2; exit 2; }
ROCKNIX_COMMIT=c445081a59518f37d9776e5412dd7b14910696f7        # release 20261001 (steam/rocknix-platform.lock.json)
SRC=$WORK/rocknix-src DL=$WORK/dl BUILD=$WORK/build OUT=$WORK/out/kernel
mkdir -p "$DL" "$BUILD" "$OUT" "$WORK/tmp"; export TMPDIR=$WORK/tmp
[ "$(git -C "$SRC" rev-parse HEAD)" = "$ROCKNIX_COMMIT" ] || { echo "ROCKNIX checkout is not the pinned commit (run build-uboot.sh first)" >&2; exit 1; }
pkg() { sed -n "s/^$2=\"\(.*\)\"/\1/p" "$SRC/$1/package.mk" | head -1; }
fetch() {  # name url sha256
    [ -f "$DL/$1" ] || curl -sfL --retry 3 -m 1800 -o "$DL/$1" "$2"
    [ "$(sha256sum "$DL/$1" | cut -c1-64)" = "$3" ] || { echo "hash mismatch for $1" >&2; exit 1; }
}

DEV=H700
LK=$SRC/projects/ROCKNIX/packages/linux
KFW=projects/ROCKNIX/packages/linux-firmware/kernel-firmware
# Versions come from the recipe's H700 branch: PKG_VERSION / PKG_SHA256 inside `H700|SM4450|...)`.
KVER=$(sed -n '/^  H700|/,/;;/s/^ *PKG_VERSION="\(.*\)"/\1/p' "$LK/package.mk" | head -1)
KSHA=$(sed -n '/^  H700|/,/;;/s/^ *PKG_SHA256="\(.*\)"/\1/p' "$LK/package.mk" | head -1)
FW_VER=$(pkg "$KFW" PKG_VERSION); FW_SHA=$(pkg "$KFW" PKG_SHA256)
RD_VER=$(pkg packages/network/wireless-regdb PKG_VERSION); RD_SHA=$(pkg packages/network/wireless-regdb PKG_SHA256)
[ -n "$KVER" ] && [ -n "$KSHA" ] || { echo "could not read the kernel pin from the recipe" >&2; exit 1; }
fetch "linux-$KVER.tar.xz" "https://www.kernel.org/pub/linux/kernel/v${KVER%%.*}.x/linux-$KVER.tar.xz" "$KSHA"
fetch "linux-firmware-$FW_VER.tar.gz" "https://cdn.kernel.org/pub/linux/kernel/firmware/linux-firmware-$FW_VER.tar.gz" "$FW_SHA"
JP=projects/ROCKNIX/packages/linux-drivers/rocknix-joypad
JP_VER=$(pkg "$JP" PKG_VERSION); JP_SHA=$(pkg "$JP" PKG_SHA256)
fetch "rocknix-joypad-$JP_VER.tar.gz" "https://github.com/ROCKNIX/rocknix-joypad/archive/$JP_VER.tar.gz" "$JP_SHA"
fetch "wireless-regdb-$RD_VER.tar.xz" "https://www.kernel.org/pub/software/network/wireless-regdb/wireless-regdb-$RD_VER.tar.xz" "$RD_SHA"

K=$BUILD/linux
rm -rf "$K" "$BUILD/fw" "$BUILD/regdb"; mkdir -p "$K" "$BUILD/fw" "$BUILD/regdb"
tar -xJf "$DL/linux-$KVER.tar.xz" -C "$K" --strip-components=1
tar -xzf "$DL/linux-firmware-$FW_VER.tar.gz" -C "$BUILD/fw" --strip-components=1
tar -xJf "$DL/wireless-regdb-$RD_VER.tar.xz" -C "$BUILD/regdb" --strip-components=1

# 1. Patches, in the order ROCKNIX's scripts/unpack collects them: the package's patch dirs
#    (`mainline`, then the one named after the kernel version), then the device's own patches.
#    A patch that does not apply stops the build.
PLOG=$BUILD/linux-patches.log; : >"$PLOG"; n=0
for p in "$LK"/patches/mainline/*.patch "$LK/patches/$KVER"/*.patch "$SRC/projects/ROCKNIX/devices/$DEV/patches/linux"/*.patch; do
    [ -f "$p" ] || continue
    echo "== $p" >>"$PLOG"
    if grep -qE '^GIT binary patch$|^rename from|^rename to' "$p"; then
        git apply --directory="$K" -p1 --whitespace=nowarn --unsafe-paths <"$p" >>"$PLOG" 2>&1
    else
        patch -d "$K" -p1 < <(sed -e "s#@TARGET_CPU@#cortex-a53#g" -e "s#@DEVICE@#$DEV#g" "$p") >>"$PLOG" 2>&1
    fi
    n=$((n+1))
done
echo "applied $n patches"
rsync -a "$SRC/projects/ROCKNIX/devices/$DEV/linux/dts/" "$K/arch/arm64/boot/dts/"

# 2. Firmware built into the kernel (CONFIG_EXTRA_FIRMWARE), as the recipe's H700 branch lists it.
#    ROCKNIX's extra-firmware overlay (panel files) sits on top of the linux-firmware tree.
EF=$K/external-firmware; mkdir -p "$EF/rtl_bt" "$EF/rtw88" "$EF/panels"
# rtl8821cs_config.bin exists only as a link in linux-firmware (copy-firmware.sh makes it from WHENCE).
grep -q '^Link: rtl_bt/rtl8821cs_config.bin -> rtl8761b_config.bin$' "$BUILD/fw/WHENCE" \
    || { echo "WHENCE no longer links rtl8821cs_config.bin to rtl8761b_config.bin" >&2; exit 1; }
cp -L "$SRC/$KFW/extra-firmware/rtl_bt/rtl8761b_config.bin" "$EF/rtl_bt/rtl8821cs_config.bin"   # overlay file wins, as in ROCKNIX
cp -L "$BUILD/fw/rtl_bt/rtl8821cs_fw.bin" "$EF/rtl_bt/"
cp -L "$BUILD/fw/rtw88/rtw8821c_fw.bin" "$EF/rtw88/"
cp -L "$SRC/$KFW/extra-firmware/panels/"*.panel "$EF/panels/"
cp -L "$BUILD/regdb/regulatory.db" "$BUILD/regdb/regulatory.db.p7s" "$EF/"
FW_LIST="$(cd "$EF" && find panels rtl_bt rtw88 -type f | sort | xargs) regulatory.db regulatory.db.p7s"

# 3. Configuration: ROCKNIX's H700 .config plus the three settings its build rewrites
#    (hostname, built-in firmware list, initramfs source). The reference kernel's embedded config
#    differs from the source config in exactly these.
cp "$SRC/projects/ROCKNIX/devices/$DEV/linux/linux.aarch64.conf" "$K/.config"
# A single `*.cpio.gz` is embedded as given (kbuild "avoid double compression"); gzip -n -9 is what
# kbuild itself runs, and it reproduces ROCKNIX's embedded stream byte for byte.
gzip -n -9 -c "$INITRAMFS" >"$BUILD/initramfs.cpio.gz"
"$K/scripts/config" --file "$K/.config" --set-str CONFIG_INITRAMFS_SOURCE "$BUILD/initramfs.cpio.gz" \
    --set-str CONFIG_DEFAULT_HOSTNAME "WispOS" \
    --set-str CONFIG_EXTRA_FIRMWARE "$FW_LIST" --set-str CONFIG_EXTRA_FIRMWARE_DIR "external-firmware"

# 4. Build. Fixed build identity so two builds of the same inputs match.
export KBUILD_BUILD_USER=wispos KBUILD_BUILD_HOST=wispos KBUILD_BUILD_VERSION=1
export KBUILD_BUILD_TIMESTAMP='Thu Oct  1 18:39:51 UTC 2026'
export SOURCE_DATE_EPOCH=$(date -u -d "$KBUILD_BUILD_TIMESTAMP" +%s)
DTBS=$(python3 -I - "$SRC/projects/ROCKNIX/config.xml" <<'PY'
import sys, xml.etree.ElementTree as ET
r = ET.parse(sys.argv[1]).getroot().find('H700')
print(' '.join(sorted({f'{r.get("dtb_prefix")}/{e.text.strip()}.dtb' for e in r.iter('file')})))
PY
)
KM() { make -C "$K" ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- HOSTCC=gcc HOSTCXX=g++ DEPMOD=depmod "$@"; }
KM olddefconfig >"$BUILD/linux-oldconfig.log" 2>&1     # ROCKNIX pipes `yes ""` into oldconfig: same defaults
cp "$K/.config" "$OUT/config"
# shellcheck disable=SC2086
DTC_FLAGS=-@ KM -j"$(nproc)" Image $DTBS modules >"$BUILD/linux-build.log" 2>&1

# 5. Install: image, device trees, modules (the layout ROCKNIX's SYSTEM carries under kernel-overlays).
rm -rf "$OUT/dtbs" "$OUT/modules"; mkdir -p "$OUT/dtbs" "$OUT/modules"
install -m 644 "$K/arch/arm64/boot/Image" "$OUT/Image"
install -m 644 "$K/System.map" "$OUT/System.map"
for d in $DTBS; do install -m 644 "$K/arch/arm64/boot/dts/$d" "$OUT/dtbs/"; done
# Device-tree overlays (ROCKNIX's device-tree-overlays package): the DDR3 board needs a 1.2 V DRAM rail.
rm -rf "$OUT/overlays"; mkdir -p "$OUT/overlays"
for dts in "$SRC/projects/ROCKNIX/packages/kernel-drivers/device-tree-overlays/sources/$DEV"/*.dts; do
    "$K/scripts/dtc/dtc" -@ -I dts -O dtb -o "$OUT/overlays/$(basename "${dts%.dts}").dtbo" "$dts"
done
KM INSTALL_MOD_PATH="$OUT/modules" modules_install >"$BUILD/linux-modinstall.log" 2>&1
rm -f "$OUT"/modules/lib/modules/*/build "$OUT"/modules/lib/modules/*/source
# The built-in firmware files, as the rootfs also carries them (ROCKNIX ships the same set under
# kernel-overlays/base/lib/firmware, for tools that read them from disk).
rm -rf "$OUT/firmware"; cp -a "$EF" "$OUT/firmware"
# The one out-of-tree driver the H700 image carries (ROCKNIX's rocknix-joypad), built against this tree.
rm -rf "$BUILD/joypad"; mkdir -p "$BUILD/joypad"; tar -xzf "$DL/rocknix-joypad-$JP_VER.tar.gz" -C "$BUILD/joypad" --strip-components=1
# Its Makefile picks the module by $(DEVICE) (H700 -> rocknix-singleadc-joypad), which ROCKNIX's build sets.
KM DEVICE=$DEV M="$BUILD/joypad" >"$BUILD/joypad-build.log" 2>&1
KREL=$(ls "$OUT/modules/lib/modules")
mkdir -p "$OUT/modules/lib/modules/$KREL/rocknix-joypad"; cp "$BUILD"/joypad/*.ko "$OUT/modules/lib/modules/$KREL/rocknix-joypad/"
depmod -b "$OUT/modules" "$KREL"
( cd "$OUT" && find Image config System.map dtbs overlays -type f | sort | xargs sha256sum >SHA256SUMS && cat SHA256SUMS | head -4 )
echo "kernel release: $(ls "$OUT/modules/lib/modules")"
