#!/usr/bin/env python3
"""The WispOS default wallpaper (docs/WISPOS-UI-LANGUAGE.md, "Desktop mode"): a subtle gradient in the palette's blues and, optionally, the badge as a very faint ghost or recess.

  wallpaper.py --badge PNG --out FILE [--size 720x480] [--variant plain|ghost|carved] [--strength N]

plain   the gradient only: top-left WISP_UI_WALL_TOP down to WISP_UI_WALL_BOTTOM, a soft glow of the accent colour near the upper left, a slight vignette.
ghost   plus the badge as a watermark: its own colours (white-to-cyan wisp, blue orb) added at `strength` percent, so the wisp is a little lighter than the ground.
carved  plus the badge as a recess: the ground darkened inside its outline by `strength` levels, a one-pixel shade on the upper-left edge and a lift on the lower-right, as if cut into the surface.
The badge is placed to the right of centre, whole, above the dock's strip; a wallpaper is mostly covered by windows, and the dock takes the bottom edge.
Deterministic: a fixed-seed triangular dither keeps a dark gradient from banding on an 8-bit panel. Colours come from os/ui/wisp-ui.h (one source); sizes are a parameter so
another panel gets the same picture. Needs Pillow and numpy (the host's; nothing is installed on the device for it: the image carries the PNG this makes)."""
import argparse, re, sys
from pathlib import Path
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
BADGE_HEIGHT = 0.86       # of the picture height: the whole badge sits above the dock (the bottom 44 of 480 px)
BADGE_CX, BADGE_CY = 0.70, 0.455      # centre of the badge, fractions of width and height
DEFAULT_STRENGTH = {'ghost': 1.8, 'carved': 3.0}      # percent of the badge colours added / levels darkened: both stay a few levels of 255


def token(name):
    m = re.search(r'#define\s+%s\s+(\S+)' % name, (HERE / 'wisp-ui.h').read_text())
    if not m: sys.exit('token %s not found in wisp-ui.h' % name)
    return int(m.group(1), 0)


def rgb(c): return np.array([(c >> 16) & 255, (c >> 8) & 255, c & 255], np.float32)


def gradient(w, h):
    """The base picture as float RGB in 0..255, before dither."""
    top, bottom, accent = rgb(token('WISP_UI_WALL_TOP')), rgb(token('WISP_UI_WALL_BOTTOM')), rgb(token('WISP_UI_ACCENT'))
    y, x = np.mgrid[0:h, 0:w].astype(np.float32)
    t = (y / max(h - 1, 1))[..., None]
    img = top * (1 - t) + bottom * t
    gx, gy = token('WISP_UI_WALL_GLOW_X') / 100 * w, token('WISP_UI_WALL_GLOW_Y') / 100 * h
    d = np.sqrt((x - gx) ** 2 + (y - gy) ** 2) / (0.75 * w)
    glow = np.clip(1 - d, 0, 1) ** 2 * (token('WISP_UI_WALL_GLOW') / 100)
    img = img * (1 - glow[..., None]) + accent * glow[..., None]
    v = np.sqrt(((x - w / 2) / (w / 2)) ** 2 + ((y - h / 2) / (h / 2)) ** 2) / np.sqrt(2)
    img *= (1 - 0.10 * np.clip(v, 0, 1) ** 2)[..., None]
    return img


def badge_layer(badge, w, h):
    """The badge scaled and placed, as float RGB (0..255, straight colour) and alpha (0..1), both w x h."""
    bw, bh = badge.size
    nh = int(round(h * BADGE_HEIGHT)); nw = int(round(bw * nh / bh))
    big = badge.convert('RGBA').resize((nw, nh), Image.LANCZOS)
    canvas = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    canvas.paste(big, (int(round(BADGE_CX * w - nw / 2)), int(round(BADGE_CY * h - nh / 2))), big)
    a = np.asarray(canvas, np.float32)
    return a[..., :3], a[..., 3] / 255


def render(w=720, h=480, variant='plain', badge=None, strength=None):
    img = gradient(w, h)
    if variant != 'plain':
        if badge is None: raise ValueError('the %s variant needs the badge image' % variant)
        colour, alpha = badge_layer(badge, w, h)
        k = DEFAULT_STRENGTH[variant] if strength is None else strength
        if variant == 'ghost':
            img = img + (k / 100) * colour * alpha[..., None]
        elif variant == 'carved':
            soft = alpha[..., None]
            img = img - k * soft
            up = np.roll(np.roll(alpha, 1, 0), 1, 1); down = np.roll(np.roll(alpha, -1, 0), -1, 1)
            edge_light = np.clip(alpha - down, 0, 1)          # the lower-right rim: inside edge facing the light
            edge_dark = np.clip(alpha - up, 0, 1)             # the upper-left rim: in the recess's own shadow
            img = img + (0.5 * k) * edge_light[..., None] - (0.5 * k) * edge_dark[..., None]
        else:
            raise ValueError(variant)
    rng = np.random.default_rng(20261012)                      # TPDF dither: the sum of two uniform noises, +-1 level
    noise = rng.random((h, w, 1), dtype=np.float32) + rng.random((h, w, 1), dtype=np.float32) - 1
    return Image.fromarray(np.clip(np.rint(img + noise), 0, 255).astype(np.uint8), 'RGB')


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--badge'); ap.add_argument('--out', required=True); ap.add_argument('--size', default='720x480')
    ap.add_argument('--variant', default='plain', choices=('plain', 'ghost', 'carved')); ap.add_argument('--strength', type=float)
    a = ap.parse_args(argv)
    w, h = (int(v) for v in a.size.split('x'))
    render(w, h, a.variant, Image.open(a.badge) if a.badge else None, a.strength).save(a.out, optimize=True)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
