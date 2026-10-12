#!/bin/bash
# Keep a headless WispOS VM running for development, and run commands in it.
#
#   os/qemu-vm.sh start [WORKDIR]     boot out/qemu/test-image.img (made by qemu-test.sh, with its throwaway key)
#   os/qemu-vm.sh ssh CMD...          run a command in the VM as root
#   os/qemu-vm.sh put SRC DEST        copy a file into the VM
#   os/qemu-vm.sh get SRC DEST        copy a file out of the VM
#   os/qemu-vm.sh stop
#
# Same generic-arm64 test machine as qemu-test.sh (test-only virtio kernel, snapshot disk, SSH on localhost:$WISP_QEMU_SSH_PORT, default 2222). It is
# for exercising userspace, e.g. SDL programs with a dummy video driver, without the handheld.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${WISP_WORK:-/mnt/hdd/wispos-staging/wispos-os-20261007}
D=$WORK/qemu-boot; PIDF=$D/vm.pid; KEY=${WISP_VM_KEY:-$D/test-key}
PORT=${WISP_QEMU_SSH_PORT:-2222}
SSHO=(-p "$PORT" -i "$KEY" -o IdentitiesOnly=yes -o PasswordAuthentication=no -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=8)
case "${1:-}" in
  start)
    [ -n "${2:-}" ] && WORK=$2
    VMIMG=${WISP_VM_IMAGE:-$WORK/out/qemu/test-image.img}; [ -f "$VMIMG" ] && [ -f "$KEY" ] || { echo "run os/qemu-test.sh once first (it builds the test image and key)" >&2; exit 1; }
    [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null && { echo "already running"; exit 0; }
    export TMPDIR=$WORK/tmp; : >"$D/vm-serial.log"
    nohup setsid qemu-system-aarch64 -M virt -cpu cortex-a53 -smp 4 -m 1024 -display none -monitor none -serial "file:$D/vm-serial.log" \
        -kernel "$WORK/out/qemu/Image" -append "boot=LABEL=ROCKNIX disk=LABEL=STORAGE console=ttyAMA0 loglevel=4" \
        -drive "file=$VMIMG,format=raw,if=none,id=d0,snapshot=on" -device virtio-blk-pci,drive=d0 \
        -netdev user,id=n0,hostfwd=tcp:127.0.0.1:$PORT-:22 -device virtio-net-pci,netdev=n0 >"$D/vm.out" 2>&1 </dev/null &
    echo $! >"$PIDF"
    for _ in $(seq 1 120); do ssh "${SSHO[@]}" root@127.0.0.1 true 2>/dev/null && { echo "VM is up"; exit 0; }; sleep 5; done
    echo "VM did not come up; see $D/vm-serial.log" >&2; exit 1 ;;
  ssh) shift; ssh "${SSHO[@]}" root@127.0.0.1 "$@" ;;
  put) scp -q -P "$PORT" -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "$2" "root@127.0.0.1:$3" ;;
  get) scp -q -P "$PORT" -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "root@127.0.0.1:$2" "$3" ;;
  stop) [ -f "$PIDF" ] && kill "$(cat "$PIDF")" 2>/dev/null; rm -f "$PIDF"; echo stopped ;;
  *) sed -n 2,12p "$0"; exit 2 ;;
esac
