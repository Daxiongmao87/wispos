#!/usr/bin/env python3
"""Populate a freshly made FAT32 image with files, without mounting it (no root, no mtools).

    fatwrite.py IMAGE_OR_PARTITION_OFFSET... see populate(); CLI: fatwrite.py IMG OFFSET SRC_DIR

The filesystem must already exist (mkfs.fat) and be empty. This writer only knows what an image
builder needs: directories and regular files laid out contiguously, 8.3 names plus VFAT long-name
entries, no deletion, no fragmentation. Geometry (sector size, cluster size, FAT size, root
cluster) is read from the boot sector that mkfs.fat wrote, so the result stays a normal FAT32
volume: `fsck.fat -n` and a file-by-file comparison verify it (os/test_fatwrite.py).
"""
import struct, sys
from pathlib import Path

ATTR_DIR, ATTR_LFN = 0x10, 0x0F


def lfn_checksum(short11):
    total = 0
    for byte in short11:
        total = (((total & 1) << 7) + (total >> 1) + byte) & 0xFF
    return total


def short_name(name, taken):
    """8.3 name for `name`: exact when it already fits uppercase, else a ~N alias."""
    base, dot, ext = name.rpartition('.')
    if not dot:
        base, ext = name, ''
    clean = lambda s: ''.join(c for c in s.upper() if c.isalnum() or c in '_-$~!#%&(){}')
    stem, suffix = clean(base), clean(ext)[:3]
    exact = name == name.upper() and len(base) <= 8 and len(ext) <= 3 and clean(base) == base and clean(ext) == ext
    if not exact:
        for n in range(1, 1000):
            tail = f'~{n}'
            candidate = (stem[:8 - len(tail)] + tail).ljust(8) + suffix.ljust(3)
            if candidate not in taken:
                return candidate.encode('ascii'), True
        raise ValueError(f'no short name for {name}')
    return (stem.ljust(8) + suffix.ljust(3)).encode('ascii'), False


def dir_entries(name, short11, needs_lfn, attr, first_cluster, size):
    """Directory entry bytes (long-name entries first, then the short entry)."""
    entries = b''
    if needs_lfn:
        units = list(name.encode('utf-16-le'))
        chars = [units[i] | units[i + 1] << 8 for i in range(0, len(units), 2)] + [0]
        chars += [0xFFFF] * ((-len(chars)) % 13)
        chunks = [chars[i:i + 13] for i in range(0, len(chars), 13)]
        check = lfn_checksum(short11)
        for index in range(len(chunks), 0, -1):
            part = chunks[index - 1]
            order = index | (0x40 if index == len(chunks) else 0)
            entries += struct.pack('<B5HBBB6HH2H', order, *part[0:5], ATTR_LFN, 0, check, *part[5:11], 0, *part[11:13])
    entries += struct.pack('<11sBBBHHHHHHHI', short11, attr, 0, 0, 0x6000, 0x5A21, 0x5A21,
                           first_cluster >> 16, 0x6000, 0x5A21, first_cluster & 0xFFFF, size)
    return entries


