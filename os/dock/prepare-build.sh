#!/bin/bash
# Prepares what the desktop shell (os/dock/shell.c) needs to be compiled and nothing else: the Wayland protocol definitions it speaks, fetched from upstream at pinned commits and checked against
# pinned SHA-256 sums, turned into C by the host's wayland-scanner; and the headers of FreeType, libpng and libwayland-client, copied from the host's development packages (headers only: the
# program links against the libraries already in the image, so nothing extra ships and nothing is installed).
#   prepare-build.sh WORK_DIR      -> WORK_DIR/dockgen (protocol sources) and WORK_DIR/dockinc (headers)
# Host prerequisites (build only): wayland-scanner and the libwayland, FreeType and libpng development headers.
set -euo pipefail
WORK=${1:?usage: prepare-build.sh WORK_DIR}
DL=$WORK/dl/protocols; GEN=$WORK/dockgen; INC=$WORK/dockinc
mkdir -p "$DL" "$GEN" "$INC"

WLR=https://gitlab.freedesktop.org/wlroots/wlr-protocols/-/raw/bf4fc79abc359eea5a0edec0ac6d4a2b2955f82a/unstable        # wlr-protocols, 2026-03-09
WLP=https://gitlab.freedesktop.org/wayland/wayland-protocols/-/raw/ee78491a237eaff9389a0ccf8680521d074407d3/stable        # wayland-protocols 1.49
ROOTS=https://gitlab.freedesktop.org/wlroots/wlroots/-/raw/88a869855742281c98c22cab9641b317b8d065ef/protocol              # wlroots 0.19.3 (the tests' virtual keyboard)
fetch() {   # name url sha256
    if [ ! -f "$DL/$1" ]; then curl -sfL --retry 3 -m 120 -o "$DL/$1.part" "$2" && mv "$DL/$1.part" "$DL/$1"; fi
    [ "$(sha256sum "$DL/$1" | cut -c1-64)" = "$3" ] || { echo "hash mismatch for $1" >&2; rm -f "$DL/$1"; exit 1; }
}
fetch wlr-layer-shell-unstable-v1.xml "$WLR/wlr-layer-shell-unstable-v1.xml" 87e0b9c837aecd6977f76f3c47d73088b7159871f5d979dc1840f6cadb5e2ed8
fetch wlr-foreign-toplevel-management-unstable-v1.xml "$WLR/wlr-foreign-toplevel-management-unstable-v1.xml" 4ecc4588858e29fe680a33521e1f22bcf22071d66d1553fe96b4ddec03d591d2
fetch wlr-virtual-pointer-unstable-v1.xml "$WLR/wlr-virtual-pointer-unstable-v1.xml" 3ff6d540be0bc5228195bf072bde42117ea17945a5c2061add5d3cf97d6bb524
fetch xdg-shell.xml "$WLP/xdg-shell/xdg-shell.xml" 7ba7f9c8473deee674cb1f154a18abd0bb0cc072604fc055b0c15e459fc4c7df
fetch cursor-shape-v1.xml "${WLP%/stable}/staging/cursor-shape/cursor-shape-v1.xml" bb57d91e53a79dadab7c612dab87c233393cee73673feefa7442cfbfdd9aed2f
fetch tablet-v2.xml "${WLP%/stable}/unstable/tablet/tablet-unstable-v2.xml" db291b574adb2d42d27f3d01a77723bb3350a7a32e6d33de16513521b42294a9       # cursor-shape refers to its tool interface
fetch virtual-keyboard-unstable-v1.xml "$ROOTS/virtual-keyboard-unstable-v1.xml" 7ad7870003ecd592cae47dc19d277a609b7f18fd7b7be012623cf3225a7294f5

SCANNER=$(command -v wayland-scanner) || { echo "wayland-scanner is needed to build the shell" >&2; exit 1; }
for x in wlr-layer-shell-unstable-v1 wlr-foreign-toplevel-management-unstable-v1 wlr-virtual-pointer-unstable-v1 xdg-shell virtual-keyboard-unstable-v1 cursor-shape-v1 tablet-v2; do
    "$SCANNER" client-header "$DL/$x.xml" "$GEN/$x-client.h"
    "$SCANNER" private-code "$DL/$x.xml" "$GEN/$x-protocol.c"
done

# headers: the host's, checked for the versions the code was written against
need() { [ -f "$1" ] || { echo "missing build header $1" >&2; exit 1; }; }
need /usr/include/wayland-client.h; need /usr/include/png.h; need /usr/include/freetype2/ft2build.h
rm -rf "$INC/freetype2" "$INC/wayland" && mkdir -p "$INC/wayland"
cp -r /usr/include/freetype2 "$INC/"
cp /usr/include/png.h /usr/include/pngconf.h /usr/include/pnglibconf.h "$INC/"
cp /usr/include/wayland-client.h /usr/include/wayland-client-core.h /usr/include/wayland-util.h /usr/include/wayland-version.h /usr/include/wayland-client-protocol.h "$INC/"
grep -q 'define PNG_LIBPNG_VER_STRING "1\.6\.' "$INC/png.h" || { echo "libpng headers are not 1.6" >&2; exit 1; }
grep -q 'define WAYLAND_VERSION_MAJOR 1' "$INC/wayland-version.h" || { echo "wayland headers are not 1.x" >&2; exit 1; }
echo "dockgen: $(ls "$GEN" | wc -l) files; dockinc: freetype $(grep -h 'define FREETYPE_MAJOR\|define FREETYPE_MINOR' "$INC/freetype2/freetype/freetype.h" | awk '{print $3}' | paste -sd.), libpng $(grep -o 'PNG_LIBPNG_VER_STRING "[^"]*"' "$INC/png.h" | cut -d'"' -f2), wayland $(grep -h 'define WAYLAND_VERSION ' "$INC/wayland-version.h" | cut -d'"' -f2 | tr -d '\\n')"
