#!/usr/bin/env python3
"""The two-slot update scheme end to end in QEMU, with the REAL initramfs step, the real busybox, the real vfat driver, the real wisp-update and (default) the REAL U-Boot code.

usage: qemu_update_test.py KERNEL CARD OUT_DIR KEY [scenario ...]        scenarios: update rollback nosystemd cut-staged cut-pending cut-switched badkernel   (default: all, in parallel)
  KERNEL  the QEMU test kernel (virtio disk and net) with the PATCHED initramfs embedded (os/boot/build-qemu-kernel.sh after os/boot/make-initramfs.sh + build-kernel.sh)
  CARD    a WispOS test card built by os/build-image.sh with WISP_DEV=1 WISP_QEMU_CARD=1 and the QEMU kernel as its KERNEL (os/qemu-update-test.sh makes it and the throwaway key)
  The card is copied (reflink) and never modified. Every scenario writes serial logs and a verdict into OUT_DIR/<scenario>/.
  Environment: WISP_QEMU_UBOOT=path of u-boot.bin from os/boot/build-uboot-qemu.sh (real U-Boot, see below); unset or `none`: the stand-in only, with KERNEL started by QEMU itself.

WHO CHOOSES THE SLOT. With WISP_QEMU_UBOOT, QEMU's firmware IS U-Boot (built from the handheld bootloader's patched source tree, os/boot/build-uboot-qemu.sh): it reads the card's
extlinux.conf, starts the label named by its first line and, when that label cannot be started (kernel missing, empty, or not a Linux image), the other one, exactly the code the
H700 build runs (boot/pxe_utils.c). Without it, a stand-in reads the card's FAT volume the way U-Boot would (a small read-only FAT32 reader of its own, so what it sees is what is
on the "media", not what the guest cached) and starts QEMU with the test kernel and that label's command line. Either way the stand-in PREDICTS the slot, and with real U-Boot every
boot checks the prediction against the slot the kernel reports on its command line, so the model is itself tested. -no-reboot makes a guest restart end QEMU, and the next "power-on"
asks again, so a switch made by the guest (or by the initramfs, in a rollback) takes effect exactly as it would on the device. A POWER CUT is SIGKILL of QEMU: what the guest had not
written to the virtual disk is lost, what it had is on the image, and the next start reads the image.

SCENARIOS
  update      a real-size update (the card's own KERNEL and SYSTEM as the payload): apply in the running slot a, the card says DEFAULT b with a trial marker, restart -> slot b boots from
              /flash/wispos/b/SYSTEM, its initramfs counts the start, `wisp-update confirm` ends the trial, the next restart has no trial.
  rollback    a payload whose system cannot start (random bytes): three starts of slot b, each unconfirmed and each ending in a RESTART BY ITSELF (the reference init would power off), then the
              initramfs goes back to slot a (DEFAULT a, FAILED-<id>, no marker) and slot a starts; the build is not offered again.
  nosystemd   the same with a system that mounts but has no init: the reference init would stop in a debug shell ("Could not find systemd!"); on trial it restarts instead.
  cut-staged / cut-pending / cut-switched    the commit sequence of `wisp-update apply` ended by a power cut right after that step: the state on the card is the expected one, the card
              starts, and (cut-pending) the next boot of slot a removes the stale marker.
  badkernel   (real U-Boot only) a payload whose KERNEL is not a Linux image at all: after the switch U-Boot cannot start slot b and starts slot a by itself; slot a's initramfs sees that
              the version on trial never started (the letter says b, slot a runs): FAILED-<id> is left, the letter goes back to a, the marker is gone."""
import hashlib, http.server, json, os, shutil, signal, struct, subprocess, sys, tempfile, threading, time, base64
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
BOOT_OFFSET = 16 << 20
BUILD_ID, VERSION = '20991231.9', '9.9.9'
APPLY_ENV = 'WISP_UPDATE_CONF=/tmp/u.conf WISP_UPDATE_PUBKEY=/tmp/u.pub WISP_UPDATE_ALLOW_HTTP=1'


