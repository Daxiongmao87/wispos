#!/usr/bin/env python3
"""Release-readiness report for a built WispOS tree (docs/WISPOS-OS-DESIGN.md, "Effort 4 inventory"): what the image carries NOW against the proposed release profile.

  os/check_release.py [--tree DIR] [--extlinux FILE] [--strict] [--json]
    --tree      the unpacked SYSTEM (default: the work tree's rootfs/tree)
    --extlinux  the extlinux.conf of the card image (default: the work tree's image/boot/extlinux/extlinux.conf; the kernel arguments are not in SYSTEM)
Each row says OK (matches the proposal), DIFFERS (a known gap to close before a release) or DECISION (the proposal itself is the operator's call; the current value is shown so he can
decide). This is decision support, not a gate: the exit status is 0 unless --strict is given, and then only DIFFERS rows fail it. check_rootfs.py is the gate for what is already decided
(dev files, forbidden files)."""
import argparse, json, re, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import accounts

WORK = Path('/mnt/hdd/wispos-staging/wispos-os-20261007')
UNIT_DIRS = ('usr/lib/systemd/system', 'etc/systemd/system')
# ROCKNIX-origin units that WispOS (a Steam appliance) does not need, from the effort-1 inventory. led-poweroff is real (its ExecStop turns the LEDs off at shutdown), so it is not listed.
ROCKNIX_UNITS = ('rocknix-autostart.service', 'input.service', 'powerstate.service', 'batteryplus.service', 'hdmi-hotplug.path', 'rocknix-memory-manager.service',
                 'rocknix-automount.service', 'shell.service', 'debug-shell.service')


def enabled(tree, unit):
    """Directories (relative) whose *.wants hold a link to UNIT."""
    return sorted(str(p.parent.relative_to(tree)) for d in UNIT_DIRS if (tree / d).is_dir() for p in (tree / d).glob(f'*.wants/{unit}') if p.is_symlink() or p.exists())


def started_by_autostart(tree, unit):
    """Autostart steps (usr/lib/autostart/common) that run `systemctl start|restart UNIT`: ROCKNIX starts some services that way instead of enabling them (input.service, from
    098-deviceutils), so a missing enablement link does not mean the unit never runs."""
    name = re.escape(unit.rsplit('.', 1)[0]); found = []
    d = tree / 'usr/lib/autostart/common'
    for f in sorted(d.iterdir()) if d.is_dir() else []:
        try: text = f.read_text(errors='replace')
        except OSError: continue
        if re.search(rf'systemctl\s+(start|restart)\s+{name}\b', text): found.append(f.name)
    return found


def sshd_directive(tree, name):
    """The first effective value of an sshd_config directive (sshd takes the first), or None."""
    try: text = (tree / 'etc/ssh/sshd_config').read_text()
    except OSError: return None
    for line in text.splitlines():
        m = re.match(rf'\s*{name}\s+(\S+)', line, re.I)
        if m: return m.group(1)
    return None


