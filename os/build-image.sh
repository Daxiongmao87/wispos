#!/bin/bash
# Assemble the WispOS card image from the pieces the other build scripts produce.
#
#   os/build-image.sh [WORKDIR]       default /mnt/hdd/wispos-staging/wispos-os-20261007
#
# Needs in $WORK/out: u-boot-sunxi-with-spl.bin (build-uboot.sh), kernel/ (build-kernel.sh),
# SYSTEM + SYSTEM.md5 (build-rootfs.sh). Produces $WORK/out/wispos-rg34xxsp.img:
#
#   byte 0        MBR (disk id 0x57495350), partition 1 FAT32 label ROCKNIX from 16 MiB, bootable;
#                 partition 2 ext4 label STORAGE right behind it (32 MiB, grown at first boot)
#   byte 8192     our U-Boot with SPL and BL31 (where the Allwinner boot ROM looks)
#   BOOT          KERNEL, KERNEL.md5, SYSTEM, SYSTEM.md5, dtb.img, extlinux/, overlays/, wispos/
#                 (wispos/: README, wifi.conf.example. No keys or credentials, except in a dev build:
#                 WISP_DEV=1 with WISP_WIFI_CONF=file and/or WISP_SSH_KEY=file, named ...-dev.img and
#                 marked wispos/DEV-BUILD.txt)
#   STORAGE       only `.please_resize_me`: the initramfs then runs the first-boot resize
#
# The labels stay ROCKNIX/STORAGE because the initramfs and the rootfs scripts address them by
# those names. Nothing here needs root: the FAT volume is filled by os/fatwrite.py and the ext4
# volume by mke2fs plus debugfs. Verify the result with os/verify_image.py.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${1:-/mnt/hdd/wispos-staging/wispos-os-20261007}
case "$WORK" in /mnt/hdd/*) ;; *) echo "work directory must be on the HDD" >&2; exit 2 ;; esac
OUT=$WORK/out; STAGE=$WORK/image
# Overrides, used to build the stage-isolation images (see docs/WISPOS-FIRST-BOOT-TEST.md): any of the
# parts can be swapped for ROCKNIX's reference part to find which stage a failing card breaks at.
# Dev/debug builds are the only ones that may carry a key or a Wi-Fi credential. Without WISP_DEV=1 the
# script refuses them, so a release image cannot pick one up by accident. Dev images are named and
# marked as such and are never to be published.
if [ -n "${WISP_SSH_KEY:-}${WISP_WIFI_CONF:-}${WISP_DEV_ANNOUNCE:-}" ] && [ "${WISP_DEV:-}" != 1 ]; then
    echo "refusing: WISP_SSH_KEY / WISP_WIFI_CONF / WISP_DEV_ANNOUNCE put credentials or a developer address in the image; that needs WISP_DEV=1 (dev build)" >&2; exit 2
fi
DEFAULT_NAME=wispos-rg34xxsp.img; [ "${WISP_DEV:-}" = 1 ] && DEFAULT_NAME=wispos-rg34xxsp-dev.img
IMG=$OUT/${WISP_IMAGE_NAME:-$DEFAULT_NAME}
KERNEL_IMG=${WISP_KERNEL_IMAGE:-$OUT/kernel/Image}
DEFAULT_SYSTEM=$OUT/SYSTEM; [ "${WISP_DEV:-}" = 1 ] && DEFAULT_SYSTEM=$OUT/variants/SYSTEM-dev     # a dev image carries the dev SYSTEM
SYSTEM_IMG=${WISP_SYSTEM:-$DEFAULT_SYSTEM}; SYSTEM_MD5=${WISP_SYSTEM_MD5:-$DEFAULT_SYSTEM.md5}
BOOT_MIB=${WISP_BOOT_MIB:-1024}
for f in "$OUT/u-boot-sunxi-with-spl.bin" "$KERNEL_IMG" "$SYSTEM_IMG" "$SYSTEM_MD5" \
         "$OUT/kernel/dtbs/sun50i-h700-anbernic-rg34xx-sp-v2-panel.dtb" \
         "$OUT/kernel/overlays/sun50i-h700-anbernic-rg35xx-2024-ddr3.dtbo"; do
    [ -f "$f" ] || { echo "missing input: $f" >&2; exit 1; }
done
mkdir -p "$WORK/tmp"; export TMPDIR=$WORK/tmp
MIB=$((1 << 20)); BOOT_OFFSET=$((16 * MIB)); BOOT_SIZE=$((BOOT_MIB * MIB)); STORAGE_SIZE=$((32 * MIB))
case "$STAGE" in /mnt/hdd/*/image) rm -rf -- "$STAGE" ;; *) echo "refusing to clear $STAGE" >&2; exit 2 ;; esac
mkdir -p "$STAGE/boot/extlinux" "$STAGE/boot/overlays" "$STAGE/storage"