# ---- the card as the "media" shows it: a read-only FAT32 reader ------------------------------------------------------------------------------------------------------------------
class Fat32:
    def __init__(self, path, offset=BOOT_OFFSET):
        self.f = open(path, 'rb'); self.off = offset
        bs = self.read(0, 512)
        self.bps, self.spc, self.rsv, self.nfat = struct.unpack_from('<H', bs, 11)[0], bs[13], struct.unpack_from('<H', bs, 14)[0], bs[16]
        self.fatsz, self.root = struct.unpack_from('<I', bs, 36)[0], struct.unpack_from('<I', bs, 44)[0]
        self.fat_off = self.rsv * self.bps; self.data_off = (self.rsv + self.nfat * self.fatsz) * self.bps; self.csize = self.spc * self.bps

    def close(self): self.f.close()
    def read(self, pos, n): self.f.seek(self.off + pos); return self.f.read(n)
    def next(self, c): return struct.unpack('<I', self.read(self.fat_off + 4 * c, 4))[0] & 0x0FFFFFFF

    def chain(self, c):
        seen = 0
        while 2 <= c < 0x0FFFFFF8:
            yield c; c = self.next(c); seen += 1
            if seen > 10_000_000: raise ValueError('FAT chain loops')

    def entries(self, c):
        """(name, is_dir, start cluster, size) of a directory, long names assembled; deleted and volume entries skipped."""
        data = b''.join(self.read(self.data_off + (k - 2) * self.csize, self.csize) for k in self.chain(c)); lfn = []
        for i in range(0, len(data), 32):
            e = data[i:i + 32]
            if e[0] == 0: break
            if e[0] == 0xE5: lfn = []; continue
            if e[11] == 0x0F:
                part = e[1:11] + e[14:26] + e[28:32]; lfn.insert(0, part.decode('utf-16-le').split('\x00')[0].split('￿')[0]); continue
            if e[11] & 0x08: lfn = []; continue
            name = ''.join(lfn) if lfn else (e[0:8].decode('latin-1').rstrip() + ('.' + e[8:11].decode('latin-1').rstrip() if e[8:11].strip() else ''))
            if not lfn:
                base, ext = e[0:8].decode('latin-1').rstrip(), e[8:11].decode('latin-1').rstrip()
                if e[12] & 0x08: base = base.lower()
                if e[12] & 0x10: ext = ext.lower()
                name = base + ('.' + ext if ext else '')
            start = (struct.unpack_from('<H', e, 20)[0] << 16) | struct.unpack_from('<H', e, 26)[0]
            yield name, bool(e[11] & 0x10), start, struct.unpack_from('<I', e, 28)[0]; lfn = []

    def lookup(self, path):
        c, parts = self.root, [p for p in path.split('/') if p]
        for n, part in enumerate(parts):
            for name, is_dir, start, size in self.entries(c):
                if name.lower() == part.lower():
                    if n == len(parts) - 1: return {'dir': is_dir, 'start': start, 'size': size}
                    if not is_dir: return None
                    c = start; break
            else: return None
        return {'dir': True, 'start': c, 'size': 0}

    def read_file(self, path):
        f = self.lookup(path)
        if f is None or f['dir']: return None
        out = bytearray()
        for k in self.chain(f['start']): out += self.read(self.data_off + (k - 2) * self.csize, self.csize)
        return bytes(out[:f['size']])

    def sha(self, path):
        f = self.lookup(path)
        if f is None or f['dir']: return None
        h = hashlib.sha256(); left = f['size']
        for k in self.chain(f['start']):
            take = min(self.csize, left)
            if take <= 0: break
            h.update(self.read(self.data_off + (k - 2) * self.csize, take)); left -= take
        return h.hexdigest()

    def listdir(self, path):
        f = self.lookup(path)
        return None if f is None or not f['dir'] else [n for n, _, _, _ in self.entries(f['start']) if n not in ('.', '..')]


