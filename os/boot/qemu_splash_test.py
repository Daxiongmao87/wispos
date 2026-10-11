#!/usr/bin/env python3
"""Boot the patched initramfs (the WispOS boot splash) in QEMU with a 720x480 virtual GPU and watch what is on the "panel" (docs/WISPOS-SPLASH-DESIGN.md).
usage: qemu_splash_test.py KERNEL_IMAGE DISK_IMAGE OUT_DIR [SECONDS=240]
KERNEL_IMAGE is a test kernel with the patched initramfs embedded and DRM_VIRTIO_GPU (os/boot/make-initramfs.sh + a test-kernel build), DISK_IMAGE the WispOS test image (opened
snapshot=on, never modified). It takes QMP screendumps while the guest boots, writes PNGs and a log into OUT_DIR, and prints a verdict:
  - the splash appears (the "WispOS" lock-up is on the panel: white pixels where the Title is) and how many seconds after QEMU started;
  - the guest reaches userspace (sshd answers; the image's first boot resizes STORAGE and reboots once, so the splash is seen twice) and the splash PROCESS is still running
    in the new root (switch_root moved /dev, so /dev/wisp-splash.fifo is the same pipe);
  - a status line written to that FIFO changes /dev/fb0 (read over ssh: the fbdev picture, which sway's DRM master does not hide) and nothing but the status moves;
  - `ready` makes the splash exit.
This is emulation of a generic arm64 machine with a virtual GPU: it proves the boot integration (init -> wrapper -> daemonised splash -> switch_root), not the H700 panel."""
import json, os, socket, subprocess, sys, time
from pathlib import Path
from PIL import Image

W, H = 720, 480
SSH_PORT = int(os.environ.get('WISP_QEMU_SSH_PORT', '2231'))      # host port forwarded to the guest's sshd (2223 is often taken by a docker-proxy here)
NAME_REGION = (290, 198, 430, 228)          # "WispOS" in Title (os/ui/wisp-ui.h WISP_UI_BOOT_NAME_Y, centred)
STATUS_REGION = (100, 300, 620, 328)        # the current stage in Body
ABOVE_SPINNER_AND_HINT = ((0, 0, W, 240), (0, 340, W, H))   # badge + name (static), hint pill (static)


class Qmp:
    def __init__(self, path, tries=100):
        self.s = socket.socket(socket.AF_UNIX)
        for _ in range(tries):
            try: self.s.connect(path); break
            except OSError: time.sleep(0.1)
        else: raise OSError('QMP socket never appeared')
        self.f = self.s.makefile('rw'); self.f.readline()                       # greeting
        self.cmd('qmp_capabilities')

    def cmd(self, name, **args):
        self.f.write(json.dumps({'execute': name, 'arguments': args}) + '\n'); self.f.flush()
        while True:
            r = json.loads(self.f.readline())
            if 'return' in r or 'error' in r: return r


def shot(qmp, out_dir, name):
    ppm = Path(out_dir) / (name + '.ppm')
    qmp.cmd('screendump', filename=str(ppm))
    for _ in range(50):
        if ppm.exists() and ppm.stat().st_size > 1000: break
        time.sleep(0.1)
    time.sleep(0.2)
    img = Image.open(ppm).convert('RGB'); ppm.unlink()
    img.save(Path(out_dir) / (name + '.png'))
    return img


def has_white(img, box):
    """The splash is up: a black ground (the setup screen's ground is navy, and its buttons are white) with white pixels where the name is."""
    return img.getpixel((5, 5)) == (0, 0, 0) and any(img.getpixel((x, y)) == (255, 255, 255) for y in range(box[1], box[3]) for x in range(box[0], box[2]) if x < img.width and y < img.height)


def region_sig(img, box): return img.crop(box).tobytes()


def ssh(key, *cmd, timeout=15, binary=False):
    """Run a command as root in the guest (the test image carries the throwaway key of os/qemu-test.sh). Returns (exit status, stdout).
    ssh joins its arguments into ONE string for the guest's shell, so a command with quotes or a redirect is passed as a single argument."""
    r = subprocess.run(['ssh', '-p', str(SSH_PORT), '-i', str(key), '-o', 'IdentitiesOnly=yes', '-o', 'PasswordAuthentication=no', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=no',
                        '-o', 'UserKnownHostsFile=/dev/null', '-o', 'LogLevel=ERROR', '-o', 'ConnectTimeout=3', 'root@127.0.0.1', *cmd], capture_output=True, timeout=timeout)
    return r.returncode, (r.stdout if binary else r.stdout.decode(errors='replace').strip())


