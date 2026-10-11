# Development builds: reaching the device for diagnosis

A **release** image contains no key, no Wi-Fi credential and no address of anyone's machine. A **dev** image
(`WISP_DEV=1`) adds, for the person building it only:

| What | Where it lands | Why |
| --- | --- | --- |
| a public SSH key | FAT partition `wispos/authorized_keys` (`WISP_SSH_KEY=`) | log in as root without a password |
| the builder's Wi-Fi (optional) | `wispos/wifi.conf` (`WISP_WIFI_CONF=`) | skip the first-run screen on the bench |
| an announce target | `wispos/dev.conf`, `announce=HOST:PORT` (`WISP_DEV_ANNOUNCE=`) | the device tells you where it is |
| the announcer | `wisp-dev-announce` and its unit, only in the dev SYSTEM | sends the device's address once it is online |

Everything is private to the builder: the SSH key is a *public* key (the private half never leaves the
builder's machine), the announce target is written to the card the builder flashes, and the dev SYSTEM is a
separate file (`out/variants/SYSTEM-dev`) so it can never replace the release SYSTEM. The build scripts refuse
any of those inputs without `WISP_DEV=1`. A dev image is named `...-dev.img`, carries `wispos/DEV-BUILD.txt`,
and its setup screen shows a red **DEV BUILD** label. Never publish one.

## Use

On the developer's machine, once:

    ssh-keygen -t ed25519 -N '' -f ~/.ssh/wispos-dev

Build (the announce address is this machine as the handheld will see it on the LAN):

    WISP_DEV=1 bash os/build-rootfs.sh
    WISP_DEV=1 WISP_SSH_KEY=~/.ssh/wispos-dev.pub WISP_DEV_ANNOUNCE=192.0.2.205:47653 bash os/build-image.sh
    # -> out/wispos-rg34xxsp-dev.img (+ .sha256), write it to the card as usual

Listen, then boot the handheld and go through the Wi-Fi screen:

    python3 os/dev/listen.py              # port 47653, state in ~/.wispos-dev

As soon as the device is online (by the setup screen, or by `wifi.conf`) it posts its hostname, interface,
address, MAC, kernel, uptime, systemd state and failed units, about once a minute. The listener prints the
command to use:

    ssh -i ~/.ssh/wispos-dev root@<address>

`~/.wispos-dev/devices.json` has the latest record per device (keyed by MAC), `devices.jsonl` every report,
and `GET /devices` on the listener returns the same JSON.

## What it does and does not do

- The listener binds `0.0.0.0` (the developer machine is headless) but answers only loopback, RFC 1918 and
  IPv6 unique-local sources, accepts nothing larger than 4 KiB, and keeps only known fields.
- The announcer sends no secret: not the Wi-Fi password, not any key, not the shadow file. It sends only to the
  address in `dev.conf`, and does nothing if `dev.conf` is absent.
- It does not open anything that a dev image did not already have: sshd and the key are the access; the
  announcer only says where to knock.
- A release image has none of this: `os/check_rootfs.py` fails if the release tree contains the announcer,
  `os/verify_image.py` fails if the release card has anything in `wispos/` besides `README.txt` and
  `wifi.conf.example`.

## Watching a device without writing to its card: `log-stream.sh`

The card is written as little as possible, in dev too (operator, 2026-10-11). Logs and samples therefore travel over ssh to the HDD of the developer machine and nothing is kept on the device:

    os/dev/log-stream.sh --host 192.0.2.102 --for 8h --period 60       # files under /mnt/hdd/wispos-staging/card-logs-YYYYMMDD/stream/

- `journal-<host>.log`: the device's journal, live (kernel messages, every service, the flight recorder's line every 10 s). A `# ... connect` / `disconnected` line marks each break; a reconnect asks for what it missed.
- `mem-<host>.jsonl`: one JSON line per period from `diag/mem-stream.py`: memory and zram, pressure, every Steam process (role, age, resident and swapped MB), and the KiB written to the boot card and the TF2 card since the previous line, with the processes that wrote most. The script is piped into `python3 -` over ssh, so it is not on the card either, and it ends with the connection.
- Start it BEFORE a test that might wedge the device: the device journal is RAM and a power-cycle erases it; the host keeps what was streamed until the device went quiet.
- The address comes from `--host`, `WISP_DEV_HOST`, or the listener's newest record (it warns when that is old: the announcer only reaches a listener that is running).
- New device-side dev tools should print to stdout (journal or the stream), not to `/storage`; keep file output for results that must survive a reboot and are small.

## Keeping the Steam sign-in across reflashes

`os/dev/steam-session.sh save` copies Steam's remembered-login state (a revocable token, not the password) from the device to
`~/.wispos-dev/steam-session/` (owner-only, outside the repo); `restore` puts it back on a freshly flashed device and restarts Steam.
Revoke it by signing out of all devices on the Steam account. Whether Steam accepts a restored token on a new flash is up to Steam.

## Driving the UI without hands: `wisp-dev-pad`

Dev images carry `wisp-dev-pad`, a virtual gamepad (never in releases). `wisp-dev-pad start --clone <part of the real pad's name>`
then `wisp-dev-pad press down down a`; take screenshots with `grim -l 1 FILE.png` (`XDG_RUNTIME_DIR=/var/run/0-runtime-dir
WAYLAND_DISPLAY=wayland-1`). It never types text.

## Tests

- `python3 -I os/test_dev.py` — the listener, and that the dev overlay is behind the flag.
- `bash os/dev-test.sh` — builds a dev image with a throwaway key, boots it in the emulated VM, and checks that
  it announces its DHCP address, that the key logs in, and that the release SYSTEM has none of it.
