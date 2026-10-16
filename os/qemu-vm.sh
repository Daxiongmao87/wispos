#!/bin/bash
# Keep a headless WispOS VM running for development, and run commands in it.
#
#   os/qemu-vm.sh start [WORKDIR]     boot out/qemu/test-image.img (made by qemu-test.sh, with its throwaway key)
#   os/qemu-vm.sh ssh CMD...          run a command in the VM as root
#   os/qemu-vm.sh put SRC DEST        copy a file into the VM
#   os/qemu-vm.sh get SRC DEST        copy a file out of the VM
#   os/qemu-vm.sh stop
#
# WISP_QEMU_GPU=1 boots the GPU test kernel (out/qemu/Image-gpu: WISP_QEMU_GPU=1 os/boot/build-qemu-kernel.sh) with a virtual 720x480 panel (virtio-gpu) and virtual keyboard and
# mouse, so the real graphical session can run (sway uses Mesa's software renderer there; no environment change is needed).
# WISP_QEMU_VNC=N also serves the panel on VNC display :N (127.0.0.1, port 5900+N, password required: `vnc-password` sets it through the monitor socket) and uses an absolute-pointer
# tablet instead of the mouse, so a person can sign in through noVNC (`websockify --web /usr/share/novnc 0.0.0.0:PORT 127.0.0.1:5900+N`). WISP_QEMU_PERSIST=1 keeps the disk's writes
# (the disk is then WISP_VM_IMAGE itself: use a copy): a harness restart no longer loses Steam's install.
# WISP_QEMU_SMP / WISP_QEMU_MEM (default 4 / 1024, the handheld's) give the emulator more to work with: Steam's UI stalls past its own 15 s main-loop assert on an emulated A53, so bringing
# it up here takes more than the handheld has. WISP_QEMU_APPEND adds kernel arguments (for example systemd.mask=wisp-steam.service to start the session by hand).
#
# Same generic-arm64 test machine as qemu-test.sh (test-only virtio kernel, snapshot disk, SSH on localhost:$WISP_QEMU_SSH_PORT, default 2229). It is
# for exercising userspace, e.g. SDL programs with a dummy video driver, without the handheld.
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
WORK=${WISP_WORK:-/mnt/hdd/wispos-staging/wispos-os-20261007}
D=$WORK/qemu-boot; PIDF=$D/vm.pid; KEY=${WISP_VM_KEY:-$D/test-key}
PORT=${WISP_QEMU_SSH_PORT:-2229}
SSHO=(-p "$PORT" -i "$KEY" -o IdentitiesOnly=yes -o PasswordAuthentication=no -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=8)
case "${1:-}" in
  start)
    [ -n "${2:-}" ] && WORK=$2
    VMIMG=${WISP_VM_IMAGE:-$WORK/out/qemu/test-image.img}; [ -f "$VMIMG" ] && [ -f "$KEY" ] || { echo "run os/qemu-test.sh once first (it builds the test image and key)" >&2; exit 1; }
    [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null && { echo "already running"; exit 0; }
    export TMPDIR=$WORK/tmp; : >"$D/vm-serial.log"
    KIMG=$WORK/out/qemu/Image; GPU=(); MON=(-monitor none); SNAP=,snapshot=on; POINTER=virtio-mouse-pci
    [ "${WISP_QEMU_PERSIST:-0}" = 1 ] && SNAP=
    [ -n "${WISP_QEMU_VNC:-}" ] && { POINTER=virtio-tablet-pci; MON=(-monitor "unix:$D/monitor.sock,server,nowait"); GPU=(-vnc "127.0.0.1:$WISP_QEMU_VNC,password=on"); rm -f "$D/monitor.sock"; }
    [ "${WISP_QEMU_GPU:-0}" = 1 ] && { KIMG=$WORK/out/qemu/Image-gpu; [ -f "$KIMG" ] || { echo "need $KIMG (WISP_QEMU_GPU=1 os/boot/build-qemu-kernel.sh)" >&2; exit 1; }
        GPU+=(-device virtio-gpu-pci,xres=720,yres=480 -device virtio-keyboard-pci -device "$POINTER"); }
    nohup setsid qemu-system-aarch64 -M virt -cpu cortex-a53 -smp ${WISP_QEMU_SMP:-4} -m ${WISP_QEMU_MEM:-1024} -display none "${MON[@]}" -serial "file:$D/vm-serial.log" "${GPU[@]}" \
        -kernel "$KIMG" -append "boot=LABEL=ROCKNIX disk=LABEL=STORAGE console=ttyAMA0 loglevel=4 ${WISP_QEMU_APPEND:-}" \
         -drive "file=$VMIMG,format=raw,if=none,id=d0$SNAP" -device virtio-blk-pci,drive=d0 \
        -netdev user,id=n0,hostfwd=tcp:127.0.0.1:$PORT-:22 -device virtio-net-pci,netdev=n0 >"$D/vm.out" 2>&1 </dev/null &
    echo $! >"$PIDF"
    for _ in $(seq 1 120); do ssh "${SSHO[@]}" root@127.0.0.1 true 2>/dev/null && { echo "VM is up"; exit 0; }; sleep 5; done
    echo "VM did not come up; see $D/vm-serial.log" >&2; exit 1 ;;
  ssh) shift; ssh "${SSHO[@]}" root@127.0.0.1 "$@" ;;
  put) scp -q -P "$PORT" -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "$2" "root@127.0.0.1:$3" ;;
  get) scp -q -P "$PORT" -i "$KEY" -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR "root@127.0.0.1:$2" "$3" ;;
  vnc-password) # vnc-password PASSWORD: set the VNC password through the monitor socket (needs WISP_QEMU_VNC at start)
    python3 -I - "$D/monitor.sock" "${2:?password}" <<'PY'
import socket, sys, time
s = socket.socket(socket.AF_UNIX); s.connect(sys.argv[1]); time.sleep(0.3); s.recv(4096)
s.sendall(('set_password vnc %s\n' % sys.argv[2]).encode()); time.sleep(0.5); print(s.recv(4096).decode(errors='replace').strip().splitlines()[-1:] or 'ok')
PY
    ;;
  stop) [ -f "$PIDF" ] && kill "$(cat "$PIDF")" 2>/dev/null; rm -f "$PIDF"; echo stopped ;;
  *) sed -n 2,12p "$0"; exit 2 ;;
esac