class Card:
    def __init__(self, path): self.path = Path(path)
    def fat(self): return Fat32(self.path)

    def state(self):
        """What the media says now: the letter, the trial marker, the failed builds, and which slots are complete."""
        fat = self.fat()
        try:
            conf = (fat.read_file('extlinux/extlinux.conf') or b'').decode(errors='replace'); first = conf.split('\n', 1)[0]
            raw = fat.read_file('wispos/update/PENDING')
            pend = dict(l.split('=', 1) for l in raw.decode().splitlines() if '=' in l) if raw is not None else None
            sizes = {p: (fat.lookup(p) or {'size': None})['size'] for p in ('KERNEL', 'SYSTEM', 'wispos/b/KERNEL', 'wispos/b/SYSTEM')}
            return {'first_line': first, 'pending': pend, 'failed': sorted(n for n in (fat.listdir('wispos/update') or []) if n.startswith('FAILED-')), 'sizes': sizes, 'conf': conf,
                    'parts': sorted(n for d in ('', 'wispos/b') for n in (fat.listdir(d) or []) if n.endswith('.part'))}
        finally: fat.close()

    def hashes(self, *paths):
        fat = self.fat()
        try: return {p: fat.sha(p) for p in paths}
        finally: fat.close()


def parse_extlinux(conf):
    default, labels, cur = None, {}, None
    for line in conf.splitlines():
        if line.startswith('DEFAULT '): default = line.split(None, 1)[1].strip(); continue
        if line.startswith('LABEL '): cur = line.split(None, 1)[1].strip(); labels[cur] = {}; continue
        if cur and line[:1] in ' \t' and line.strip():
            k, _, v = line.strip().partition(' '); labels[cur][k] = v.strip()
    return default, labels


def uboot_pick(card):
    """(label, its settings) U-Boot would start, or None: the DEFAULT label if its kernel and device tree can be loaded, else the other labels in file order."""
    fat = card.fat()
    try:
        default, labels = parse_extlinux((fat.read_file('extlinux/extlinux.conf') or b'').decode(errors='replace'))
        for name in ([default] if default in labels else []) + [n for n in labels if n != default]:
            lab = labels[name]; k = fat.lookup(lab.get('LINUX', '')); d = fat.lookup(lab['FDT']) if 'FDT' in lab else {'size': 1}        # no FDT line (the QEMU card): U-Boot passes its own tree on
            if k and not k['dir'] and k['size'] > 0 and d and d['size'] > 0: return name, lab
        return None
    finally: fat.close()


def qemu_append(lab):
    keep = [t for t in lab['APPEND'].split() if not t.startswith(('console=', 'systemd.debug_shell=', 'loglevel=')) and t != 'quiet']
    return ' '.join(keep + ['console=ttyAMA0', 'loglevel=6'])


# ---- one machine ------------------------------------------------------------------------------------------------------------------------------------------------------------------
UBOOT = os.environ.get('WISP_QEMU_UBOOT', '')
UBOOT = UBOOT if UBOOT and UBOOT != 'none' else ''


