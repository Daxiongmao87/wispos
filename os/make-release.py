#!/usr/bin/env python3
"""Make a WispOS update release: the files to upload to a GitHub Release, and the signed manifest the device's `wisp-update` believes (docs/WISPOS-UPDATE-DESIGN.md).

  os/make-release.py keygen [--private ~/.wispos-release/update-signing.pem] [--public os/steamos/update-key.pub]
        Make the ed25519 signing key pair. The PRIVATE key stays outside the repository (directory 0700, file 0600) and is never overwritten: losing it means no image can be
        updated by a new key without a re-flash, so an existing key is left alone and the command stops. The PUBLIC key is the one baked into every image.
  os/make-release.py build --kernel FILE --system FILE --base-url URL --out DIR [--build-id YYYYMMDD.N] [--version X.Y.Z]
                           [--key PRIVATE.pem] [--requires X.Y.Z] [--major] [--hw-device H700] [--allow-http]
                           [--tree SYSTEM_TREE] [--extlinux FILE] [--allow-gaps]
        Writes DIR/KERNEL, DIR/SYSTEM, DIR/manifest.json, DIR/manifest.json.sig and DIR/RELEASE.txt. BASE_URL is where the release's assets will be (for GitHub:
        https://github.com/OWNER/REPO/releases/download/TAG); the manifest names KERNEL and SYSTEM there, and the device finds the manifest itself at the stable
        https://github.com/OWNER/REPO/releases/latest/download/manifest.json. Nothing is uploaded: publishing is the operator's act.
THE RELEASE GATE (operator, 2026-10-11): before anything is written, the SYSTEM being released (--tree, default the work tree's rootfs/tree) is checked with check_release.py --strict;
any gap against the release profile (SSH on by default, ROCKNIX leftovers in the boot graph, no update source, ...) refuses the release. --allow-gaps releases anyway and says so in RELEASE.txt.
--version and --build-id default to manifest/lockfile.json (the authority; stage_rootfs.py stamps the same two values into the SYSTEM it builds). Whatever they are, THE SYSTEM
BEING RELEASED MUST SAY THE SAME: its own /etc/os-release (read from the squashfs) has to carry that BUILD_ID, WISPOS_VERSION and HW_DEVICE, or the release is refused. A device
that installed a system which names an older build than its manifest would still believe it was out of date and offer the same update again for ever.
KERNEL is the file the image carries as /KERNEL (out/kernel/Image: the kernel with the initramfs inside it) and SYSTEM the squashfs (out/SYSTEM). The two always go together
(the modules in SYSTEM must match the kernel).
The signature is over the exact bytes of manifest.json (openssl pkeyutl -sign -rawin, base64 in manifest.json.sig). The build refuses a build id or version that cannot compare,
checks the signature back with the public key derived from the private one, and warns when that public key is not the one the images carry (os/steamos/update-key.pub):
a release signed by another key would be rejected by every device."""
import argparse, base64, hashlib, json, os, re, shutil, stat, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import stage_rootfs
WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))
DEFAULT_PRIVATE = Path.home() / '.wispos-release' / 'update-signing.pem'
BAKED_PUBLIC = Path(os.environ.get('WISP_BAKED_PUBLIC', HERE / 'steamos' / 'update-key.pub'))      # the override is for tests


def die(msg, code=2): print('make-release: ' + msg, file=sys.stderr); sys.exit(code)


def openssl(*args, data=None):
    r = subprocess.run(['openssl', *args], capture_output=True, input=data)
    if r.returncode != 0: die('openssl %s failed: %s' % (args[0], r.stderr.decode(errors='replace').strip()), 1)
    return r.stdout


def public_of(private):
    return openssl('pkey', '-in', str(private), '-pubout').decode()


