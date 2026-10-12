#!/usr/bin/env python3
"""Print what a Steam Input template (a .vdf from the Steam client's controller_base/) binds each physical control to: the way to READ Valve's conventions instead of recalling them.

  steam-bindings.py FILE.vdf [FILE.vdf ...]

For every preset: each physical input group (button diamond, D-pad, joysticks, trackpads, triggers, the switches: bumpers, menu, escape, grips) with its mode and what each part is bound to
(key_press, mouse_button, mouse_wheel, xinput_button, controller_action, game_action). The files live where the Steam client is installed (on the device: runtime/steam/controller_base;
templates for a program in controller_base/templates). Read-only; the files are Valve's data and are only parsed, never run. Written 2026-10-12 to check the Deck desktop layout
(desktop_neptune.vdf) and Valve's Web Browser template (templates/controller_neptune_webbrowser.vdf) before the desktop-mode pad layout was locked."""
import re, sys


def parse(text):
    toks = re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', text)
    pos = 0

    def block():
        nonlocal pos
        d = {}
        while pos < len(toks):
            s, br = toks[pos]
            if br == '}': pos += 1; return d
            key = s; pos += 1
            s2, br2 = toks[pos]
            if br2 == '{': pos += 1; v = block()
            else: v = s2; pos += 1
            d.setdefault(key, []).append(v)
        return d
    return block()


def first(d, k, default=None):
    v = d.get(k); return v[0] if v else default


def summarize(path):
    root = parse(open(path, encoding='utf-8', errors='replace').read())
    cm = first(root, 'controller_mappings')
    groups = {}
    for g in cm.get('group', []):
        gid, mode, ins = first(g, 'id'), first(g, 'mode'), {}
        for name, blocks in (first(g, 'inputs') or {}).items():
            outs = []
            for ib in blocks:
                for kind, activators in (first(ib, 'activators') or {}).items():
                    for ab in activators:
                        for bb in (first(ab, 'bindings') or {}).get('binding', []):
                            outs.append(bb.split(',')[0] + ('' if kind == 'Full_Press' else '(%s)' % kind))
            ins[name] = outs
        groups[gid] = (mode, ins)
    for preset in cm.get('preset', []):
        print('== preset', first(preset, 'name'))
        for gid, vals in sorted((first(preset, 'group_source_bindings') or {}).items(), key=lambda kv: kv[1][0]):
            mode, ins = groups.get(gid, ('?', {}))
            if not vals[0].endswith(' active'): continue                 # the main mode of each group, not its mode-shift alternatives
            print('  %-24s %-16s %s' % (vals[0].replace(' active', ''), mode, '; '.join('%s=%s' % (k, ','.join(v)) for k, v in ins.items() if v)))


if __name__ == '__main__':
    if len(sys.argv) < 2: sys.exit(__doc__)
    for f in sys.argv[1:]:
        print('#####', f); summarize(f)