# --- BOOT contents ---
cp "$KERNEL_IMG" "$STAGE/boot/KERNEL"
printf '%s  target/KERNEL\n' "$(md5sum "$STAGE/boot/KERNEL" | cut -d' ' -f1)" >"$STAGE/boot/KERNEL.md5"
cp "$SYSTEM_IMG" "$STAGE/boot/SYSTEM"; cp "$SYSTEM_MD5" "$STAGE/boot/SYSTEM.md5"
cp "$OUT/kernel/dtbs/sun50i-h700-anbernic-rg34xx-sp-v2-panel.dtb" "$STAGE/boot/dtb.img"
cp "$OUT/kernel/overlays/"*.dtbo "$STAGE/boot/overlays/"
# ROCKNIX's boot arguments plus the WispOS boot splash's (docs/WISPOS-SPLASH-DESIGN.md). The splash covers the console (it puts the terminal in graphics mode), and the
# console keeps filling behind it, so SELECT shows the real boot log. `quiet loglevel=3` keeps the kernel's early text off the panel until the splash starts, and
# systemd.show_status=true keeps systemd's own lines (which `quiet` would hide) in the log you get with SELECT. logo.nologo and the cursor setting remove the penguin and
# the blinking block. A dev image is not quiet (the kernel text is on the UART and in the log view) and says so on the splash (wisp.dev).
BOOT_ARGS="boot=LABEL=ROCKNIX disk=LABEL=STORAGE console=ttyS0,115200 console=tty0 systemd.debug_shell=ttyS0 logo.nologo vt.global_cursor_default=0"
if [ "${WISP_DEV:-}" = 1 ]; then BOOT_ARGS="$BOOT_ARGS wisp.dev"; else BOOT_ARGS="$BOOT_ARGS quiet loglevel=3 systemd.show_status=true"; fi
cat >"$STAGE/boot/extlinux/extlinux.conf" <<EOF
LABEL WispOS
  LINUX /KERNEL
  FDT /dtb.img
  APPEND $BOOT_ARGS
  FDTOVERLAYS /overlays/sun50i-h700-anbernic-rg35xx-2024-ddr3.dtbo
EOF
# Settings the operator can edit with the card in any PC (applied by wisp-firstboot at every boot).
mkdir -p "$STAGE/boot/wispos"
cat >"$STAGE/boot/wispos/README.txt" <<'EOT'
WispOS boot partition
=====================
Optional files in this folder are applied at every boot:

  wifi.conf         two lines:   ssid=YOUR NETWORK NAME
                                 psk=YOUR PASSPHRASE
                    (see wifi.conf.example). Joins that WPA2 network and keeps it.
  authorized_keys   OpenSSH public keys allowed to log in as root and as wisp.
  ssh               an empty file named "ssh": turns SSH on at the first boot (the Raspberry Pi convention).