class Guest:
    def __init__(self, name, card, kernel, key, outdir, port):
        self.name, self.card, self.kernel, self.key, self.out, self.port = name, Card(card), kernel, key, Path(outdir), port
        self.out.mkdir(parents=True, exist_ok=True); self.n = 0; self.q = None; self.serial = None

    def power_on(self):
        """The firmware, then the system. Returns the slot the stand-in PREDICTS U-Boot starts (with real U-Boot the real choice is read from the kernel's command line: boot())."""
        pick = uboot_pick(self.card)
        if pick is None and not UBOOT: raise RuntimeError('%s: U-Boot would find no kernel to load' % self.name)
        label, lab = pick if pick else (None, None); self.n += 1; self.label = label
        self.serial = self.out / ('boot%d.serial.log' % self.n); self.serial.write_bytes(b'')
        machine = ['qemu-system-aarch64', '-M', 'virt', '-cpu', 'cortex-a53', '-smp', '4', '-m', '1024', '-display', 'none', '-monitor', 'none', '-no-reboot', '-serial', 'file:%s' % self.serial]
        machine += ['-bios', UBOOT] if UBOOT else ['-kernel', str(self.kernel), '-append', qemu_append(lab)]
        self.q = subprocess.Popen(machine + ['-drive', 'file=%s,format=raw,if=none,id=d0' % self.card.path, '-device', 'virtio-blk-pci,drive=d0',
                                             '-netdev', 'user,id=n0,hostfwd=tcp:127.0.0.1:%d-:22' % self.port, '-device', 'virtio-net-pci,netdev=n0'],
                                  stdout=open(self.out / ('boot%d.qemu.out' % self.n), 'w'), stderr=subprocess.STDOUT)
        return label

    def booted_slot(self, timeout=180):
        """The slot the kernel reports on its command line (wisp.slot=), once it has printed it; None if the machine stopped or nothing came in time."""
        end = time.time() + timeout
        while time.time() < end:
            text = self.serial.read_text(errors='replace') if self.serial.exists() else ''
            for line in text.splitlines():
                if 'Kernel command line:' in line and 'wisp.slot=' in line: return line.split('wisp.slot=', 1)[1][:1]
            if not self.alive(): return None
            time.sleep(1)
        return None

    def boot(self, v, differs=False):
        """power_on, and with real U-Boot check that it started the slot the stand-in predicted. Returns the slot that really started. differs=True: the stand-in is EXPECTED to be wrong here
        (it only models a kernel that cannot be loaded, not one that is not a Linux image) and the check is that the real choice is the other slot."""
        predicted = self.power_on()
        if not UBOOT: return predicted
        real = self.booted_slot()
        if differs: v.check('real U-Boot started slot %s, not the %s the stand-in predicted (a kernel file that is not a Linux image)' % (real, predicted), real is not None and real != predicted, self.serial_text()[-500:])
        else: v.check('real U-Boot started slot %s%s' % (real, ' (the stand-in predicted %s)' % predicted if predicted else ' (the stand-in predicted that no kernel could be loaded)'), predicted is None or real == predicted, self.serial_text()[-500:])
        return real

    def alive(self): return self.q is not None and self.q.poll() is None

    def wait_exit(self, timeout):
        try: self.q.wait(timeout); return True
        except subprocess.TimeoutExpired: return False

    def cut_power(self):
        """SIGKILL: nothing the guest has not written to the virtual disk survives."""
        self.q.kill(); self.q.wait(30)

    def ssh(self, command, stdin=None, timeout=300):
        r = subprocess.run(['ssh', '-p', str(self.port), '-i', str(self.key), '-o', 'IdentitiesOnly=yes', '-o', 'PasswordAuthentication=no', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=no',
                            '-o', 'UserKnownHostsFile=/dev/null', '-o', 'LogLevel=ERROR', '-o', 'ConnectTimeout=3', 'root@127.0.0.1', command], input=stdin, capture_output=True, timeout=timeout)
        return r.returncode, r.stdout.decode(errors='replace').strip()

    def wait_up(self, timeout=400):
        end = time.time() + timeout
        while time.time() < end and self.alive():
            try:
                if self.ssh('true', timeout=10)[0] == 0: return True
            except subprocess.TimeoutExpired: pass
            time.sleep(3)
        return False

    def serial_text(self): return self.serial.read_text(errors='replace')

    def restart(self):
        """`reboot` in the guest, wait for QEMU to end (-no-reboot), and say whether it did."""
        self.ssh("sh -c 'sleep 1; systemctl reboot' >/dev/null 2>&1 &", timeout=20)
        return self.wait_exit(180)


# ---- the update server -------------------------------------------------------------------------------------------------------------------------------------------------------------
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        body = self.server.files.get(self.path)
        if body is None: self.send_error(404); return
        start, status = 0, 200
        import re
        m = re.match(r'bytes=(\d+)-', self.headers.get('Range') or '')
        if m: start, status = int(m.group(1)), 206
        data = memoryview(body)[start:]
        self.send_response(status); self.send_header('Content-Length', str(len(data)))
        if status == 206: self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, len(body) - 1, len(body)))
        self.end_headers()
        try:
            for i in range(0, len(data), 1 << 20): self.wfile.write(data[i:i + (1 << 20)])
        except (BrokenPipeError, ConnectionResetError): pass