class Fat32:
    def __init__(self, handle, offset):
        self.f, self.base = handle, offset
        self.f.seek(offset)
        boot = self.f.read(512)
        (self.bps, self.spc, reserved, nfats) = struct.unpack_from('<HBHB', boot, 11)
        self.fat_sectors, = struct.unpack_from('<I', boot, 36)
        self.root_cluster, = struct.unpack_from('<I', boot, 44)
        total, = struct.unpack_from('<I', boot, 32)
        self.fat_start = reserved
        self.data_start = reserved + nfats * self.fat_sectors
        self.nfats = nfats
        self.cluster_bytes = self.bps * self.spc
        self.clusters = (total - self.data_start) // self.spc
        self.next_free = 3                       # cluster 2 is the root directory (mkfs.fat)
        self.fat = {}
        self.f.seek(self.cluster_offset(self.root_cluster))
        first = self.f.read(32)
        # Keep the volume-label entry mkfs.fat wrote, with its creation time replaced by the fixed
        # stamp used for every other entry, so two builds of the same inputs are byte-identical.
        self.root_prefix = b''
        if len(first) == 32 and first[11] == 0x08:
            self.root_prefix = dir_entries('', first[:11], False, 0x08, 0, 0)

    def cluster_offset(self, cluster):
        return self.base + (self.data_start + (cluster - 2) * self.spc) * self.bps

    def allocate(self, nbytes):
        count = max(1, -(-nbytes // self.cluster_bytes))
        first = self.next_free
        if first + count > self.clusters + 2:
            raise SystemExit('FAT image is full')
        for i in range(count):
            self.fat[first + i] = first + i + 1 if i + 1 < count else 0x0FFFFFFF
        self.next_free += count
        return first, count

    def write_chain(self, cluster, data):
        self.f.seek(self.cluster_offset(cluster))
        self.f.write(data)

    def put_tree(self, directory, parent_cluster, own_cluster=None):
        """Write `directory`'s children; returns the cluster of the directory itself."""
        children = sorted(directory.iterdir(), key=lambda p: p.name)
        if parent_cluster is not None and any(c.is_dir() for c in children):
            raise SystemExit('fatwrite: nesting deeper than one level is not supported')
        taken, entries, pending = set(), [], []
        for child in children:
            short, lfn = short_name(child.name, taken)
            taken.add(short.decode('ascii'))
            if child.is_dir():
                cluster = self.put_tree(child, self.root_cluster)
                entries.append(dir_entries(child.name, short, lfn, ATTR_DIR, cluster, 0))
            else:
                size = child.stat().st_size
                cluster, count = self.allocate(max(size, 1)) if size else (0, 0)
                if size:
                    self.f.seek(self.cluster_offset(cluster))
                    with open(child, 'rb') as src:
                        while chunk := src.read(1 << 22):
                            self.f.write(chunk)
                entries.append(dir_entries(child.name, short, lfn, 0x20, cluster, size))
        body = b''.join(entries)
        if own_cluster == self.root_cluster:
            body = self.root_prefix + body          # keep the volume-label entry mkfs.fat wrote
        if own_cluster is None:
            own_cluster, _ = self.allocate(len(body) + 64)
            dots = (dir_entries('.', b'.          ', False, ATTR_DIR, own_cluster, 0) +
                    dir_entries('..', b'..         ', False, ATTR_DIR, 0 if parent_cluster in (None, self.root_cluster) else parent_cluster, 0))
            body = dots + body
        self.write_chain_padded(own_cluster, body)
        return own_cluster

    def write_chain_padded(self, cluster, body):
        need = -(-max(len(body), 1) // self.cluster_bytes)
        have = 1
        while self.fat.get(cluster + have - 1, 0x0FFFFFFF) != 0x0FFFFFFF and have < 10 ** 6:
            have += 1
        if need > have:
            raise SystemExit('directory outgrew its clusters')
        self.write_chain(cluster, body.ljust(have * self.cluster_bytes, b'\0'))

    def finish(self):
        for copy in range(self.nfats):
            start = self.base + (self.fat_start + copy * self.fat_sectors) * self.bps
            for cluster, value in self.fat.items():
                self.f.seek(start + cluster * 4)
                self.f.write(struct.pack('<I', value))
        # FSInfo: keep the free-cluster count and next-free hint truthful for fsck and the kernel.
        self.f.seek(self.base + 48)
        info_sector, = struct.unpack('<H', self.f.read(2))
        used = self.next_free - 2
        self.f.seek(self.base + info_sector * self.bps + 488)
        self.f.write(struct.pack('<II', self.clusters - used, self.next_free))
        self.f.flush()


def populate(image_path, offset, source):
    """Fill the FAT32 volume at `offset` in `image_path` from the directory `source`."""
    with open(image_path, 'r+b') as handle:
        fs = Fat32(handle, offset)
        # Root directory lives in cluster 2 and already has its (empty) cluster from mkfs.fat.
        fs.fat[fs.root_cluster] = 0x0FFFFFFF
        root = Path(source)
        children_cluster = fs.put_tree(root, None, own_cluster=fs.root_cluster)
        assert children_cluster == fs.root_cluster
        fs.finish()


if __name__ == '__main__':
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    populate(sys.argv[1], int(sys.argv[2]), sys.argv[3])
