#!/bin/bash
# A signed TEST release of the current candidate for exercising the on-device update path before anything is published (docs/WISPOS-NOTES.md "Next device session", item 5).
#
#   os/dev/test-release.sh [--release] [--broken | --dead-kernel] [--build-id YYYYMMDD.N] [--version X.Y.Z] [--port 8765] [--address IP] [--no-serve] [WORKDIR]
#
# It copies the candidate's DEV SYSTEM (out/variants/SYSTEM-dev: a dev device must stay a dev device, or its first update would replace the dev tools and the dev update override with the
# release system's, found on the handheld 2026-10-12; --release uses out/SYSTEM instead) with /etc/os-release changed to say it is a NEWER build (nothing else differs), signs a manifest for that with the REAL release key
# (~/.wispos-release/update-signing.pem, which the images trust; the files never leave this LAN unless you publish them), and serves the result on plain http (the signature and the
# hashes still decide what is installed). KERNEL is the candidate's own. It then prints what to put on a DEV image so its update helper looks here.
#   --broken   the new system cannot start (its systemd is missing, so the initramfs would stop at "Could not find systemd"; on trial it restarts by itself instead): three starts and the card is back on the old slot with nobody touching it.
#   --dead-kernel   the new KERNEL loads (a valid arm64 Image header) and then hangs on its very first instruction (the branch at offset 0 is replaced by a branch to itself), so
#               its initramfs never runs: U-Boot's boot-trial script counts the starts, you power-cycle the handheld twice, and the third start comes up on the previous version.
#               Nothing restarts a dead kernel by itself. Only the first four bytes of KERNEL differ from the candidate's.
# Defaults: the build id and version are the candidate's with the sequence / patch number raised by one. Everything is written under WORKDIR/test-release (the HDD work tree).
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd); ROOT=$(cd "$HERE/../.." && pwd)
BROKEN=; DEAD=; RELEASE=; BUILD=; VERSION=; PORT=8765; ADDR=; SERVE=1; WORK=/mnt/hdd/wispos-staging/wispos-os-20261007
while [ $# -gt 0 ]; do
    case $1 in
        --broken) BROKEN=1 ;; --release) RELEASE=1 ;; --dead-kernel) DEAD=1 ;; --build-id) BUILD=$2; shift ;; --version) VERSION=$2; shift ;; --port) PORT=$2; shift ;; --address) ADDR=$2; shift ;; --no-serve) SERVE= ;;
        -*) echo "unknown option $1" >&2; exit 2 ;; *) WORK=$1 ;;
    esac; shift