class Release:
    """A signed manifest and its two files, served on the host's loopback (the guest reaches it as 10.0.2.2)."""
    def __init__(self, tmp, kernel, system):
        self.tmp = Path(tmp)
        subprocess.run(['openssl', 'genpkey', '-algorithm', 'ED25519', '-out', str(self.tmp / 'key.pem')], check=True, capture_output=True)
        subprocess.run(['openssl', 'pkey', '-in', str(self.tmp / 'key.pem'), '-pubout', '-out', str(self.tmp / 'pub.pem')], check=True, capture_output=True)
        self.srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler); self.srv.daemon_threads = True
        base = 'http://10.0.2.2:%d' % self.srv.server_address[1]
        manifest = {'format': 1, 'variant': 'stable', 'build_id': BUILD_ID, 'version': VERSION, 'requires': '', 'update_type': 0, 'hw_device': 'H700',
                    'files': {n: {'url': '%s/%s' % (base, n), 'size': len(b), 'sha256': hashlib.sha256(b).hexdigest()} for n, b in (('KERNEL', kernel), ('SYSTEM', system))}}
        raw = json.dumps(manifest).encode(); (self.tmp / 'm.json').write_bytes(raw)
        sig = subprocess.run(['openssl', 'pkeyutl', '-sign', '-rawin', '-in', str(self.tmp / 'm.json'), '-inkey', str(self.tmp / 'key.pem')], check=True, capture_output=True).stdout
        self.srv.files = {'/manifest.json': raw, '/manifest.json.sig': base64.b64encode(sig), '/KERNEL': kernel, '/SYSTEM': system}
        self.conf = json.dumps({'manifest_url': base + '/manifest.json'}).encode(); self.pub = (self.tmp / 'pub.pem').read_bytes()
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def install(self, guest):
        guest.ssh('cat > /tmp/u.conf', stdin=self.conf); guest.ssh('cat > /tmp/u.pub', stdin=self.pub)

    def close(self): self.srv.shutdown(); self.srv.server_close()


# ---- checks ------------------------------------------------------------------------------------------------------------------------------------------------------------------------
class Verdict:
    def __init__(self, name): self.name, self.rows = name, []; self.lock = threading.Lock()
    def check(self, what, ok, detail=''):
        with self.lock: self.rows.append((what, bool(ok), str(detail)[:600]))
        print('%s  [%s] %s%s' % ('PASS' if ok else 'FAIL', self.name, what, '' if ok else '   ' + str(detail)[:600]), flush=True); return ok
    @property
    def ok(self): return all(r[1] for r in self.rows)


def card_kernel(card):
    fat = Card(card).fat()
    try: return fat.read_file('KERNEL')
    finally: fat.close()


def clone_card(base, dest):
    subprocess.run(['cp', '--reflink=auto', str(base), str(dest)], check=True)


def prepare(kernel, card, work, key, port):
    """First boot of the fresh card (it grows STORAGE and restarts once), then a clean power-off: the base every scenario starts from."""
    out = work / 'prepare'; base = out / 'base.img'; out.mkdir(parents=True, exist_ok=True); clone_card(card, base)
    g = Guest('prepare', base, kernel, key, out, port)
    for _ in range(4):
        g.power_on()
        if g.wait_up(500): break
        if g.alive(): g.cut_power(); raise RuntimeError('prepare: the guest never answered; see %s' % g.serial)
    else: raise RuntimeError('prepare: no boot reached the network; see %s' % g.serial)
    g.ssh("sh -c 'sleep 1; systemctl poweroff' >/dev/null 2>&1 &", timeout=20); g.wait_exit(180)
    if g.alive(): g.cut_power()
    return base


