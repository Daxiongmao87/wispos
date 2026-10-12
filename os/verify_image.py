#!/usr/bin/env python3
"""Check a built WispOS card image against everything that can be verified without booting it.

    verify_image.py IMAGE WORKDIR

Reads the image as a card would be read: partition table, the boot ROM's SPL checksum, the FAT
volume (fsck, then every file extracted and compared with the build output), the extlinux
configuration against the labels actually on the volumes, and the ext4 volume (fsck, label,
first-boot marker). Prints one PASS/FAIL line per check and exits non-zero on any FAIL. It cannot
prove the handheld boots; it proves the image is what the build scripts say it is, and that every
reference a boot makes (labels, paths, file names) points at something that exists.
"""
import hashlib, json, os, re, shutil, struct, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
results = []


def check(name, ok, detail=''):
    results.append(ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  ' + str(detail)) if detail and not ok else ''}")
    return ok


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while chunk := f.read(1 << 22):
            h.update(chunk)
    return h.hexdigest()


def partitions(mbr):
    rows = []
    for i in range(4):
        e = mbr[446 + 16 * i: 462 + 16 * i]
        boot, ptype, start, size = e[0], e[4], struct.unpack_from('<I', e, 8)[0], struct.unpack_from('<I', e, 12)[0]
        if ptype:
            rows.append({'boot': boot == 0x80, 'type': ptype, 'start': start * 512, 'size': size * 512})
    return rows


def spl_checksum_ok(blob):
    """Allwinner boot ROM check: sum of all 32-bit words, with the checksum field read as the stamp."""
    if blob[4:12] != b'eGON.BT0':
        return False, 'no eGON.BT0 magic'
    length = struct.unpack_from('<I', blob, 16)[0]
    if length % 4 or length > len(blob):
        return False, f'bad length {length}'
    words = list(struct.unpack_from(f'<{length // 4}I', blob, 0))
    stored = words[3]
    words[3] = 0x5F0A6C39
    return sum(words) & 0xFFFFFFFF == stored, f'length {length}'


def carve(image, offset, size, dest):
    with open(image, 'rb') as src, open(dest, 'wb') as out:
        src.seek(offset)
        left = size
        while left:
            chunk = src.read(min(left, 1 << 22))
            out.write(chunk)
            left -= len(chunk)


def main(image, work):
    image, work = Path(image), Path(work)
    out = work / 'out'
    tmp = Path(tempfile.mkdtemp(prefix='verify-', dir=work / 'tmp'))
    lock = json.loads((HERE.parent / 'steam/rocknix-platform.lock.json').read_text())['files']
    with open(image, 'rb') as f:
        mbr = f.read(512)
    check('MBR signature 0x55AA', mbr[510:512] == b'\x55\xaa')
    check('disk id 0x57495350', struct.unpack_from('<I', mbr, 440)[0] == 0x57495350)
    parts = partitions(mbr)
    check('two partitions: bootable FAT32 (0x0c) then ext4 (0x83)',
          len(parts) == 2 and parts[0]['boot'] and parts[0]['type'] == 0x0C and parts[1]['type'] == 0x83, parts)
    check('BOOT starts at 16 MiB (the layout the reference image and U-Boot expect)', parts[0]['start'] == 16 << 20)
    check('STORAGE directly behind BOOT, nothing overlaps',
          parts[1]['start'] == parts[0]['start'] + parts[0]['size'] and parts[1]['start'] + parts[1]['size'] <= image.stat().st_size)

    uboot = (out / 'u-boot-sunxi-with-spl.bin').read_bytes()
    with open(image, 'rb') as f:
        f.seek(8192); blob = f.read(len(uboot))
    check('U-Boot written at byte 8192 and equal to the build output', blob == uboot)
    ok, detail = spl_checksum_ok(blob)
    check('SPL passes the boot ROM checksum', ok, detail)
    check('U-Boot ends before the first partition', 8192 + len(uboot) < parts[0]['start'])

    boot_img = tmp / 'boot.fat'
    carve(image, parts[0]['start'], parts[0]['size'], boot_img)
    fsck = subprocess.run(['fsck.fat', '-n', str(boot_img)], capture_output=True, text=True)
    check('BOOT passes fsck.fat', fsck.returncode == 0, fsck.stdout[-400:] + fsck.stderr[-200:])
    sector = boot_img.read_bytes()[:512]
    check('BOOT label is ROCKNIX (the initramfs boot=LABEL=)', sector[71:82] == b'ROCKNIX    ', sector[71:82])
    extract = tmp / 'boot'
    subprocess.run(['7z', 'x', '-y', f'-o{extract}', str(boot_img)], check=True, capture_output=True)
    env = os.environ.get; dev = env('WISP_DEV') == '1'
    default_system = out / ('variants/SYSTEM-dev' if dev else 'SYSTEM')
    expected = {'KERNEL': Path(env('WISP_KERNEL_IMAGE', out / 'kernel/Image')), 'SYSTEM': Path(env('WISP_SYSTEM', default_system)),
                'SYSTEM.md5': Path(env('WISP_SYSTEM_MD5', str(default_system) + '.md5')),
                'dtb.img': out / 'kernel/dtbs/sun50i-h700-anbernic-rg34xx-sp-v2-panel.dtb',
                'overlays/sun50i-h700-anbernic-rg35xx-2024-ddr3.dtbo': out / 'kernel/overlays/sun50i-h700-anbernic-rg35xx-2024-ddr3.dtbo'}
    for name, source in expected.items():
        got = extract / name
        check(f'BOOT/{name} equals its build output', got.is_file() and sha(got) == sha(source))
    kmd5 = (extract / 'KERNEL.md5').read_text().split()
    check('KERNEL.md5 matches KERNEL (the format initramfs checks: "<md5>  target/KERNEL")',
          kmd5 == [hashlib.md5((extract / 'KERNEL').read_bytes()).hexdigest(), 'target/KERNEL'])
    smd5 = (extract / 'SYSTEM.md5').read_text().split()
    check('SYSTEM.md5 matches SYSTEM', smd5 == [hashlib.md5((extract / 'SYSTEM').read_bytes()).hexdigest(), 'target/SYSTEM'])
    check('dtb.img is the dtb ROCKNIX booted on this device (hash from the lock file)', sha(extract / 'dtb.img') == lock['/dtb.img']['sha256'])
    check('DDR3 overlay equals the one ROCKNIX booted with (lock file)',
          sha(extract / 'overlays/sun50i-h700-anbernic-rg35xx-2024-ddr3.dtbo') == lock['/overlays/sun50i-h700-anbernic-rg35xx-2024-ddr3.dtbo']['sha256'])

    shipped = sorted(p.name for p in (extract / 'wispos').iterdir())
    if dev:
        check('dev image is marked as one and says so on the boot partition', 'DEV-BUILD.txt' in shipped, shipped)
    else:
        check('release image carries no keys, credentials, dev config or dev marker on the boot partition', shipped == ['README.txt', 'wifi.conf.example'], shipped)
    conf = (extract / 'extlinux/extlinux.conf').read_text()
    labels = re.findall(r'^LABEL (\S+)\n((?:[ \t]+.*\n)+)', conf, re.M)
    by_label = {name: dict(re.findall(r'^[ \t]+(\w+)[ \t]+(.*)$', body, re.M)) for name, body in labels}
    default = (extract / 'extlinux/extlinux.conf').read_bytes().split(b'\n', 1)[0]
    check('extlinux.conf starts with exactly "DEFAULT a" (the first line the update scheme flips in place) and has the labels a and b',
          default == b'DEFAULT a' and sorted(by_label) == ['a', 'b'], (default, sorted(by_label)))
    ok_slots = sorted(by_label) == ['a', 'b'] and all(by_label[x].get('APPEND', '').split()[-1:] == ['wisp.slot=' + x] for x in 'ab')
    check('each label carries its own slot (wisp.slot=a|b); slot a is /KERNEL, slot b is /wispos/b/KERNEL; both share the device tree and overlay',
          ok_slots and by_label['a'].get('LINUX') == '/KERNEL' and by_label['b'].get('LINUX') == '/wispos/b/KERNEL'
          and all(by_label['a'].get(k) == by_label['b'].get(k) for k in ('FDT', 'FDTOVERLAYS')), by_label)
    check('everything the DEFAULT label names exists (slot b is empty until the first update; the shared device tree and overlay are there)',
          'a' in by_label and all((extract / by_label['a'].get(k, '/nonexistent').lstrip('/')).is_file() for k in ('LINUX', 'FDT', 'FDTOVERLAYS')), by_label.get('a'))
    check('slot b and the update directory are absent on the shipped card (an update creates them)', not (extract / 'wispos/b').exists() and not (extract / 'wispos/update').exists())
    strip = lambda d: {k: v.replace('wisp.slot=a', '').replace('wisp.slot=b', '') for k, v in d.items() if k != 'LINUX'}
    check('the two labels differ in nothing but the kernel path and the slot', ok_slots and strip(by_label['a']) == strip(by_label['b']))
    rb = (extract / 'extlinux/rollback.conf'); rb_text = rb.read_text() if rb.is_file() else ''
    check('extlinux/rollback.conf exists, starts with "DEFAULT a" and is otherwise identical to extlinux.conf (the bootloader starts it after two starts of a new kernel that never reached its initramfs)',
          rb.is_file() and rb_text.split('\n', 1)[0] == 'DEFAULT a' and rb_text.split('\n', 1)[1:] == conf.split('\n', 1)[1:], rb_text[:120])
    check('the kernel restarts itself after a panic (panic=10), so a version on trial that panics is counted and rolled back without a finger on Power',
          len(by_label) == 2 and all('panic=10' in v.get('APPEND', '').split() for v in by_label.values()), {k: v.get('APPEND') for k, v in by_label.items()})
    check('extlinux boot=/disk= labels are the ones on the volumes',
          len(by_label) == 2 and all('boot=LABEL=ROCKNIX' in v.get('APPEND', '') and 'disk=LABEL=STORAGE' in v.get('APPEND', '') for v in by_label.values()))

    kernel_head = (extract / 'KERNEL').read_bytes()[:64]
    check('KERNEL is an arm64 Image (ARMd magic, little-endian, 4K pages)', kernel_head[56:60] == b'ARMd' and (struct.unpack_from('<Q', kernel_head, 24)[0] & 0b0111) == 0b0010)

    storage = tmp / 'storage.ext4'
    carve(image, parts[1]['start'], parts[1]['size'], storage)
    e2 = subprocess.run(['/usr/sbin/e2fsck', '-fn', str(storage)], capture_output=True, text=True)
    check('STORAGE passes e2fsck', e2.returncode == 0, e2.stdout[-300:])
    info = subprocess.run(['/usr/sbin/dumpe2fs', '-h', str(storage)], capture_output=True, text=True).stdout
    check('STORAGE label is STORAGE (the initramfs disk=LABEL=)', re.search(r'volume name:\s+STORAGE\b', info) is not None)
    listing = subprocess.run(['/usr/sbin/debugfs', '-R', 'ls -l /', str(storage)], capture_output=True, text=True).stdout
    check('STORAGE carries .please_resize_me and nothing else of ours', '.please_resize_me' in listing)

    # The first boot grows STORAGE to the card (os/rootfs's fs-resize: e2fsck -f -p, resize2fs, tune2fs -U random). mke2fs only
    # reserves room for 1024x growth by default; 32 MiB -> a 58 GB card needs ~1800x and resize2fs then corrupts the volume
    # (the next boot's fsck stops with "Filesystem corruption has been detected!"). Replay that sequence on a sparse copy.
    for label, size in (('58 GiB card', 58 << 30), ('2 TiB card, the microSD maximum', 2 << 40)):
        grown = tmp / 'grown.ext4'
        subprocess.run(['cp', '--sparse=always', str(storage), str(grown)], check=True); os.truncate(grown, size)
        steps = [['/usr/sbin/e2fsck', '-f', '-p', str(grown)], ['/usr/sbin/resize2fs', str(grown)], ['/usr/sbin/tune2fs', str(grown), '-U', 'random']]
        ran = [subprocess.run(c, capture_output=True, text=True) for c in steps]
        again = subprocess.run(['/usr/sbin/e2fsck', '-fn', str(grown)], capture_output=True, text=True)
        check(f'STORAGE survives the first-boot grow to a {label} and fscks clean',
              all(r.returncode == 0 for r in ran) and again.returncode == 0, (again.stdout + ''.join(r.stderr for r in ran))[-300:])
        grown.unlink()

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n{sum(results)}/{len(results)} checks passed")
    return all(results)


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    sys.exit(0 if main(*sys.argv[1:]) else 1)
