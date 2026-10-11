#!/usr/bin/env python3
"""Pin/fetch/extract native Steam libraries without package installation.

Reads apt-cache metadata only; never apt update/install or target execution.
The lock and separate overlay manifest do not claim dpkg installation.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile
import urllib.request

INITIAL = 'libxcomposite1 libxdamage1 libxtst6 libatk1.0-0t64 libatk-bridge2.0-0t64 libatspi2.0-0t64 libcairo2 libcups2t64 libibus-1.0-5 libnm0 libnspr4 libnss3 libopenal1 libpango-1.0-0 libxkbcommon0 libasyncns0 libbrotli1 libssh2-1t64 libva2 libvdpau1'.split()


def paragraphs(text):
    result = []
    for paragraph in text.split('\n\n'):
        fields = {}
        for line in paragraph.splitlines():
            if line and not line[0].isspace() and ': ' in line:
                key, value = line.split(': ', 1)
                fields[key] = value
        if fields:
            result.append(fields)
    return result


def compare(left, op, right):
    return subprocess.run(['dpkg', '--compare-versions', left, op, right], check=False).returncode == 0


def resolve(root):
    installed = {p['Package']: p['Version'] for p in paragraphs((root / 'var/lib/dpkg/status').read_text()) if p.get('Status') == 'install ok installed'}
    selected = {}
    queue = [(name, None, None) for name in INITIAL]
    while queue:
        name, op, version = queue.pop(0)
        if name in installed:
            if op and not compare(installed[name], op, version):
                raise ValueError(f'base {name} {installed[name]} does not satisfy {op} {version}')
            continue
        if name in selected:
            if op and not compare(selected[name]['version'], op, version):
                raise ValueError(f'pinned {name} does not satisfy {op} {version}')
            continue
        metadata = subprocess.run(['apt-cache', 'show', name + ':arm64'], capture_output=True, text=True, check=True).stdout
        metadata += '\n\n' + subprocess.run(['apt-cache', 'show', name], capture_output=True, text=True, check=True).stdout
        candidates = [p for p in paragraphs(metadata) if p.get('Architecture') in ('arm64', 'all') and p.get('SHA256') and p.get('Filename') and (not op or compare(p['Version'], op, version))]
        if not candidates:
            raise ValueError(f'no arm64 cached metadata for {name}')
        chosen = candidates[0]
        for candidate in candidates[1:]:
            if compare(candidate['Version'], 'lt', chosen['Version']):
                chosen = candidate
        selected[name] = dict(package=name, version=chosen['Version'], architecture=chosen['Architecture'], sha256=chosen['SHA256'], url='https://ports.ubuntu.com/ubuntu-ports/' + chosen['Filename'])
        for term in (chosen.get('Pre-Depends', '') + ',' + chosen.get('Depends', '')).split(','):
            alternatives = [x.strip() for x in term.split('|') if x.strip()]
            if not alternatives:
                continue
            # Prefer an already supplied alternative, otherwise the first.
            term = next((x for x in alternatives if re.match(r'[\w.+-]+', x).group() in installed), alternatives[0])
            match = re.fullmatch(r'([\w.+-]+)(?::[\w-]+)?(?: \((>=|<=|=|>>|<<) ([^)]+)\))?', term)
            if not match:
                raise ValueError(f'unsupported dependency: {term}')
            dep, depop, depversion = match.groups()
            queue.append((dep, {'>>': 'gt', '<<': 'lt'}.get(depop, depop), depversion))
    return {'schema': 1, 'purpose': 'native Steam startup data-only overlays; not dpkg installed', 'packages': list(selected.values())}


def route(root, rel):
    path = root / rel
    if not path.resolve().is_relative_to(root):
        raise ValueError(f'destination escapes extracted root: {rel}')
    return path


def extract(root, data):
    """Reject escaping paths/links before writing any archive payload."""
    root = root.resolve()
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:*') as archive:
        members = archive.getmembers()
        links = set()
        for member in members:
            name = member.name.removeprefix('./')
            if name in ('', '.'):
                continue
            rel = Path(name)
            if rel.is_absolute() or '..' in rel.parts:
                raise ValueError(f'unsafe archive path: {name}')
            route(root, rel)
            if member.issym():
                target = Path(member.linkname)
                if target.is_absolute() or not (root / rel.parent / target).resolve().is_relative_to(root):
                    raise ValueError(f'unsafe archive symlink: {name}')
                links.add(rel)
            elif not (member.isfile() or member.isdir()):
                raise ValueError(f'unsupported archive entry: {name}')
        for member in members:
            rel = Path(member.name.removeprefix('./'))
            if any(parent in links for parent in rel.parents):
                raise ValueError(f'archive writes through its own symlink: {rel}')
        for member in members:
            rel = Path(member.name.removeprefix('./'))
            if str(rel) == '.':
                continue
            path = route(root, rel)
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                path.unlink()
            if member.issym():
                path.symlink_to(member.linkname)
            else:
                path.write_bytes(archive.extractfile(member).read())
                path.chmod(member.mode & 0o777)


def apply(root, cache, lock):
    root = root.resolve()
    if root == Path('/') or not (root / 'usr/lib/xorg/Xorg').is_file():
        raise ValueError('expected extracted test root with Xorg')
    cache.mkdir(parents=True, exist_ok=True)
    for package in lock['packages']:
        path = cache / (package['package'] + '_' + package['sha256'][:16] + '.deb')
        if not path.exists():
            with urllib.request.urlopen(package['url'], timeout=60) as response:
                data = response.read()
            if hashlib.sha256(data).hexdigest() != package['sha256']:
                raise ValueError(f'hash mismatch: {package["package"]}')
            path.write_bytes(data)
        if hashlib.sha256(path.read_bytes()).hexdigest() != package['sha256']:
            raise ValueError(f'cached hash mismatch: {package["package"]}')
        control = subprocess.run(['dpkg-deb', '-f', str(path), 'Architecture'], capture_output=True, text=True, check=True).stdout.strip()
        if control not in ('arm64', 'all') or control != package['architecture']:
            raise ValueError(f'architecture mismatch: {package["package"]}')
        data = subprocess.run(['dpkg-deb', '--fsys-tarfile', str(path)], capture_output=True, check=True).stdout
        extract(root, data)
        print('Extracted data-only overlay:', package['package'], package['version'], flush=True)
    manifest = route(root, Path('usr/share/steam34/native-runtime-overlays.json'))
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(lock, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('resolve', 'apply'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--lock', type=Path, default=Path(__file__).with_name('native-libs.lock.json'))
    parser.add_argument('--cache', type=Path, default=Path('build/native-runtime-debs'))
    args = parser.parse_args()
    if args.mode == 'resolve':
        args.lock.write_text(json.dumps(resolve(args.root), indent=2) + '\n')
    else:
        apply(args.root, args.cache, json.loads(args.lock.read_text()))