def scenario_update(v, base, kernel, key, work, port):
    out = work / 'update'; card = out / 'card.img'; out.mkdir(parents=True, exist_ok=True); clone_card(base, card)
    g = Guest('update', card, kernel, key, out, port)
    before = Card(card).hashes('KERNEL', 'SYSTEM'); fat = Card(card).fat(); k, s = fat.read_file('KERNEL'), fat.read_file('SYSTEM'); fat.close()
    rel = Release(out, k, s)
    try:
        g.boot(v); v.check('slot a starts and answers', g.wait_up(400), g.serial_text()[-600:])
        v.check('the command line says slot a', 'wisp.slot=a' in g.ssh('cat /proc/cmdline')[1])
        rel.install(g)
        rc, o = g.ssh('%s /usr/lib/wispos/wisp-update apply %s' % (APPLY_ENV, BUILD_ID), timeout=900)
        v.check('apply stages 213 MB into slot b and ends with done', rc == 0 and o.splitlines()[-1] == 'done', o[-400:])
        st = Card(card).state(); p = st['pending'] or {}
        v.check('the card now starts slot b, with a trial marker for it', st['first_line'] == 'DEFAULT b' and p.get('slot') == 'b' and p.get('build_id') == BUILD_ID and p.get('tries') == '0', st)
        v.check('slot b holds the update; slot a is untouched', st['sizes']['wispos/b/KERNEL'] == len(k) and st['sizes']['wispos/b/SYSTEM'] == len(s) and Card(card).hashes('KERNEL', 'SYSTEM') == before, st['sizes'])
        v.check('only the letter changed in extlinux.conf', st['conf'].replace('DEFAULT b', 'DEFAULT a', 1) == (Card(base).state()['conf']))
        v.check('the guest restarts and U-Boot picks slot b', g.restart() and (uboot_pick(g.card) or ('?',))[0] == 'b')
        g.boot(v); v.check('slot b starts and answers', g.wait_up(400), g.serial_text()[-600:])
        v.check('the command line says slot b', 'wisp.slot=b' in g.ssh('cat /proc/cmdline')[1])
        v.check('the root is the squashfs file of slot b', '/flash/wispos/b/SYSTEM' in g.ssh('losetup -a')[1], g.ssh('losetup -a')[1])
        v.check('the initramfs counted the start (1 of 2) and the helper agrees', 'on trial (1 of 2)' in g.serial_text() and 'tries=1' in g.ssh('/usr/lib/wispos/wisp-update status')[1], g.ssh('/usr/lib/wispos/wisp-update status')[1])
        rc, o = g.ssh('/usr/lib/wispos/wisp-update confirm')
        v.check('confirm ends the trial', rc == 0 and o == 'confirmed ' + BUILD_ID and Card(card).state()['pending'] is None, o)
        v.check('the guest restarts again', g.restart())
        g.boot(v); v.check('slot b starts again, now with no trial', g.wait_up(400) and 'on trial' not in g.serial_text() and Card(card).state()['first_line'] == 'DEFAULT b', g.serial_text()[-400:])
        rc, o = g.ssh('/usr/lib/wispos/wisp-update status'); v.check('status: running b, default b, nothing pending', o.splitlines()[:2] == ['running b', 'default b'] and 'pending' not in o, o)
    finally:
        if g.alive(): g.cut_power()
        rel.close()


def tiny_squashfs(out):
    """A system image that MOUNTS but has no init (no usr/lib/systemd/systemd): what os/dev/test-release.sh --broken makes of the real one."""
    t = out / 'tiny-tree'; (t / 'etc').mkdir(parents=True, exist_ok=True); (t / 'etc/os-release').write_text('ID=nosystemd\n')
    subprocess.run(['mksquashfs', str(t), str(out / 'tiny.sqsh'), '-noappend', '-quiet', '-comp', 'gzip'], check=True, capture_output=True)
    return (out / 'tiny.sqsh').read_bytes()


def trial_failure(name, make_system, reason, why):
    """A version on trial whose start fails inside the initramfs: it restarts BY ITSELF each time (nobody touches the machine), is counted, and at the third start the card goes back."""
    def run(v, base, kernel, key, work, port): return scenario_rollback(v, base, kernel, key, work, port, name, make_system, reason, why)
    return run


