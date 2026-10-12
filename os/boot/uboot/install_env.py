#!/usr/bin/env python3
"""Put the boot-trial environment into a U-Boot source tree (the handheld's, os/boot/build-uboot.sh): write include/configs/wisp-env.h and make the sunxi board header append
WISP_ENV after BOOTENV. It refuses a tree whose header or distro boot variable is not exactly what this was written against (each anchor must occur exactly once), so a
changed upstream cannot silently drop the hook.   usage: install_env.py UBOOT_TREE"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import make_env

ORIG_BOOT_EXTLINUX = '"boot_extlinux=" \\ "sysboot ${devtype} ${devnum}:${distro_bootpart} any " \\ "${scriptaddr} ${prefix}${boot_syslinux_conf}\\0" \\'      # whitespace-normalised


def once(text, anchor, what):
    if text.count(anchor) != 1: raise SystemExit('install_env: %s: expected exactly one match, found %d' % (what, text.count(anchor)))


def install(tree):
    tree = Path(tree)
    distro = (tree / 'include/config_distro_bootcmd.h').read_text()
    once(' '.join(distro.split()), ORIG_BOOT_EXTLINUX, 'the distro boot variable boot_extlinux')
    h = tree / 'include/configs/sunxi-common.h'; text = h.read_text()
    if '#include "wisp-env.h"' in text: raise SystemExit('install_env: already installed')
    end = '\tBOOTCMD_SUNXI_COMPAT \\\n\tBOOTENV\n'
    once(text, end, 'the end of CFG_EXTRA_ENV_SETTINGS')
    start = '#define CFG_EXTRA_ENV_SETTINGS \\\n'
    once(text, start, 'the start of CFG_EXTRA_ENV_SETTINGS')
    text = text.replace(end, '\tBOOTCMD_SUNXI_COMPAT \\\n\tBOOTENV \\\n\tWISP_ENV\n').replace(start, '#include "wisp-env.h"\n\n' + start)
    h.write_text(text)
    src = Path(__file__).resolve().parent / 'wisp-trial.txt'
    (tree / 'include/configs/wisp-env.h').write_text(make_env.header(src.read_text()))


if __name__ == '__main__':
    if len(sys.argv) != 2: raise SystemExit(__doc__)
    install(sys.argv[1])