def rows(tree, extlinux_text):
    r = []
    def add(key, what, current, proposed, status): r.append({'key': key, 'what': what, 'current': current, 'proposed': proposed, 'status': status})
    shadow = {l.split(':')[0]: l.split(':')[1] for l in (tree / 'etc/shadow').read_text().splitlines() if ':' in l} if (tree / 'etc/shadow').exists() else {}
    wisp = shadow.get('wisp', '')
    default_pw = bool(wisp) and wisp == accounts.sha512_crypt(accounts.SPEC['password'])
    add('wisp_password', "the `wisp` account's password", "the publicly known default 'wisp'" if default_pw else ('none' if wisp in ('', '*', '!') else 'a custom hash'),
        "the operator's call (with SSH off by default it is only reachable on the console)", 'DECISION' if default_pw else 'OK')
    add('root_password', 'root has no password', 'none (key only)' if shadow.get('root') in ('*', '!', '!!') else 'a password is set', 'none (key only)', 'OK' if shadow.get('root') in ('*', '!', '!!') else 'DIFFERS')
    sshd_on = enabled(tree, 'sshd.service')
    add('sshd_enabled', 'sshd starts at boot', 'yes (%s)' % ', '.join(sshd_on) if sshd_on else 'no', 'off by default, with an on/off toggle in desktop mode (operator, 2026-10-11)', 'DIFFERS' if sshd_on else 'OK')
    pa = sshd_directive(tree, 'PasswordAuthentication')
    add('ssh_password_auth', 'PasswordAuthentication while sshd is on', pa or 'not set', "the operator's call", 'DECISION' if pa == 'yes' else 'OK')
    prl = sshd_directive(tree, 'PermitRootLogin')
    add('ssh_root_login', 'PermitRootLogin', prl or 'not set (the default)', 'prohibit-password or no', 'OK' if prl in ('prohibit-password', 'no', 'without-password', None) else 'DIFFERS')
    append = re.search(r'^\s*APPEND\s+(.*)$', extlinux_text or '', re.M)
    args = append.group(1).split() if append else None
    add('debug_shell_arg', 'systemd.debug_shell on the kernel command line', 'unknown (no extlinux.conf given)' if args is None else ('present' if any(a.startswith('systemd.debug_shell') for a in args) else 'absent'),
        'absent on a release image', 'OK' if args is not None and not any(a.startswith('systemd.debug_shell') for a in args) else 'DIFFERS')
    add('serial_console_arg', 'a serial console on the kernel command line', 'unknown (no extlinux.conf given)' if args is None else ('present' if any(a.startswith('console=ttyS') for a in args) else 'absent'),
        'dev images only', 'OK' if args is not None and not any(a.startswith('console=ttyS') for a in args) else 'DIFFERS')
    add('tty9_shell', 'an always-on root shell on tty9', 'enabled' if enabled(tree, 'debug-shell.service') else 'not enabled', 'not enabled', 'DIFFERS' if enabled(tree, 'debug-shell.service') else 'OK')
    left = []
    for u in ROCKNIX_UNITS:
        if u == 'debug-shell.service': continue
        if enabled(tree, u): left.append(u)
        elif started_by_autostart(tree, u): left.append(f"{u} (started by autostart step {', '.join(started_by_autostart(tree, u))})")
    add('rocknix_units', 'ROCKNIX leftovers enabled in the boot graph', ', '.join(left) if left else 'none', 'removed or replaced (effort 1)', 'DIFFERS' if left else 'OK')
    osr = (tree / 'etc/os-release').read_text() if (tree / 'etc/os-release').exists() else ''
    add('os_release_identity', 'os-release carries WispOS identity and no ROCKNIX upstream', 'ROCKNIX named' if re.search('ROCKNIX', osr) else ('WispOS' if 'ID="wispos"' in osr else 'no WispOS id'),
        'WispOS, our version and build id', 'OK' if 'ID="wispos"' in osr and 'ROCKNIX' not in osr else 'DIFFERS')
    src = (tree / 'usr/share/wispos/update.conf').exists() and (tree / 'usr/share/wispos/update-key.pub').exists()
    add('update_source', 'an update source and signing key are baked in', 'yes' if src else 'no (the helper says "no update source is configured")', 'yes, from the release process', 'OK' if src else 'DIFFERS')
    return r


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--tree', default=str(WORK / 'rootfs/tree')); ap.add_argument('--extlinux', default=str(WORK / 'image/boot/extlinux/extlinux.conf'))
    ap.add_argument('--strict', action='store_true'); ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    tree = Path(a.tree)
    if not (tree / 'etc').is_dir(): print(f'not a SYSTEM tree: {tree}', file=sys.stderr); return 2
    ext = Path(a.extlinux); out = rows(tree, ext.read_text() if ext.is_file() else None)
    if a.json: print(json.dumps(out, indent=1))
    else:
        for x in out: print(f"{x['status']:8} {x['what']}\n         now: {x['current']}\n         proposed: {x['proposed']}")
        n = {s: sum(1 for x in out if x['status'] == s) for s in ('OK', 'DIFFERS', 'DECISION')}
        print(f"\n{n['OK']} match the proposed release profile, {n['DIFFERS']} differ (gaps to close), {n['DECISION']} are the operator's decision.")
    return 1 if a.strict and any(x['status'] == 'DIFFERS' for x in out) else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