def scenario_rollback(v, base, kernel, key, work, port, name='rollback', make_system=lambda out: os.urandom(700_000), reason='power off requested', why='cannot mount its system'):
    out = work / name; card = out / 'card.img'; out.mkdir(parents=True, exist_ok=True); clone_card(base, card)
    g = Guest(name, card, kernel, key, out, port); rel = Release(out, card_kernel(card), make_system(out))      # a kernel that starts, a system that cannot start
    try:
        g.boot(v); v.check('slot a starts and answers', g.wait_up(400), g.serial_text()[-600:]); rel.install(g)
        rc, o = g.ssh('%s /usr/lib/wispos/wisp-update apply %s' % (APPLY_ENV, BUILD_ID)); v.check('a (bad) update is staged and switched to', rc == 0 and Card(card).state()['first_line'] == 'DEFAULT b', o)
        v.check('the guest restarts', g.restart())
        for attempt in (1, 2):
            label = g.boot(v); done = g.wait_exit(240)
            v.check('start %d of the new version: it is slot b, it is counted (%d of 2), %s, and the machine RESTARTS BY ITSELF (reason said: %s)' % (attempt, attempt, why, reason),
                    label == 'b' and ('on trial (%d of 2)' % attempt) in g.serial_text() and done
                    and ('start %d of 2 on trial failed (%s' % (attempt, reason)) in g.serial_text() and 'restarting' in g.serial_text(), g.serial_text()[-700:])
            if not done: g.cut_power()
            v.check('  the card says tries=%d and still starts slot b' % attempt, (Card(card).state()['pending'] or {}).get('tries') == str(attempt) and Card(card).state()['first_line'] == 'DEFAULT b')
        label = g.boot(v); done = g.wait_exit(240); st = Card(card).state()
        v.check('start 3: the initramfs says the version never came up well, goes back and restarts', label == 'b' and 'never came up well' in g.serial_text() and 'going back to the previous version' in g.serial_text() and done, g.serial_text()[-600:])
        if not done: g.cut_power()
        v.check('the card: DEFAULT a, no trial marker, FAILED-%s left, slot a untouched' % BUILD_ID, st['first_line'] == 'DEFAULT a' and st['pending'] is None and st['failed'] == ['FAILED-' + BUILD_ID], st)
        label = g.boot(v); v.check('the previous version starts again (slot a)', label == 'a' and g.wait_up(400), g.serial_text()[-600:])
        rel.install(g)
        rc, o = g.ssh('/usr/lib/wispos/wisp-update status'); v.check('status names the failed build', 'failed ' + BUILD_ID in o and o.startswith('running a'), o)
        rc, o = g.ssh('%s /usr/lib/wispos/wisp-update check' % APPLY_ENV); v.check('the failed build is not offered again', rc == 0 and 'update' not in o, o)
        rc, o = g.ssh('%s /usr/lib/wispos/wisp-update apply %s' % (APPLY_ENV, BUILD_ID)); v.check('and cannot be applied by name', rc == 1 and 'UnknownUpdate' in o, o)
    finally:
        if g.alive(): g.cut_power()
        rel.close()


def scenario_cut(point):
    def run(v, base, kernel, key, work, port):
        out = work / ('cut-' + point); card = out / 'card.img'; out.mkdir(parents=True, exist_ok=True); clone_card(base, card)
        k = card_kernel(card); g = Guest('cut-' + point, card, kernel, key, out, port); rel = Release(out, k, os.urandom(700_000))
        try:
            g.boot(v); v.check('slot a starts and answers', g.wait_up(400), g.serial_text()[-600:]); rel.install(g)
            rc, o = g.ssh('WISP_UPDATE_STOP_AFTER=%s %s /usr/lib/wispos/wisp-update apply %s' % (point, APPLY_ENV, BUILD_ID))
            v.check('the helper stops right after the "%s" step (exit 99)' % point, rc == 99, (rc, o))
            g.cut_power(); st = Card(card).state(); p = st['pending']
            want = {'staged': ('DEFAULT a', None), 'pending': ('DEFAULT a', 'b'), 'switched': ('DEFAULT b', 'b')}[point]
            v.check('the media after the cut: %s, trial marker %s' % (want[0], 'for slot b' if want[1] else 'absent'), st['first_line'] == want[0] and (p or {}).get('slot') == want[1], st)
            v.check('slot b holds both files whole; no half-written file is left', st['sizes']['wispos/b/KERNEL'] == len(k) and st['sizes']['wispos/b/SYSTEM'] == 700_000 and st['parts'] == [], st['sizes'])
            pick = uboot_pick(g.card); v.check('U-Boot finds a kernel to load (slot %s)' % want[0][-1], pick is not None and pick[0] == want[0][-1], pick)
            if point == 'pending':
                g.boot(v); v.check('slot a starts', g.wait_up(400), g.serial_text()[-600:])
                v.check('its initramfs removed the stale marker', 'stale update marker for slot b' in g.serial_text() and Card(card).state()['pending'] is None, g.serial_text()[-500:])
                v.check('the helper can finish the update afterwards', g.ssh('cat > /tmp/u.conf', stdin=rel.conf)[0] == 0 and g.ssh('cat > /tmp/u.pub', stdin=rel.pub)[0] == 0
                        and g.ssh('%s /usr/lib/wispos/wisp-update apply %s' % (APPLY_ENV, BUILD_ID))[0] == 0 and Card(card).state()['first_line'] == 'DEFAULT b')
        finally:
            if g.alive(): g.cut_power()
            rel.close()
    return run


