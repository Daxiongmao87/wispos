#!/bin/bash
# Copy the Steam runtime onto a running WispOS device over SSH and start the session.
#
#   os/push-steam-runtime.sh [--dry-run] [--no-start] TARGET [RUNTIME_DIR]
#
#   TARGET       user@host of the device (root, by a key the device's owner has installed)
#   RUNTIME_DIR  a directory holding READY, lib/, meta/ and steam/ (default: the runtime that
#                reached Steam's sign-in under emulation, in the staging tree)
#
# The runtime is about 3 GB. It is not part of the image because the image's STORAGE partition is
# small until the first-boot resize, and because Steam updates itself afterwards. Copy order makes
# an interrupted copy harmless: READY, which the session unit requires, is sent last. Re-running
# resumes (rsync). Logs, caches and crash markers from earlier runs are not copied. The device must
# have >= 4 GiB free on /storage. Nothing outside /storage/wispos/runtime is touched.
set -euo pipefail
DRY=; START=yes
while [ $# -gt 0 ]; do
    case "$1" in --dry-run) DRY=--dry-run ;; --no-start) START=no ;; *) break ;; esac; shift
done
TARGET=${1:?usage: push-steam-runtime.sh [--dry-run] [--no-start] user@host [runtime-dir]}
SRC=${2:-/mnt/hdd/wispos-staging/steam-lse-scan-20261006/qemu-env/wispos/runtime}
: "${WISP_SSH_PRIVATE_KEY:?set WISP_SSH_PRIVATE_KEY to a key the device owner has authorized}"
KEY=$WISP_SSH_PRIVATE_KEY
[ -f "$SRC/READY" ] && [ -d "$SRC/steam/steamrtarm64" ] || { echo "not a Steam runtime directory: $SRC" >&2; exit 1; }
file "$SRC/steam/steamrtarm64/steam" | grep -q 'ARM aarch64' || { echo "steam executable is not aarch64" >&2; exit 1; }
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o PasswordAuthentication=no -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 ${WISP_SSH_PORT:+-p $WISP_SSH_PORT})
read -ra EXTRA <<<"${WISP_SSH_EXTRA:-}"; SSH+=("${EXTRA[@]}")      # e.g. -o UserKnownHostsFile=/dev/null for a test VM
remote() { "${SSH[@]}" "$TARGET" "$@"; }

name=$(remote '. /etc/os-release; echo "$OS_NAME"') || { echo "cannot reach $TARGET" >&2; exit 1; }
[ "$name" = WispOS ] || { echo "$TARGET is '$name', not WispOS; refusing" >&2; exit 1; }
free_kib=$(remote "df -k /storage | awk 'NR==2 {print \$4}'")
need_kib=$((4 * 1024 * 1024))
if [ "${WISP_SKIP_SPACE_CHECK:-}" != 1 ] && [ "$free_kib" -lt "$need_kib" ]; then
    echo "only $((free_kib / 1024)) MiB free on /storage; need $((need_kib / 1024)). Has the first-boot resize run?" >&2; exit 1
fi
remote 'mkdir -p /storage/wispos/runtime /storage/wispos/logs /storage/wispos/home'

RSYNC=(rsync -a --partial --info=stats1 $DRY -e "${SSH[*]}"
       --exclude=/READY --exclude=/steam/logs/ --exclude=/steam/.crash --exclude=/steam/dumps/
       --exclude=/steam/config/htmlcache/ --exclude=/steam/appcache/httpcache/)
"${RSYNC[@]}" "$SRC/" "$TARGET:/storage/wispos/runtime/"
[ -n "$DRY" ] && { echo "dry run: nothing copied"; exit 0; }
scp -q -i "$KEY" -o IdentitiesOnly=yes ${WISP_SSH_PORT:+-P $WISP_SSH_PORT} "${EXTRA[@]}" "$SRC/READY" "$TARGET:/storage/wispos/runtime/READY"
remote 'sync; test -x /storage/wispos/runtime/steam/steamrtarm64/steam && test -f /storage/wispos/runtime/READY' \
    || { echo "copy finished but the runtime is incomplete on the device" >&2; exit 1; }
echo "runtime installed under /storage/wispos/runtime"
if [ "$START" = yes ]; then
    remote 'systemctl start wisp-steam' && echo "wisp-steam started (first start takes minutes; see /storage/wispos/logs/session.log)"
fi
