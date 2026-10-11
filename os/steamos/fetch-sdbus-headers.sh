#!/bin/bash
# Fetch systemd's PUBLIC headers (sd-bus.h and friends) for building wisp-steamos-manager. Only the headers are needed: the program links against the libsystemd already in the
# image (and, for the host tests, the host's). The source is the release the image runs, pinned by hash (the same pin as ROCKNIX's systemd package recipe).
#   os/steamos/fetch-sdbus-headers.sh [WORKDIR]     prints the include directory (WORKDIR/sdbus-inc, holding systemd/sd-bus.h); idempotent
set -euo pipefail
WORK=${1:-/mnt/hdd/wispos-staging/wispos-os-20261007}
case "$WORK" in /mnt/hdd/*) ;; *) echo "work directory must be on the HDD" >&2; exit 2 ;; esac
VER=261.2; SHA=ed1059ff964f5df35b6056434cc17cc83f86dc913f10489948a0b19b6081c5ec
TAR=$WORK/dl/systemd-$VER.tar.gz; INC=$WORK/sdbus-inc
if [ -f "$INC/systemd/sd-bus.h" ] && [ "$(cat "$INC/.source-sha256" 2>/dev/null)" = "$SHA" ]; then echo "$INC"; exit 0; fi
mkdir -p "$WORK/dl"
[ -f "$TAR" ] || curl -sfL --retry 3 -m 300 -o "$TAR" "https://github.com/systemd/systemd/archive/v$VER.tar.gz"
[ "$(sha256sum "$TAR" | cut -c1-64)" = "$SHA" ] || { echo "hash mismatch for $TAR" >&2; exit 1; }
case "$INC" in /mnt/hdd/*/sdbus-inc) rm -rf -- "$INC" ;; *) echo "refusing to clear $INC" >&2; exit 2 ;; esac
mkdir -p "$INC/systemd"; tar -xzf "$TAR" -C "$INC/systemd" --strip-components=3 --wildcards "systemd-$VER/src/systemd/*.h"
printf '%s\n' "$SHA" >"$INC/.source-sha256"; echo "$INC"
