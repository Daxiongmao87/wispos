#!/bin/bash
# Keep a dev device's Steam sign-in across card reflashes. DEV ONLY; never part of an image or the repo.
#
#   os/dev/steam-session.sh save      copy Steam's saved login state from the device to ~/.wispos-dev/steam-session/
#   os/dev/steam-session.sh restore   put it back on a freshly flashed device (stops Steam, restores, starts Steam)
#
# What is saved is Steam's own remembered-login state (config.vdf, registry.vdf, the account's userdata): a revocable token,
# not the password. It is credential-equivalent, so the directory is owner-only and stays on this machine. To revoke it, sign out of
# all devices from the Steam account page (Steam > Account > Manage Steam Guard). Whether Steam accepts a restored token on a freshly
# flashed system depends on what Steam binds it to (machine id, etc.); `restore` says what it did and the screen shows whether it worked.
set -euo pipefail
DIR=$HOME/.wispos-dev/steam-session
HOST=${WISP_DEV_HOST:-$(python3 -I -c 'import json,os; print(list(json.load(open(os.path.expanduser("~/.wispos-dev/devices.json"))).values())[0]["ip"])')}
KEY=${WISP_DEV_KEY:-$HOME/.ssh/wispos-dev}
SSH=(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o UserKnownHostsFile="$HOME/.wispos-dev/known_hosts" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "root@$HOST")
FILES='runtime/steam/config/config.vdf home/.steam/registry.vdf runtime/steam/userdata'
umask 077
case "${1:-}" in
  save)
    mkdir -p "$DIR"; chmod 700 "$DIR"
    "${SSH[@]}" "cd /storage/wispos && tar -cf - $FILES" >"$DIR/session.tar.new"
    listing=$(tar -tf "$DIR/session.tar.new")          # not `tar | grep -q`: under pipefail grep's early exit makes tar fail with SIGPIPE
    grep -q 'config/config.vdf' <<<"$listing" || { rm -f "$DIR/session.tar.new"; echo "saved archive has no config.vdf; kept the old backup" >&2; exit 1; }
    mv "$DIR/session.tar.new" "$DIR/session.tar"; chmod 600 "$DIR/session.tar"
    echo "saved $(tar -tf "$DIR/session.tar" | wc -l) entries ($(stat -c %s "$DIR/session.tar") bytes) from $HOST to $DIR/session.tar" ;;
  restore)
    [ -f "$DIR/session.tar" ] || { echo "nothing saved: run save first" >&2; exit 1; }
    "${SSH[@]}" 'systemctl stop wisp-steam; sleep 2' || true
    "${SSH[@]}" 'mkdir -p /storage/wispos && tar -C /storage/wispos -xf -' <"$DIR/session.tar"
    "${SSH[@]}" 'systemctl start --no-block wisp-steam'
    echo "restored onto $HOST and started Steam; watch the screen to see whether it signed in" ;;
  *) echo "usage: $0 save|restore" >&2; exit 2 ;;
esac
