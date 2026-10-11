#!/usr/bin/env python3
"""Fetch a pinned Valve ARM64 client for private test images; never execute it.

Only source/locks belong in git. Valve binaries remain in ignored build storage.
Archives and the exact CDN manifest are hash checked before extraction.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import struct
import tempfile
import urllib.request
import zipfile


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fetch(url, expected, destination, size=None):
    """Reuse verified cache entries; atomically publish verified downloads."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if digest(destination) != expected or (size is not None and destination.stat().st_size != size):
            raise ValueError(f'cache hash/size mismatch: {destination}')
        return destination
    fd, temporary = tempfile.mkstemp(prefix='.download-', dir=destination.parent)
    try:
        with os.fdopen(fd, 'wb') as output, urllib.request.urlopen(url, timeout=120) as response:
            shutil.copyfileobj(response, output)
        if digest(temporary) != expected:
            raise ValueError(f'download hash mismatch: {url}')
        if size is not None and Path(temporary).stat().st_size != size:
            raise ValueError(f'download size mismatch: {url}')
        os.replace(temporary, destination)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return destination


def safe_name(name):
    """Normalize Valve Windows separators and reject absolute/traversal paths."""
    name = name.replace('\\', '/')
    path = PurePosixPath(name)
    if not name or path.is_absolute() or '..' in path.parts or ':' in name or '\x00' in name:
        raise ValueError(f'unsafe ZIP path: {name!r}')
    return path


def contained(path, root):
    return path.resolve().is_relative_to(root.resolve())


def extract(archive, root):
    """Extract without following archive-created symlinks outside the seed."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        # Preflight every name before writing any entry.
        entries = [(entry, safe_name(entry.filename)) for entry in bundle.infolist()]
        for entry, relative in entries:
            target = root.joinpath(*relative.parts)
            if not contained(target.parent, root):
                raise ValueError(f'symlink parent escapes seed: {relative}')
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = entry.external_attr >> 16
            if entry.is_dir():
                if target.is_symlink():
                    raise ValueError(f'directory is a symlink: {relative}')
                target.mkdir(exist_ok=True)
                continue
            if stat.S_ISLNK(mode):
                link = bundle.read(entry).decode('utf-8').replace('\\', '/')
                if not link or PurePosixPath(link).is_absolute() or ':' in link or '\x00' in link:
                    raise ValueError(f'unsafe symlink: {relative}')
                if not contained(target.parent / link, root):
                    raise ValueError(f'symlink escapes seed: {relative}')
                if target.exists() or target.is_symlink():
                    raise ValueError(f'duplicate symlink target: {relative}')
                target.symlink_to(link)
                continue
            if target.is_symlink():
                raise ValueError(f'refusing write through symlink: {relative}')
            with bundle.open(entry) as source, target.open('wb') as output:
                shutil.copyfileobj(source, output)
            permissions = stat.S_IMODE(mode) & 0o777
            with target.open('rb') as source:
                if source.read(4) == b'\x7fELF':
                    permissions |= 0o111
            target.chmod(permissions or 0o644)


def verify_arm64(root):
    """Check ELF identity of startup binaries without loading/executing them."""
    result = {}
    for name in ('steam', 'steamwebhelper', 'steamclient.so'):
        path = Path(root) / 'steamrtarm64' / name
        header = path.read_bytes()[:20]
        if len(header) != 20 or header[:6] != b'\x7fELF\x02\x01' or struct.unpack('<H', header[18:20])[0] != 183:
            raise ValueError(f'not ELF64 little-endian AArch64: {path}')
        result[name] = digest(path)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path, default=Path(__file__).with_name('native-client.lock.json'))
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--fetch-only', action='store_true')
    parser.add_argument('--jobs', type=int, default=4)
    args = parser.parse_args()
    lock = json.loads(args.lock.read_text())
    if args.jobs < 1:
        parser.error('--jobs must be positive')
    if not args.fetch_only and args.output is None:
        parser.error('--output is required unless --fetch-only')
    fetch(lock['manifest_url'], lock['manifest_sha256'], args.cache / 'manifest.vdf')
    def get(component):
        filename = component['url'].rsplit('/', 1)[1]
        return fetch(component['url'], component['sha256'], args.cache / filename, component['size'])
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        archives = list(executor.map(get, lock['components']))
    if not args.fetch_only:
        if args.output.exists():
            raise ValueError(f'output already exists: {args.output}; choose a fresh seed')
        for archive in archives:
            extract(archive, args.output)
        print(json.dumps({'version':lock['version'], 'elf_sha256':verify_arm64(args.output)}, indent=2))
    else:
        print(f'verified {len(archives)} components for {lock["version"]}')


if __name__ == '__main__':
    main()
