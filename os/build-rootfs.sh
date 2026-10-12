#!/bin/bash
# Build the WispOS root filesystem (SYSTEM squashfs) from ROCKNIX's pinned userland and our kernel.
#
#   os/build-rootfs.sh [WORKDIR]       default /mnt/hdd/wispos-staging/wispos-os-20261007
#
# Input:  the pinned ROCKNIX SYSTEM image (hash from steam/rocknix-platform.lock.json) and the
#         output of boot/build-kernel.sh ($WORK/out/kernel).
# Output: $WORK/out/SYSTEM (+ SYSTEM.md5 in the format the initramfs checks) and
#         $WORK/rootfs/tree, the staged tree, for os/check_rootfs.py.
# Ownership is kept with fakeroot (one file is root:dbus; nothing needs real root). No host changes.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${1:-/mnt/hdd/wispos-staging/wispos-os-20261007}
case "$WORK" in /mnt/hdd/*) ;; *) echo "work directory must be on the HDD" >&2; exit 2 ;; esac
LOCK=$HERE/../steam/rocknix-platform.lock.json
SQ=${ROCKNIX_SYSTEM:-/mnt/hdd/wispos-staging/rocknix-hardware-reference-20261003/SYSTEM.squashfs}
SQ_SHA=$(python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["files"]["/SYSTEM"]["sha256"])' "$LOCK" 2>/dev/null \
      || python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1]))["files"]["SYSTEM"]["sha256"])' "$LOCK")
[ "$(sha256sum "$SQ" | cut -c1-64)" = "$SQ_SHA" ] || { echo "pinned SYSTEM image does not match the lock file" >&2; exit 1; }
KERNEL=$WORK/out/kernel
[ -f "$KERNEL/Image" ] && [ -d "$KERNEL/modules/lib/modules" ] || { echo "run os/boot/build-kernel.sh first" >&2; exit 1; }
# WISP_ROOTFS_VARIANT=modules-only builds the stage-isolation SYSTEM: ROCKNIX's userland as shipped,
# with only the kernel modules replaced by ours (see docs/WISPOS-FIRST-BOOT-TEST.md).
VARIANT=${WISP_ROOTFS_VARIANT:-}
# WISP_DEV=1 builds the development SYSTEM (adds rootfs-dev/: the announcer for the developer's machine) as out/variants/SYSTEM-dev,
# in its own work tree, so a dev build can never overwrite the release SYSTEM.
TAG=$VARIANT; [ "${WISP_DEV:-}" = 1 ] && { [ -z "$VARIANT" ] || { echo "WISP_DEV and WISP_ROOTFS_VARIANT cannot be combined" >&2; exit 2; }; TAG=dev; }
T=$WORK/rootfs${TAG:+-$TAG}; OUT=$WORK/out
SYSTEM_OUT=$OUT/${TAG:+variants/}SYSTEM${TAG:+-$TAG}
mkdir -p "$WORK/tmp" "$OUT/variants"; export TMPDIR=$WORK/tmp
case "$T" in /mnt/hdd/*/rootfs|/mnt/hdd/*/rootfs-modules-only|/mnt/hdd/*/rootfs-dev) rm -rf -- "$T" ;; *) echo "refusing to clear $T" >&2; exit 2 ;; esac
mkdir -p "$T"
export SOURCE_DATE_EPOCH=$(date -u -d '2026-10-01 18:31:19' +%s)

