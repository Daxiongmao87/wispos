#!/usr/bin/env python3
"""Patch ROCKNIX's reference initramfs (a newc cpio) for WispOS: the boot splash (docs/WISPOS-SPLASH-DESIGN.md) and the boot slots of the update scheme
(docs/WISPOS-UPDATE-DESIGN.md). Two entries replaced or edited, two added, nothing else:
  usr/bin/rocknix-splash  -> our wrapper script (ROCKNIX's init runs it synchronously and carries on; the wrapper starts wisp-splash detached)
  usr/bin/wisp-splash     -> new, the static splash binary
  wisp-boot               -> new, os/boot/wisp-boot.sh: the functions of the slot step
  init                    -> ROCKNIX's init with exactly two lines added: `. /wisp-boot` after the line that sources /device.init, and the step `wisp_slot` after `cleanup_flash`
                             in the list of boot steps (after the boot partition is mounted, before the system image is). Each anchor must occur exactly once or the patch refuses.
usage: patch_initramfs.py REF_CPIO OUT_CPIO WRAPPER_SCRIPT SPLASH_BINARY BOOT_SCRIPT
`read_cpio` / `write_cpio` are exact: writing back what was read reproduces the archive byte for byte (the test proves it on the real reference), so a patched archive
differs from the reference in those entries and in nothing else. Pure stdlib."""
import sys

MAGIC = b'070701'
FIELDS = ('ino', 'mode', 'uid', 'gid', 'nlink', 'mtime', 'filesize', 'devmajor', 'devminor', 'rdevmajor', 'rdevminor', 'namesize', 'check')
S_IFREG = 0o100000


def pad4(n): return (n + 3) & ~3


def read_cpio(data):
    """-> (entries, tail): entries is a list of dicts (the header fields without sizes, 'name', 'data'), tail the bytes after the trailer entry (block padding)."""
    pos, entries = 0, []
    while True:
        if data[pos:pos + 6] != MAGIC: raise ValueError('not a newc cpio entry at offset %d' % pos)
        f = {k: int(data[pos + 6 + 8 * i:pos + 14 + 8 * i], 16) for i, k in enumerate(FIELDS)}
        name_at = pos + 110
        name = data[name_at:name_at + f['namesize'] - 1].decode()
        body_at = pad4(name_at + f['namesize'])
        body = data[body_at:body_at + f['filesize']]
        pos = pad4(body_at + f['filesize'])
        if name == 'TRAILER!!!': return entries, data[pos:]
        entries.append({'ino': f['ino'], 'mode': f['mode'], 'uid': f['uid'], 'gid': f['gid'], 'nlink': f['nlink'], 'mtime': f['mtime'], 'devmajor': f['devmajor'],
                        'devminor': f['devminor'], 'rdevmajor': f['rdevmajor'], 'rdevminor': f['rdevminor'], 'check': f['check'], 'name': name, 'data': bytes(body)})


def entry_bytes(e):
    name = e['name'].encode() + b'\0'
    hdr = MAGIC + b''.join(b'%08X' % v for v in (e['ino'], e['mode'], e['uid'], e['gid'], e['nlink'], e['mtime'], len(e['data']), e['devmajor'], e['devminor'],
                                                  e['rdevmajor'], e['rdevminor'], len(name), e['check']))
    out = hdr + name
    out += b'\0' * (pad4(len(out)) - len(out))
    out += e['data']
    out += b'\0' * (pad4(len(out)) - len(out))
    return out


def write_cpio(entries, tail):
    trailer = {'ino': 0, 'mode': 0, 'uid': 0, 'gid': 0, 'nlink': 1, 'mtime': 0, 'devmajor': 0, 'devminor': 0, 'rdevmajor': 0, 'rdevminor': 0, 'check': 0, 'name': 'TRAILER!!!', 'data': b''}
    return b''.join(entry_bytes(e) for e in entries + [trailer]) + tail


SOURCE_ANCHOR = b'. /device.init 2>/dev/null\n'            # the init sources its helper files here; ours goes right behind
SOURCE_LINE = b'. /wisp-boot 2>/dev/null\n'
STEP_ANCHOR = b'    cleanup_flash \\\n'                      # in `for BOOT_STEP in \ ... do`: the boot partition is mounted, the system image is not yet
STEP_LINE = b'    wisp_slot \\\n'


def patch_init(init):
    """ROCKNIX's init with the two WispOS lines added; refuses an init that does not have each anchor exactly once (an unknown init is never edited blindly)."""
    for anchor in (SOURCE_ANCHOR, STEP_ANCHOR):
        if init.count(anchor) != 1: raise ValueError('the init does not have the anchor %r exactly once (found %d)' % (anchor.decode(), init.count(anchor)))
    return init.replace(SOURCE_ANCHOR, SOURCE_ANCHOR + SOURCE_LINE).replace(STEP_ANCHOR, STEP_ANCHOR + STEP_LINE)


def patch(data, wrapper, binary, boot_script, mtime=None):
    entries, tail = read_cpio(data)
    names = [e['name'] for e in entries]
    for needed in ('usr/bin/rocknix-splash', 'init', 'device.init'):
        if needed not in names: raise ValueError('the reference has no %s' % needed)
    if 'usr/bin/wisp-splash' in names or 'wisp-boot' in names: raise ValueError('already patched')
    i = names.index('usr/bin/rocknix-splash')
    old = entries[i]
    new_inode = max(e['ino'] for e in entries) + 1
    when = old['mtime'] if mtime is None else mtime
    entries[i] = dict(old, data=wrapper, mode=S_IFREG | 0o755, nlink=1, check=0)
    entries.insert(i + 1, dict(old, ino=new_inode, name='usr/bin/wisp-splash', data=binary, mode=S_IFREG | 0o755, nlink=1, mtime=when, check=0))
    j = names.index('init')
    entries[j] = dict(entries[j], data=patch_init(entries[j]['data']))
    k = [e['name'] for e in entries].index('device.init')
    dev = entries[k]
    entries.insert(k + 1, dict(dev, ino=new_inode + 1, name='wisp-boot', data=boot_script, mode=S_IFREG | 0o644, nlink=1, mtime=dev['mtime'] if mtime is None else mtime, check=0))
    return write_cpio(entries, tail)


def main(argv):
    if len(argv) != 5: sys.exit(__doc__)
    ref, out, wrapper, binary, boot = argv
    with open(ref, 'rb') as f: data = f.read()
    with open(wrapper, 'rb') as f: w = f.read()
    with open(binary, 'rb') as f: b = f.read()
    with open(boot, 'rb') as f: g = f.read()
    patched = patch(data, w, b, g)
    with open(out, 'wb') as f: f.write(patched)
    print('wrote %s: %d bytes (reference %d, wrapper %d, splash %d, boot script %d)' % (out, len(patched), len(data), len(w), len(b), len(g)))


if __name__ == '__main__':
    main(sys.argv[1:])