def read_fb(key, out_dir, name):
    """The framebuffer as the splash drew it: 720 x 480 x 4 bytes, BGRA (stride 2880)."""
    rc, raw = ssh(key, 'dd', 'if=/dev/fb0', 'bs=2880', 'count=480', 'status=none', binary=True)
    if rc != 0 or len(raw) != W * H * 4: return None
    img = Image.frombuffer('RGBA', (W, H), raw, 'raw', 'BGRA', 0, 1).convert('RGB')
    img.save(Path(out_dir) / (name + '.png')); return img


def guest_checks(key, out, verdict):
    """Once sshd answers: the splash process lives in the new root, its FIFO is there, `status` moves only the status line, `ready` ends it."""
    verdict['splash_pid_after_switch_root'] = ssh(key, 'pidof', 'wisp-splash')[1] or None
    verdict['fifo_present'] = ssh(key, 'test', '-p', '/dev/wisp-splash.fifo')[0] == 0
    before = read_fb(key, out, 'fb-before')
    ssh(key, "echo 'status Checking the stage line' > /dev/wisp-splash.fifo"); time.sleep(1.5)
    after = read_fb(key, out, 'fb-after-status')
    if before and after:
        verdict['status_changed'] = region_sig(before, STATUS_REGION) != region_sig(after, STATUS_REGION)
        # the spinner (between the name and the status) animates by itself; what must stay untouched is the badge and name above it and the hint pill below the status
        verdict['only_status_moved'] = all(region_sig(before, b) == region_sig(after, b) for b in ABOVE_SPINNER_AND_HINT)
    ssh(key, "echo ready > /dev/wisp-splash.fifo"); time.sleep(2)
    verdict['ready_exits'] = not ssh(key, 'pidof', 'wisp-splash')[1]


def main(kernel, disk, out_dir, seconds=240):
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    sock = out / 'qmp.sock'
    if sock.exists(): sock.unlink()
    serial = out / 'serial.log'; serial.write_bytes(b'')
    cmdline = 'boot=LABEL=ROCKNIX disk=LABEL=STORAGE console=ttyAMA0 console=tty0 quiet loglevel=3 logo.nologo vt.global_cursor_default=0'
    q = subprocess.Popen(['qemu-system-aarch64', '-M', 'virt', '-cpu', 'cortex-a53', '-smp', '4', '-m', '1024', '-display', 'none', '-monitor', 'none',
                          '-serial', f'file:{serial}', '-qmp', f'unix:{sock},server,nowait', '-device', f'virtio-gpu-pci,xres={W},yres={H}',
                          '-kernel', kernel, '-append', cmdline, '-drive', f'file={disk},format=raw,if=none,id=d0,snapshot=on', '-device', 'virtio-blk-pci,drive=d0',
                          '-netdev', f'user,id=n0,hostfwd=tcp:127.0.0.1:{SSH_PORT}-:22', '-device', 'virtio-net-pci,netdev=n0'], stdout=open(out / 'qemu.out', 'w'), stderr=subprocess.STDOUT)
    key = Path(os.environ.get('WISP_QEMU_KEY', Path(disk).resolve().parents[2] / 'qemu-boot/test-key'))
    t0 = time.time(); verdict = {'splash_first_seen_s': None, 'ssh_up_s': None, 'splash_pid_after_switch_root': None, 'fifo_present': None, 'status_changed': None, 'only_status_moved': None,
                                 'ready_exits': None}
    try:
        qmp = Qmp(str(sock))
        last = None; n = 0
        while time.time() - t0 < seconds:
            time.sleep(3 if time.time() - t0 < 60 else 8)
            n += 1
            img = shot(qmp, out, 'frame-%03d' % n)
            t = time.time() - t0
            seen = has_white(img, NAME_REGION)
            if seen and verdict['splash_first_seen_s'] is None: verdict['splash_first_seen_s'] = round(t, 1)
            print('%6.1fs frame %03d splash=%s' % (t, n, seen), flush=True)
            if verdict['ssh_up_s'] is None:
                try: up = ssh(key, 'true')[0] == 0
                except subprocess.TimeoutExpired: up = False
                if up:
                    verdict['ssh_up_s'] = round(t, 1); guest_checks(key, out, verdict); break
        verdict['last_frame'] = 'frame-%03d.png' % n
    finally:
        q.terminate()
        try: q.wait(10)
        except subprocess.TimeoutExpired: q.kill()
    (out / 'verdict.json').write_text(json.dumps(verdict, indent=1))
    print(json.dumps(verdict, indent=1))
    ok = verdict['splash_first_seen_s'] is not None and all(verdict[k] for k in ('splash_pid_after_switch_root', 'fifo_present', 'status_changed', 'only_status_moved', 'ready_exits'))
    print('PASS' if ok else 'FAIL'); return 0 if ok else 1


if __name__ == '__main__':
    if len(sys.argv) < 4: sys.exit(__doc__)
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 240))