fakeroot -s "$T/state" -- unsquashfs -no-xattrs -f -d "$T/tree" "$SQ" >"$T/unsquashfs.log" 2>&1
if [ -z "$VARIANT" ]; then
    # The first-run Wi-Fi screen (setup/wisp-setup.c). Only the HEADERS of SDL2, SDL2_ttf and SDL2_image are needed at build time
    # (hash-pinned releases); it links against the libraries already in the image, so nothing extra ships and nothing is installed here.
    DL=$WORK/dl; mkdir -p "$DL" "$T/sdlinc/SDL2"
    sdl_fetch() { [ -f "$DL/$1" ] || curl -sfL --retry 3 -m 300 -o "$DL/$1" "$2"; [ "$(sha256sum "$DL/$1" | cut -c1-64)" = "$3" ] || { echo "hash mismatch for $1" >&2; exit 1; }; }
    sdl_fetch SDL2-2.32.10.tar.gz https://github.com/libsdl-org/SDL/releases/download/release-2.32.10/SDL2-2.32.10.tar.gz 5f5993c530f084535c65a6879e9b26ad441169b3e25d789d83287040a9ca5165
    sdl_fetch SDL2_ttf-2.20.2.tar.gz https://github.com/libsdl-org/SDL_ttf/releases/download/release-2.20.2/SDL2_ttf-2.20.2.tar.gz 9dc71ed93487521b107a2c4a9ca6bf43fb62f6bddd5c26b055e6b91418a22053
    sdl_fetch SDL2_image-2.8.2.tar.gz https://github.com/libsdl-org/SDL_image/releases/download/release-2.8.2/SDL2_image-2.8.2.tar.gz 8f486bbfbcf8464dd58c9e5d93394ab0255ce68b51c5a966a918244820a76ddc
    tar -xzf "$DL/SDL2-2.32.10.tar.gz" -C "$T/sdlinc/SDL2" --strip-components=2 --wildcards 'SDL2-2.32.10/include/*.h'
    tar -xzf "$DL/SDL2_ttf-2.20.2.tar.gz" -C "$T/sdlinc/SDL2" --strip-components=1 SDL2_ttf-2.20.2/SDL_ttf.h
    tar -xzf "$DL/SDL2_image-2.8.2.tar.gz" -C "$T/sdlinc/SDL2" --strip-components=2 SDL2_image-2.8.2/include/SDL_image.h
    mkdir -p "$OUT/setup" "$OUT/bootscreen"; LIB=$T/tree/usr/lib
    # The boot screen's glyph atlases and badge, from the image's own Liberation Sans files (the same generator and inputs as os/boot/make-initramfs.sh): the first-run hand-over draws the
    # boot screen's last picture itself (setup/bootframe.c, with the drawing code the boot screens share) and the Wayland boot screen below uses the same header.
    python3 -I "$HERE/ui/gen_assets.py" --fonts "$T/tree/usr/share/fonts/liberation" --badge "$HERE/setup/assets/wispos-badge.png" --out "$OUT/bootscreen/wisp-ui-assets.h"
    aarch64-linux-gnu-gcc -std=gnu11 -O2 -Wall -Wextra -Werror -I"$T/sdlinc" -I"$HERE/setup" -I"$HERE/splash" -I"$HERE/ui" -I"$OUT/bootscreen" -o "$OUT/setup/wisp-setup" "$HERE/setup/wisp-setup.c" "$HERE/setup/bootframe.c" \
        "$LIB/libSDL2-2.0.so.0" "$LIB/libSDL2_ttf-2.0.so.0" "$LIB/libSDL2_image-2.0.so.0" -lm -lpthread -Wl,-rpath-link,"$LIB"
    export WISP_SETUP_BIN=$OUT/setup/wisp-setup
    # The daemon behind Steam's SteamOS calls (steamos/wisp-steamos-manager.c): systemd's public headers only (hash-pinned, steamos/fetch-sdbus-headers.sh), linked against the
    # libsystemd already in the image, so nothing extra ships.
    SDBUS_INC=$(bash "$HERE/steamos/fetch-sdbus-headers.sh" "$WORK"); mkdir -p "$OUT/steamos"
    aarch64-linux-gnu-gcc -std=gnu11 -O2 -Wall -Wextra -Werror -I"$SDBUS_INC" -o "$OUT/steamos/wisp-steamos-manager" "$HERE/steamos/wisp-steamos-manager.c" \
        "$LIB/libsystemd.so.0" -Wl,-rpath-link,"$LIB"
    export WISP_STEAMOS_MANAGER_BIN=$OUT/steamos/wisp-steamos-manager
    # The Steam UI's memory ceiling (reclaim/wisp-ui-reclaim.c): plain libc, no other library.
    mkdir -p "$OUT/reclaim"
    aarch64-linux-gnu-gcc -std=gnu11 -O2 -Wall -Wextra -Werror -o "$OUT/reclaim/wisp-ui-reclaim" "$HERE/reclaim/wisp-ui-reclaim.c"
    export WISP_UI_RECLAIM_BIN=$OUT/reclaim/wisp-ui-reclaim
    # The gamepad as pointer and keyboard in desktop mode (pad/wisp-pad.c): libc and libm, no other library.
    mkdir -p "$OUT/pad"
    aarch64-linux-gnu-gcc -std=gnu11 -O2 -Wall -Wextra -Werror -o "$OUT/pad/wisp-pad" "$HERE/pad/wisp-pad.c" "$HERE/pad/pad-io.c" "$HERE/pad/pad-logic.c" -lm
    export WISP_PAD_BIN=$OUT/pad/wisp-pad
    # The desktop-mode shell (dock/shell.c): the Wayland protocol code is generated from definitions fetched at pinned commits (dock/prepare-build.sh, which also copies the headers of
    # FreeType, libpng and libwayland-client from the host's development packages); it links against the libraries already in the image, so nothing extra ships.
    bash "$HERE/dock/prepare-build.sh" "$WORK" >/dev/null
    DG=$WORK/dockgen; DI=$WORK/dockinc; mkdir -p "$OUT/dock"
    aarch64-linux-gnu-gcc -std=gnu11 -O2 -Wall -Wextra -Werror -I"$DG" -I"$DI" -I"$DI/freetype2" -I"$HERE/dock" -o "$OUT/dock/wisp-dock" \
        "$HERE"/dock/shell.c "$HERE"/dock/ui.c "$HERE"/dock/view.c "$HERE"/dock/render.c "$HERE"/dock/model.c "$HERE"/dock/sysinfo.c "$HERE"/dock/swayipc.c "$HERE"/dock/downloads.c \
        "$DG"/wlr-layer-shell-unstable-v1-protocol.c "$DG"/wlr-foreign-toplevel-management-unstable-v1-protocol.c "$DG"/xdg-shell-protocol.c "$DG"/cursor-shape-v1-protocol.c "$DG"/tablet-v2-protocol.c \
        "$LIB/libwayland-client.so.0" "$LIB/libfreetype.so.6" "$LIB/libpng16.so.16" -lm -Wl,-rpath-link,"$LIB"
    export WISP_DOCK_BIN=$OUT/dock/wisp-dock
    # The Wayland phase of the boot screen (splash/wisp-bootscreen.c): the same drawing code as the initramfs splash (splash/scene.h) and the atlases generated above, the Wayland protocol
    # code the dock already needs, and the libwayland-client in the image.
    aarch64-linux-gnu-gcc -std=gnu11 -O2 -Wall -Wextra -Werror -I"$DG" -I"$DI" -I"$HERE/splash" -I"$HERE/ui" -I"$OUT/bootscreen" -o "$OUT/bootscreen/wisp-bootscreen" "$HERE/splash/wisp-bootscreen.c" \
        "$DG"/wlr-layer-shell-unstable-v1-protocol.c "$DG"/wlr-foreign-toplevel-management-unstable-v1-protocol.c "$DG"/xdg-shell-protocol.c \
        "$LIB/libwayland-client.so.0" -lm -Wl,-rpath-link,"$LIB"
    export WISP_BOOTSCREEN_BIN=$OUT/bootscreen/wisp-bootscreen
    # The libraries Steam's client needs that this root lacks (stage_rootfs.STEAM_LIB_PACKAGES): the pinned packages from steam/native-libs.lock.json.
    STEAM_PACKAGES=$(python3 -I -c 'import json,sys; sys.path.insert(0, sys.argv[2]); import stage_rootfs as s; p={x["package"]:x for x in json.load(open(sys.argv[1]))["packages"]}; [print(p[n]["url"], p[n]["sha256"]) for n in s.STEAM_LIB_PACKAGES]' "$HERE/../steam/native-libs.lock.json" "$HERE")
    mkdir -p "$DL/steam-debs"
    while read -r DEB_URL DEB_SHA; do sdl_fetch "steam-debs/${DEB_URL##*/}" "$DEB_URL" "$DEB_SHA"; done <<<"$STEAM_PACKAGES"
    export WISP_STEAM_DEBS=$DL/steam-debs
    # The FEX project's own Windows modules (steam/official-fex.lock.json): fetched from the upstream package pool, hash-pinned, baked into the image unmodified.
    OFFICIAL_FEX=$(python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["url"], d["sha256"])' "$HERE/../steam/official-fex.lock.json")
    read -r OFFICIAL_URL OFFICIAL_SHA <<<"$OFFICIAL_FEX"
    sdl_fetch "steam-debs/${OFFICIAL_URL##*/}" "$OFFICIAL_URL" "$OFFICIAL_SHA"
    export WISP_OFFICIAL_FEX_DEB=$DL/steam-debs/${OFFICIAL_URL##*/}
    # The SIGILL fallback (the H700's Cortex-A53 lacks the LSE atomics Steam uses), built with the exact
    # flags its tests and docs record: plain ARMv8-A, no outline atomics, so the library itself is safe
    # on this CPU. Built from the repository source; the hash is printed for the record.
    mkdir -p "$OUT/preload"
    # -z initfirst: its constructor runs before EVERY other object's. Steam's overlay hooks libc close/open/read/dlopen/dlsym with LSE atomics, and any program whose library
    # constructors call one of them (Python through libGL/libX11/libpython: Valve's fex-compat-tool, Proton's launcher) used to run that hook before this handler existed and died
    # with SIGILL before main (docs/WISPOS-NOTES.md, "Half-Life runs natively under FEX"; regression test steam/test_lse_initfirst.py).
    aarch64-linux-gnu-gcc -std=c11 -O2 -Wall -Wextra -Werror -march=armv8-a -mtune=cortex-a53 -mno-outline-atomics \
        -shared -fPIC -Wl,-z,now -Wl,-z,initfirst "$HERE/../steam/lse-ldadd.c" -o "$OUT/preload/libwispos-ldadd.so"
    # The fallback must not itself use the instructions it emulates: stop if any LSE mnemonic is in it.
    if aarch64-linux-gnu-objdump -d "$OUT/preload/libwispos-ldadd.so" | grep -E '\s(ld(add|clr|set|eor|smax|smin|umax|umin)|swp|cas|casp)[abl]*[hb]?\s'; then
        echo "preload contains LSE instructions" >&2; exit 1
    fi
    (cd "$OUT/preload" && sha256sum libwispos-ldadd.so | tee libwispos-ldadd.sha256)
    export WISP_PRELOAD=$OUT/preload/libwispos-ldadd.so
    python3 -I "$HERE/check_rootfs.py" snapshot "$T/tree" "$T/deps-before.json"      # what resolved before pruning
    fakeroot -i "$T/state" -s "$T/state" -- python3 -I "$HERE/stage_rootfs.py" "$T/tree" "$KERNEL"
    python3 -I "$HERE/check_rootfs.py" verify "$T/tree" "$T/deps-before.json" | tee "$T/check.log"
else
    fakeroot -i "$T/state" -s "$T/state" -- python3 -I "$HERE/stage_rootfs.py" "$T/tree" "$KERNEL" "$VARIANT"
fi
# Same compression as ROCKNIX's SYSTEM (zstd 19, 1 MiB blocks); SOURCE_DATE_EPOCH (exported above) fixes all timestamps so builds match.
fakeroot -i "$T/state" -- mksquashfs "$T/tree" "$SYSTEM_OUT" -noappend -quiet -no-xattrs \
    -comp zstd -Xcompression-level 19 -b 1M >"$T/mksquashfs.log" 2>&1
printf '%s  target/SYSTEM\n' "$(md5sum "$SYSTEM_OUT" | cut -d' ' -f1)" >"$SYSTEM_OUT.md5"
sha256sum "$SYSTEM_OUT" | tee "$SYSTEM_OUT.sha256"
ls -l "$SYSTEM_OUT"
