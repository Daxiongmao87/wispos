#!/bin/bash
# Build the WispOS bootloader (U-Boot + Arm Trusted Firmware BL31) from source using ROCKNIX's
# own H700 DDR3 recipe, pinned by hash. Output: $WORK/out/u-boot-sunxi-with-spl.bin, which goes
# at byte offset 8192 of the card (the Allwinner boot ROM loads it from there).
#
#   os/boot/build-uboot.sh [WORKDIR]       default /mnt/hdd/wispos-staging/wispos-os-20261007
#
# Needs only tools already on the host (aarch64 cross gcc, dtc, bison, flex, bc, make, python3
# with headers); swig is compiled into $WORK/prefix and pyelftools is used from its source tree.
# Nothing is installed system-wide. Every source tarball is verified against ROCKNIX's recipe.
set -euo pipefail
WORK=${1:-/mnt/hdd/wispos-staging/wispos-os-20261007}
case "$WORK" in /mnt/hdd/*) ;; *) echo "work directory must be on the HDD" >&2; exit 2 ;; esac
ROCKNIX_COMMIT=c445081a59518f37d9776e5412dd7b14910696f7        # release 20261001 (steam/rocknix-platform.lock.json)
SRC=$WORK/rocknix-src DL=$WORK/dl BUILD=$WORK/build OUT=$WORK/out
mkdir -p "$DL" "$BUILD" "$OUT" "$WORK/tmp"; export TMPDIR=$WORK/tmp
# Recipes are read from the ROCKNIX checkout so the pins live in one place (theirs).
if [ ! -d "$SRC/.git" ]; then
    git init -q "$SRC"; git -C "$SRC" remote add origin https://github.com/ROCKNIX/distribution.git
    git -C "$SRC" fetch -q --depth 1 origin "$ROCKNIX_COMMIT"; git -C "$SRC" checkout -q FETCH_HEAD
fi
[ "$(git -C "$SRC" rev-parse HEAD)" = "$ROCKNIX_COMMIT" ] || { echo "ROCKNIX checkout is not the pinned commit" >&2; exit 1; }
pkg() { sed -n "s/^$2=\"\(.*\)\"/\1/p" "$SRC/$1/package.mk" | head -1; }
fetch() {  # name url sha256
    [ -f "$DL/$1" ] || curl -sfL --retry 3 -m 900 -o "$DL/$1" "$2"
    [ "$(sha256sum "$DL/$1" | cut -c1-64)" = "$3" ] || { echo "hash mismatch for $1" >&2; exit 1; }
}
UB=projects/ROCKNIX/devices/H700/packages/u-boot-DDR3
UB_VER=$(pkg "$UB" PKG_VERSION); UB_SHA=$(pkg "$UB" PKG_SHA256)
ATF_VER=$(pkg packages/tools/atf PKG_VERSION); ATF_SHA=$(pkg packages/tools/atf PKG_SHA256)
SW_VER=$(pkg packages/devel/swig PKG_VERSION); SW_SHA=$(pkg packages/devel/swig PKG_SHA256)
PE_VER=$(pkg packages/python/devel/pyelftools PKG_VERSION); PE_SHA=$(pkg packages/python/devel/pyelftools PKG_SHA256)
fetch "u-boot-$UB_VER.tar.gz" "https://github.com/u-boot/u-boot/archive/$UB_VER.tar.gz" "$UB_SHA"
fetch "atf-$ATF_VER.tar.gz" "https://github.com/TrustedFirmware-A/trusted-firmware-a/archive/v$ATF_VER.tar.gz" "$ATF_SHA"
fetch "swig-$SW_VER.tar.gz" "https://downloads.sourceforge.net/project/swig/swig/swig-$SW_VER/swig-$SW_VER.tar.gz" "$SW_SHA"
fetch "pyelftools-$PE_VER.tar.gz" "https://github.com/eliben/pyelftools/archive/refs/tags/v$PE_VER.tar.gz" "$PE_SHA"
unpack() { rm -rf "$BUILD/$1"; mkdir -p "$BUILD/$1"; tar -xzf "$DL/$2" -C "$BUILD/$1" --strip-components=1; }

# 1. BL31 (the SPL embeds it). Platform from the device options: ATF_PLATFORM="sun50i_h616".
unpack atf "atf-$ATF_VER.tar.gz"
(cd "$BUILD/atf" && env -u CFLAGS -u LDFLAGS CROSS_COMPILE=aarch64-linux-gnu- CFLAGS="-ffat-lto-objects" \
    make -j"$(nproc)" PLAT=sun50i_h616 BUILD_MESSAGE_TIMESTAMP='"18:31:19, Oct  1 2026"' bl31 >"$BUILD/atf-build.log" 2>&1)
# 2. Host helpers for U-Boot's binman: swig in a private prefix, pyelftools from source.
if [ ! -x "$WORK/prefix/bin/swig" ]; then
    unpack swig "swig-$SW_VER.tar.gz"
    (cd "$BUILD/swig" && ./configure --prefix="$WORK/prefix" --without-pcre >"$BUILD/swig-build.log" 2>&1 \
        && make -j"$(nproc)" >>"$BUILD/swig-build.log" 2>&1 && make install >>"$BUILD/swig-build.log" 2>&1)
fi
unpack pyelftools "pyelftools-$PE_VER.tar.gz"
# 3. U-Boot with ROCKNIX's patches and defconfig.
unpack uboot "u-boot-$UB_VER.tar.gz"
for patch in "$SRC/projects/ROCKNIX/devices/H700/packages/u-boot-DDR3/patches/"*.patch; do
    patch -d "$BUILD/uboot" -p1 --no-backup-if-mismatch -s <"$patch"
done
cp "$SRC/projects/ROCKNIX/devices/H700/packages/u-boot-DDR3/sources/configs/"*_defconfig "$BUILD/uboot/configs/"
# Fixed timestamp so two builds of the same inputs are identical (ROCKNIX's release stamp).
export SOURCE_DATE_EPOCH=$(date -u -d '2026-10-01 18:31:19' +%s)
# SWIG 4.5 no longer maps Python 2 names that U-Boot's libfdt.i still uses; this only affects the
# host-side helper (pylibfdt), never the bootloader binary.
HOSTFLAGS="-DPyInt_AsLong=PyLong_AsLong -DPyString_FromString=PyUnicode_FromString -DPyString_AsString=PyUnicode_AsUTF8"
export PATH="$WORK/prefix/bin:$PATH" PYTHONPATH="$BUILD/pyelftools" CROSS_COMPILE=aarch64-linux-gnu- ARCH=arm
cd "$BUILD/uboot"
{ make mrproper; make HOSTCC=gcc HOSTCFLAGS="$HOSTFLAGS" anbernic_rg35xx_h700_lpddr3_defconfig
  make -j"$(nproc)" HOSTCC=gcc HOSTCFLAGS="$HOSTFLAGS" BL31="$BUILD/atf/build/sun50i_h616/release/bl31.bin" \
       HOSTSTRIP=true CONFIG_MKIMAGE_DTC_PATH=scripts/dtc/dtc; } >"$BUILD/uboot-build.log" 2>&1
install -m 644 u-boot-sunxi-with-spl.bin "$OUT/u-boot-sunxi-with-spl.bin"
sha256sum "$OUT/u-boot-sunxi-with-spl.bin" | tee "$OUT/u-boot-sunxi-with-spl.bin.sha256"
