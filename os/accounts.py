#!/usr/bin/env python3
"""WispOS account database: the `wisp` user (password `wisp`, chosen by the operator) baked into the rootfs.

The image is read-only, so accounts are written into its /etc at build time:
    accounts.py ROOTFS_DIR      # adds wisp to etc/passwd and etc/group, writes a real etc/shadow
root has no password (SSH key only; the field is '*', because sshd without PAM refuses '!' accounts even for keys); system users get no password. The hash uses a
fixed salt so two builds of the same inputs are byte-identical. `wisp`/`wisp` is a publicly known
default by the operator's decision; change SPEC (and rebuild) to change it.
"""
import subprocess, sys
from pathlib import Path

SALT = 'wispwispwispwisp'
LAST_CHANGE_DAYS = 20733          # 2026-10-07, fixed for reproducibility
SPEC = {'name': 'wisp', 'uid': 1000, 'gid': 1000, 'gecos': 'WispOS', 'home': '/storage/home/wisp',
        'shell': '/bin/sh', 'password': 'wisp',
        'groups': ['wheel', 'video', 'audio', 'input', 'render', 'netdev', 'users', 'systemd-journal']}


def sha512_crypt(password, salt=SALT):
    """$6$ crypt hash from OpenSSL (read from stdin, never in argv)."""
    out = subprocess.run(['openssl', 'passwd', '-6', '-salt', salt, '-stdin'], input=password + '\n',
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def passwd_line(spec=SPEC):
    return f"{spec['name']}:x:{spec['uid']}:{spec['gid']}:{spec['gecos']}:{spec['home']}:{spec['shell']}"


def update_group(text, spec=SPEC):
    """Add the user's primary group and append the user to its supplementary groups."""
    lines, out, seen = text.splitlines(), [], False
    for line in lines:
        fields = line.split(':')
        if fields[0] == spec['name']:
            seen = True
        if fields[0] in spec['groups'] and len(fields) == 4:
            members = [m for m in fields[3].split(',') if m]
            if spec['name'] not in members:
                members.append(spec['name'])
            fields[3] = ','.join(members)
            line = ':'.join(fields)
        out.append(line)
    if not seen:
        out.append(f"{spec['name']}:x:{spec['gid']}:")
    return '\n'.join(out) + '\n'


def shadow_text(passwd_text, spec=SPEC):
    rows = []
    for line in passwd_text.splitlines():
        name = line.split(':')[0]
        if name == spec['name']:
            secret = sha512_crypt(spec['password'])
        elif name == 'root':
            secret = '*'                  # no password login; SSH key only. Not '!': sshd refuses locked accounts even for keys
        else:
            secret = '*'
        rows.append(f"{name}:{secret}:{LAST_CHANGE_DAYS}:0:99999:7:::")
    return '\n'.join(rows) + '\n'


def apply(root):
    root = Path(root)
    passwd = (root / 'etc/passwd').read_text()
    if not any(l.startswith(SPEC['name'] + ':') for l in passwd.splitlines()):
        passwd = passwd.rstrip('\n') + '\n' + passwd_line() + '\n'
    (root / 'etc/passwd').write_text(passwd)
    (root / 'etc/group').write_text(update_group((root / 'etc/group').read_text()))
    shadow = root / 'etc/shadow'
    if shadow.is_symlink():
        shadow.unlink()                   # ROCKNIX links it into /storage; WispOS ships a real file
    shadow.write_text(shadow_text(passwd))
    shadow.chmod(0o640)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    apply(sys.argv[1])
    print(f"accounts written into {sys.argv[1]}")