def scenario_badkernel(v, base, kernel, key, work, port):
    """The switch is made, but the new KERNEL is not a Linux image: U-Boot cannot start slot b and starts slot a by itself (boot/pxe_utils.c boot_unattempted_labels)."""
    out = work / 'badkernel'; card = out / 'card.img'; out.mkdir(parents=True, exist_ok=True); clone_card(base, card)
    if not UBOOT: v.check('needs real U-Boot (WISP_QEMU_UBOOT): the stand-in only models a kernel that cannot be LOADED', True); return
    g = Guest('badkernel', card, kernel, key, out, port); rel = Release(out, os.urandom(300_000), os.urandom(700_000))
    try:
        g.boot(v); v.check('slot a starts and answers', g.wait_up(400), g.serial_text()[-600:]); rel.install(g)
        rc, o = g.ssh('%s /usr/lib/wispos/wisp-update apply %s' % (APPLY_ENV, BUILD_ID)); v.check('the update is staged and switched to', rc == 0 and Card(card).state()['first_line'] == 'DEFAULT b', o)
        v.check('the guest restarts', g.restart())
        real = g.boot(v, differs=True); v.check('U-Boot tried slot b first (it fetched /wispos/b/KERNEL), could not start it and started slot a', real == 'a' and 'Retrieving file: /wispos/b/KERNEL' in g.serial_text(), g.serial_text()[-700:])
        v.check('slot a answers', g.wait_up(400))
        st = Card(card).state()
        v.check('its initramfs saw that the version on trial never started: FAILED-%s left, the letter back to a, the marker gone' % BUILD_ID, 'never started' in g.serial_text()
                and st['pending'] is None and st['first_line'] == 'DEFAULT a' and st['failed'] == ['FAILED-' + BUILD_ID], (g.serial_text()[-500:], st))
    finally:
        if g.alive(): g.cut_power()
        rel.close()


SCENARIOS = {'update': scenario_update, 'rollback': scenario_rollback, 'nosystemd': trial_failure('nosystemd', tiny_squashfs, 'final_check: Could not find systemd!', 'its system mounts but has no init'), 'cut-staged': scenario_cut('staged'), 'cut-pending': scenario_cut('pending'), 'cut-switched': scenario_cut('switched'), 'badkernel': scenario_badkernel}


def main(argv):
    if len(argv) < 4: sys.exit(__doc__)
    kernel, card, out, key = [Path(a).resolve() for a in argv[:4]]; names = argv[4:] or list(SCENARIOS)
    for n in names:
        if n not in SCENARIOS: sys.exit('unknown scenario %s' % n)
    out.mkdir(parents=True, exist_ok=True); port0 = int(os.environ.get('WISP_QEMU_PORT_BASE', '2240'))
    t0 = time.time(); base = prepare(kernel, card, out, key, port0)
    print('prepared the base card in %d s' % (time.time() - t0), flush=True)
    verdicts, threads = {}, []
    def go(i, n):
        v = verdicts[n] = Verdict(n)
        try: SCENARIOS[n](v, base, kernel, key, out, port0 + 1 + i)
        except Exception as e:
            import traceback; v.check('the scenario ran to its end', False, traceback.format_exc()[-800:])
    for i, n in enumerate(names):
        t = threading.Thread(target=go, args=(i, n)); t.start(); threads.append(t)
    for t in threads: t.join()
    (out / 'verdict.json').write_text(json.dumps({n: {'ok': v.ok, 'checks': v.rows} for n, v in verdicts.items()}, indent=1))
    ok = all(v.ok for v in verdicts.values())
    print('%s  (%d s)  %s' % ('PASS' if ok else 'FAIL', time.time() - t0, ', '.join('%s %s' % (n, 'ok' if v.ok else 'FAILED') for n, v in verdicts.items())))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