def keygen(a):
    priv, pub = Path(a.private).expanduser(), Path(a.public)
    if priv.exists(): die(f'{priv} already exists: not overwriting a signing key (move it away first if you really mean to replace it)')
    priv.parent.mkdir(parents=True, exist_ok=True); os.chmod(priv.parent, 0o700)
    fd = os.open(priv, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as f: f.write(openssl('genpkey', '-algorithm', 'ED25519'))
    pub.parent.mkdir(parents=True, exist_ok=True); pub.write_text(public_of(priv))
    print(f'private key: {priv} (mode 0600, outside the repository: back it up somewhere safe)\npublic key:  {pub} (commit this; every image trusts it)')


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''): h.update(b)
    return h.hexdigest()


def release_gaps(a):
    """The DIFFERS rows of the release-readiness report for the SYSTEM tree being released."""
    import check_release
    tree = Path(a.tree or WORK / 'rootfs/tree'); ext = Path(a.extlinux or WORK / 'image/boot/extlinux/extlinux.conf')
    if not (tree / 'etc').is_dir(): die(f'cannot run the release gate: {tree} is not an unpacked SYSTEM tree (use --tree)')
    return [f"{r['what']}: now {r['current']}" for r in check_release.rows(tree, ext.read_text() if ext.is_file() else None) if r['status'] == 'DIFFERS']


def system_identity(system):
    """/etc/os-release of the squashfs being released, as a dict (unsquashfs reads one file out of the image; nothing is unpacked)."""
    r = subprocess.run(['unsquashfs', '-q', '-cat', str(system), 'etc/os-release'], capture_output=True, text=True)
    if r.returncode != 0 or '=' not in r.stdout: die(f'cannot read etc/os-release from {system}: {r.stderr.strip() or "it is not a SYSTEM image"}')
    return {k: v.strip('"\'') for k, v in (line.split('=', 1) for line in r.stdout.splitlines() if '=' in line)}


def release_accepted(a):
    """The ACCEPTED rows of the release-readiness report: gaps kept on purpose, with their reasons (printed in RELEASE.txt, so the person publishing the release sees them)."""
    import check_release
    tree = Path(a.tree or WORK / 'rootfs/tree'); ext = Path(a.extlinux or WORK / 'image/boot/extlinux/extlinux.conf')
    return [f"{r['what']}: {r['current']}" for r in check_release.rows(tree, ext.read_text() if ext.is_file() else None) if r['status'] == 'ACCEPTED']