done
case "$WORK" in /mnt/hdd/*) ;; *) echo "work directory must be on the HDD" >&2; exit 2 ;; esac
SYSTEM=$WORK/out/variants/SYSTEM-dev; { [ -z "$RELEASE" ] && [ -f "$SYSTEM" ]; } || SYSTEM=$WORK/out/SYSTEM; KERNEL=$WORK/out/kernel/Image; KEY=${WISP_RELEASE_KEY:-$HOME/.wispos-release/update-signing.pem}
[ -z "$BROKEN" ] || [ -z "$DEAD" ] || { echo "--broken and --dead-kernel are two different experiments: pick one" >&2; exit 2; }
for f in "$SYSTEM" "$KERNEL" "$KEY"; do [ -f "$f" ] || { echo "missing $f" >&2; exit 1; }; done
read -r CUR_BUILD CUR_VERSION < <(python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get("build_id") or "", d["version"])' "$ROOT/manifest/lockfile.json")
[ -n "$CUR_BUILD" ] || { echo "manifest/lockfile.json has no build_id" >&2; exit 1; }
[ -n "$BUILD" ] || BUILD=${CUR_BUILD%.*}.$(( ${CUR_BUILD##*.} + 1 ))
[ -n "$VERSION" ] || VERSION=$(echo "$CUR_VERSION" | awk -F. '{printf "%d.%d.%d", $1, $2, $3 + 1}')
[[ $BUILD =~ ^[0-9]{8}\.[0-9]+$ ]] || { echo "build id must look like YYYYMMDD.N, not '$BUILD'" >&2; exit 2; }
[[ $VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "version must look like X.Y.Z, not '$VERSION'" >&2; exit 2; }
[ -n "$ADDR" ] || ADDR=$(hostname -I | awk '{print $1}')
T=$WORK/test-release/$BUILD${BROKEN:+-broken}${DEAD:+-dead-kernel}; case "$T" in /mnt/hdd/*/test-release/*) rm -rf -- "$T" ;; *) exit 2 ;; esac
mkdir -p "$T/tree-parent" "$WORK/tmp"; export TMPDIR=$WORK/tmp
echo "restamping the candidate's SYSTEM as build $BUILD ($VERSION)${BROKEN:+, BROKEN on purpose}${DEAD:+, with a DEAD KERNEL on purpose}..."
export T SYSTEM BUILD VERSION BROKEN
fakeroot -- bash -c '
    unsquashfs -no-xattrs -f -d "$T/tree-parent/tree" "$SYSTEM" >/dev/null
    python3 -I - "$T/tree-parent/tree" "$BUILD" "$VERSION" <<PY
import re, sys
from pathlib import Path
p = Path(sys.argv[1]) / "etc/os-release"; build, version = sys.argv[2], sys.argv[3]
text = p.read_text()
for key, value in (("BUILD_ID", build), ("WISPOS_VERSION", version), ("VERSION_ID", version), ("PRETTY_NAME", "WispOS " + version)):
    text, n = re.subn(r"^%s=.*$" % key, "%s=\"%s\"" % (key, value), text, flags=re.M)
    assert n == 1, key
p.write_text(text)
PY
    [ -z "$BROKEN" ] || rm -f "$T/tree-parent/tree/usr/lib/systemd/systemd"
    mksquashfs "$T/tree-parent/tree" "$T/SYSTEM" -noappend -quiet -no-xattrs -comp zstd -Xcompression-level 19 -b 1M
'
rm -rf "$T/tree-parent"
if [ -n "$DEAD" ]; then                                              # the first instruction of an arm64 Image is `b primary_entry`: make it `b .`
    mkdir -p "$T"; python3 -I - "$KERNEL" "$T/Image.dead" <<'PY'
import sys
data = bytearray(open(sys.argv[1], 'rb').read())
assert data[56:60] == b'ARM\x64', 'not an arm64 Image'
data[0:4] = (0x14000000).to_bytes(4, 'little')
open(sys.argv[2], 'wb').write(data)
PY
    KERNEL=$T/Image.dead
fi
python3 -I "$ROOT/os/make-release.py" build --kernel "$KERNEL" --system "$T/SYSTEM" --build-id "$BUILD" --version "$VERSION" \
    --base-url "http://$ADDR:$PORT" --allow-http --key "$KEY" --tree "$WORK/rootfs/tree" --allow-gaps --out "$T/release" >"$T/make-release.log" 2>&1 || { cat "$T/make-release.log" >&2; exit 1; }
rm -f "$T/SYSTEM" "$T/Image.dead"
cat <<MSG

Test release $BUILD ($VERSION) is in $T/release (signed with the real release key; made with --allow-gaps: a test release is never published).
On a DEV image (it carries /usr/lib/wispos/DEV-BUILD; a release image ignores this file) point the update helper here:

    ssh root@DEVICE 'printf "{\"manifest_url\": \"http://$ADDR:$PORT/manifest.json\", \"allow_http\": true}\n" > /storage/.config/wispos/update-dev.json'

then Steam > Settings > System > Software Updates, or on the device: /usr/lib/wispos/wisp-update check ; /usr/lib/wispos/wisp-update apply $BUILD ; /usr/lib/wispos/wisp-update status
Remove the file again (rm /storage/.config/wispos/update-dev.json) to go back to the image's own source.
MSG
[ -z "$SERVE" ] || exec python3 -I "$HERE/serve_release.py" "$T/release" --port "$PORT"