SSH is OFF by default. Turn it on with that file, or on the device with `wisp-ssh on` (desktop mode's menu does the same);
the choice persists. Then log in as user "wisp", password "wisp". The system partition is read-only, so the
password is part of the image (os/accounts.py); use authorized_keys to avoid depending on it.
Everything else lives on the STORAGE partition under /storage.
EOT
printf 'ssid=MyNetwork\npsk=MyPassphrase\n' >"$STAGE/boot/wispos/wifi.conf.example"
# Dev build only (guarded above): the builder's own Wi-Fi settings and/or SSH key, plus a marker file.
if [ "${WISP_DEV:-}" = 1 ]; then
    printf 'DEVELOPMENT BUILD. This image may contain a key or Wi-Fi credential. Do not publish or share it.\n' >"$STAGE/boot/wispos/DEV-BUILD.txt"
    : >"$STAGE/boot/wispos/ssh"                                                   # a development image has SSH on by default (operator, 2026-10-11)
    if [ -n "${WISP_WIFI_CONF:-}" ]; then cp "$WISP_WIFI_CONF" "$STAGE/boot/wispos/wifi.conf"; fi
    if [ -n "${WISP_SSH_KEY:-}" ]; then cp "$WISP_SSH_KEY" "$STAGE/boot/wispos/authorized_keys"; fi
    if [ -n "${WISP_DEV_ANNOUNCE:-}" ]; then printf 'announce=%s\n' "$WISP_DEV_ANNOUNCE" >"$STAGE/boot/wispos/dev.conf"; fi
fi
: >"$STAGE/storage/.please_resize_me"; chmod 644 "$STAGE/storage/.please_resize_me"
# Timestamps inside the ext4 volume come from a fixed clock (mke2fs -d would copy the staging
# files' real ctime), so two builds of the same inputs are byte-identical.
export E2FSPROGS_FAKE_TIME=$(date -u -d '2026-10-01 18:31:19' +%s)

# --- disk ---
rm -f "$IMG"; truncate -s $((BOOT_OFFSET + BOOT_SIZE + STORAGE_SIZE)) "$IMG"
sfdisk --quiet "$IMG" <<EOF
label: dos
label-id: 0x57495350
unit: sectors

start=$((BOOT_OFFSET / 512)), size=$((BOOT_SIZE / 512)), type=c, bootable
start=$(((BOOT_OFFSET + BOOT_SIZE) / 512)), size=$((STORAGE_SIZE / 512)), type=83
EOF
dd if="$OUT/u-boot-sunxi-with-spl.bin" of="$IMG" bs=1024 seek=8 conv=notrunc status=none

mkfs.fat -F 32 -s 8 -n ROCKNIX -i 57495350 --offset=$((BOOT_OFFSET / 512)) "$IMG" $((BOOT_SIZE / 1024)) >/dev/null
python3 -I "$HERE/fatwrite.py" "$IMG" "$BOOT_OFFSET" "$STAGE/boot"

# resize=: the first boot grows this 32 MiB volume to the whole card. mke2fs reserves growth room for only 1024x by default
# (3 reserved GDT blocks here, enough for ~25 GiB); a 58 GB card then makes resize2fs corrupt the volume and the next boot's
# fsck stops with "Filesystem corruption has been detected!". 2^29 blocks of 4 KiB = 2 TiB, the microSD maximum.
truncate -s "$STORAGE_SIZE" "$STAGE/storage.ext4"
mke2fs -q -F -t ext4 -L STORAGE -U 57495350-0000-4000-8000-000000000002 -b 4096 \
    -O 64bit,metadata_csum,metadata_csum_seed -E root_owner=0:0,hash_seed=57495350-0000-4000-8000-000000000003,resize=536870912 \
    "$STAGE/storage.ext4" $((STORAGE_SIZE / 4096))
debugfs -w -R "write $STAGE/storage/.please_resize_me .please_resize_me" "$STAGE/storage.ext4" >/dev/null
dd if="$STAGE/storage.ext4" of="$IMG" bs=$MIB seek=$(((BOOT_OFFSET + BOOT_SIZE) / MIB)) conv=notrunc status=none

sha256sum "$IMG" | tee "$IMG.sha256"
ls -ls "$IMG"
