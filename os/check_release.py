#!/usr/bin/env python3
"""Release-readiness report for a built WispOS tree (docs/WISPOS-OS-DESIGN.md, "Effort 4 inventory"): what the image carries NOW against the proposed release profile.

  os/check_release.py [--tree DIR] [--extlinux FILE] [--strict] [--json]
    --tree      the unpacked SYSTEM (default: the work tree's rootfs/tree)
    --extlinux  the extlinux.conf of the card image (default: the work tree's image/boot/extlinux/extlinux.conf; the kernel arguments are not in SYSTEM)
Each row says OK (matches the proposal), DIFFERS (a known gap to close before a release), ACCEPTED (a gap that is deliberately kept for this release, with the reason and what would
close it) or DECISION (the proposal itself is the operator's call; the current value is shown so he can decide). This is decision support, not a gate: the exit status is 0 unless
--strict is given, and then only DIFFERS rows fail it. check_rootfs.py is the gate for what is already decided
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


# Targets the WispOS boot graph never reaches (wispos.target needs multi-user and graphical only; these are started by a kernel argument or by hand): a unit enabled only there is not running.
UNREACHABLE_WANTS = ('textmode.target.wants', 'installer.target.wants', 'rescue.target.wants', 'emergency.target.wants')

# ROCKNIX leftovers that 0.1.0 keeps ON PURPOSE (0.1.0 plan, "Decisions I am taking": removing them cannot be verified without the handheld), each with why. The gate reports them as
# ACCEPTED rather than as gaps; every one is on the list to close once a device session can show what it does on this board. A leftover NOT named here is a gap.
EXPLAINED_LEFTOVERS = {
    'rocknix-autostart.service': 'runs the hardware bring-up steps (audio routing, display, the H700 quirks, the sway config) that the session depends on; what each step does on this board is only visible on the device',
    'input.service': 'the volume and brightness buttons (input_sense); Steam Input does not cover the hardware keys',
    'powerstate.service': 'the AC/battery CPU performance policy; its effect on thermals and runtime needs the device',
    'batteryplus.service': 'a battery fuel-gauge helper for some H700 boards; inert where that hardware is absent, and the board here cannot be checked from the host',
    'hdmi-hotplug.path': 'inert on this board (no HDMI connector); harmless',
    'rocknix-memory-manager.service': 'memory and zram policy set at boot; the session also sets its own zram, and their interplay needs the device',
}


def enabled(tree, unit, skip=()):
    """Directories (relative) whose *.wants hold a link to UNIT (skip: *.wants directory names that do not count)."""
    return sorted(str(p.parent.relative_to(tree)) for d in UNIT_DIRS if (tree / d).is_dir() for p in (tree / d).glob(f'*.wants/{unit}') if (p.is_symlink() or p.exists()) and p.parent.name not in skip)


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
        "a known default is the convention for these devices (SteamOS deck/deck, Bazzite): ACCEPTED by the operator 2026-10-12; with SSH off by default it is reachable on the console only", 'ACCEPTED' if default_pw else 'OK')
    add('root_password', 'root has no password', 'none (key only)' if shadow.get('root') in ('*', '!', '!!') else 'a password is set', 'none (key only)', 'OK' if shadow.get('root') in ('*', '!', '!!') else 'DIFFERS')
    unit = tree / 'usr/lib/systemd/system/sshd.service'
    conditional = unit.exists() and 'ConditionPathExists=/storage/.config/wispos/ssh-enabled' in unit.read_text()
    sshd_on = enabled(tree, 'sshd.service')
    add('sshd_enabled', 'sshd starts at boot', 'only while the user turned it on (flag file; `wisp-ssh`)' if conditional else ('yes (%s)' % ', '.join(sshd_on) if sshd_on else 'no'),
        'off by default, a persistent toggle (desktop mode), on by default on dev images (operator, 2026-10-11)', 'OK' if conditional or not sshd_on else 'DIFFERS')
    pa = sshd_directive(tree, 'PasswordAuthentication')
    add('ssh_password_auth', 'PasswordAuthentication while sshd is on', pa or 'not set', "password login only while the user has turned SSH on (off by default): ACCEPTED by the operator 2026-10-12", 'ACCEPTED' if pa == 'yes' else 'OK')
    prl = sshd_directive(tree, 'PermitRootLogin')
    add('ssh_root_login', 'PermitRootLogin', prl or 'not set (the default)', 'prohibit-password or no', 'OK' if prl in ('prohibit-password', 'no', 'without-password', None) else 'DIFFERS')
    append = re.search(r'^\s*APPEND\s+(.*)$', extlinux_text or '', re.M)
    args = append.group(1).split() if append else None
    # ROCKNIX ships both on its release images (its reference extlinux.conf: `console=ttyS0,115200 console=tty0 systemd.debug_shell=ttyS0`) and anyone holding the card can edit
    # extlinux.conf on a PC anyway, so WispOS follows the convention (operator, 2026-10-11): shown for information, never a gap.
    add('debug_shell_arg', 'systemd.debug_shell on the kernel command line', 'unknown (no extlinux.conf given)' if args is None else ('present' if any(a.startswith('systemd.debug_shell') for a in args) else 'absent'),
        "ROCKNIX's convention: kept", 'OK')
    add('serial_console_arg', 'a serial console on the kernel command line', 'unknown (no extlinux.conf given)' if args is None else ('present' if any(a.startswith('console=ttyS') for a in args) else 'absent'),
        "ROCKNIX's convention: kept", 'OK')
    add('tty9_shell', 'an always-on root shell on tty9', 'enabled' if enabled(tree, 'debug-shell.service') else 'not enabled', 'not enabled', 'DIFFERS' if enabled(tree, 'debug-shell.service') else 'OK')
    left, accepted = [], []
    for u in ROCKNIX_UNITS:
        if u == 'debug-shell.service': continue
        note = None
        if enabled(tree, u, UNREACHABLE_WANTS): note = u
        elif started_by_autostart(tree, u): note = f"{u} (started by autostart step {', '.join(started_by_autostart(tree, u))})"
        if note is None: continue
        if u in EXPLAINED_LEFTOVERS: accepted.append(f'{note} [accepted for this release: {EXPLAINED_LEFTOVERS[u]}]')
        else: left.append(note)
    add('rocknix_units', 'ROCKNIX leftovers enabled in the boot graph', '; '.join(left + accepted) if left or accepted else 'none', 'removed or replaced (effort 1); the ones kept on purpose are named and explained',
        'DIFFERS' if left else ('ACCEPTED' if accepted else 'OK'))
    osr = (tree / 'etc/os-release').read_text() if (tree / 'etc/os-release').exists() else ''
    add('os_release_identity', 'os-release carries WispOS identity and no ROCKNIX upstream', 'ROCKNIX named' if re.search('ROCKNIX', osr) else ('WispOS' if 'ID="wispos"' in osr else 'no WispOS id'),
        'WispOS, our version and build id', 'OK' if 'ID="wispos"' in osr and 'ROCKNIX' not in osr else 'DIFFERS')
    src = (tree / 'usr/share/wispos/update.conf').exists() and (tree / 'usr/share/wispos/update-key.pub').exists()
    add('update_source', 'an update source and signing key are baked in', 'yes' if src else 'no (the helper says "no update source is configured")', 'yes, from the release process', 'OK' if src else 'DIFFERS')
    bench, has_tool, off = tree / 'usr/share/wispos/usb-host-bench', (tree / 'usr/bin/wisp-usb').exists(), tree / 'usr/share/wispos/usb-host-disabled'
    add('usb_host_bench', 'USB host mode has its bench-test result recorded (spike S-USB), or is switched off', ('recorded: ' + bench.read_text().strip()) if bench.exists() else
        ('switched off in this image (no bench result; operator 2026-10-12)' if off.exists() and has_tool else 'not recorded' if has_tool else 'no host-mode tool in this image'),
        'the bench result written in docs/WISPOS-OS-DESIGN.md and named at build time (WISP_USB_HOST_BENCH), or host mode switched off', 'OK' if bench.exists() or off.exists() or not has_tool else 'DIFFERS')
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
        n = {s: sum(1 for x in out if x['status'] == s) for s in ('OK', 'ACCEPTED', 'DIFFERS', 'DECISION')}
        print(f"\n{n['OK']} match the proposed release profile, {n['ACCEPTED']} are gaps accepted for this release (explained), {n['DIFFERS']} differ (gaps to close), {n['DECISION']} are the operator's decision.")
    return 1 if a.strict and any(x['status'] == 'DIFFERS' for x in out) else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