def build(a):
    lock = stage_rootfs.lockfile()
    a.version = a.version or lock['version']; a.build_id = a.build_id or lock.get('build_id')
    if not a.build_id: die('no --build-id given and manifest/lockfile.json has none')
    if not re.fullmatch(r'\d{8}\.\d+', a.build_id): die(f'--build-id must look like 20261012.1, not {a.build_id!r}')
    if not re.fullmatch(r'\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?', a.version): die(f'--version must look like 0.1.0, not {a.version!r}')
    if a.requires and not re.fullmatch(r'\d+\.\d+\.\d+', a.requires): die('--requires must look like 0.1.0')
    base = a.base_url.rstrip('/')
    if not (base.startswith('https://') or (a.allow_http and base.startswith('http://'))): die('--base-url must be https (--allow-http is for a local test server only)')
    key = Path(a.key).expanduser()
    if not key.is_file(): die(f'no signing key at {key}: run `make-release.py keygen` first')
    if stat.S_IMODE(key.stat().st_mode) & 0o077: die(f'{key} is readable by others: chmod 600 it')
    for name, p in (('--kernel', a.kernel), ('--system', a.system)):
        if not Path(p).is_file() or Path(p).stat().st_size == 0: die(f'{name} {p} is missing or empty')
    ident = system_identity(a.system)
    for field, want, option in (('BUILD_ID', a.build_id, '--build-id'), ('WISPOS_VERSION', a.version, '--version'), ('HW_DEVICE', a.hw_device, '--hw-device')):
        if ident.get(field) != want:
            die(f'the SYSTEM image says {field}={ident.get(field)!r} but {option} is {want!r}: a device that installed it would still think it was out of date and offer the same update again '
                '(rebuild the SYSTEM with the right manifest/lockfile.json, or fix the option)')
    gaps = release_gaps(a)
    if gaps and not a.allow_gaps:
        die('the release gate refuses this SYSTEM (os/check_release.py): ' + '; '.join(gaps) + '\n  fix them, or pass --allow-gaps to release anyway (RELEASE.txt will say so)')
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()): die(f'{out} is not empty: a release directory is made once')
    files = {}
    for name, src in (('KERNEL', a.kernel), ('SYSTEM', a.system)):
        shutil.copyfile(src, out / name)
        files[name] = {'url': f'{base}/{name}', 'size': (out / name).stat().st_size, 'sha256': sha256_of(out / name)}
    manifest = {'format': 1, 'variant': 'stable', 'build_id': a.build_id, 'version': a.version, 'requires': a.requires or '', 'update_type': 1 if a.major else 0,
                'hw_device': a.hw_device, 'files': files}
    raw = (json.dumps(manifest, indent=1, sort_keys=True) + '\n').encode()
    (out / 'manifest.json').write_bytes(raw)
    with tempfile.TemporaryDirectory(prefix='wisp-release-', dir=os.environ.get('TMPDIR')) as d:
        Path(d, 'm').write_bytes(raw)
        sig = openssl('pkeyutl', '-sign', '-rawin', '-in', f'{d}/m', '-inkey', str(key))
        Path(d, 's').write_bytes(sig); Path(d, 'pub').write_text(public_of(key))
        openssl('pkeyutl', '-verify', '-rawin', '-in', f'{d}/m', '-sigfile', f'{d}/s', '-pubin', '-inkey', f'{d}/pub')      # the signature must verify with the key it claims
        (out / 'manifest.json.sig').write_bytes(base64.b64encode(sig) + b'\n')
        mine = Path(d, 'pub').read_text().strip()
    baked = BAKED_PUBLIC.read_text().strip() if BAKED_PUBLIC.exists() else None
    warn = '' if baked == mine else ('\nWARNING: this key is NOT the public key baked into the images (%s): every device would reject this release.' % (BAKED_PUBLIC if baked else 'none is committed yet'))
    gap_note = ('\nWARNING: released with --allow-gaps; the release gate reported: ' + '; '.join(gaps) + '\n') if gaps else ''
    accepted = release_accepted(a)
    if accepted: gap_note += '\nGaps accepted for this release (explained; each is on the list to close after a device session):\n' + ''.join(f'  - {x}\n' for x in accepted)
    (out / 'RELEASE.txt').write_text(f'''WispOS release {a.version} (build {a.build_id}), signed update.
Upload these four files as the assets of one GitHub Release (tag of your choice; BASE_URL in the manifest is {base}):
  KERNEL ({files['KERNEL']['size']} bytes)  SYSTEM ({files['SYSTEM']['size']} bytes)  manifest.json  manifest.json.sig
Devices find the manifest at .../releases/latest/download/manifest.json, so this must be the LATEST release (not a pre-release or draft).{warn}{gap_note}
''')
    print((out / 'RELEASE.txt').read_text())


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0]); sub = ap.add_subparsers(dest='cmd', required=True)
    k = sub.add_parser('keygen'); k.add_argument('--private', default=str(DEFAULT_PRIVATE)); k.add_argument('--public', default=str(BAKED_PUBLIC)); k.set_defaults(fn=keygen)
    b = sub.add_parser('build')
    for opt in ('--kernel', '--system', '--base-url', '--out'): b.add_argument(opt, required=True)
    b.add_argument('--build-id', default=''); b.add_argument('--version', default='')
    b.add_argument('--key', default=str(DEFAULT_PRIVATE)); b.add_argument('--requires', default=''); b.add_argument('--hw-device', default='H700')
    b.add_argument('--major', action='store_true'); b.add_argument('--allow-http', action='store_true'); b.add_argument('--tree'); b.add_argument('--extlinux'); b.add_argument('--allow-gaps', action='store_true'); b.set_defaults(fn=build)
    a = ap.parse_args(argv); a.fn(a); return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
