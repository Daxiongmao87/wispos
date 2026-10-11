#!/usr/bin/env python3
"""Preview of the boot -> first-time-setup hand-over (operator idea, 2026-10-11): over 3 seconds the ground goes from black to the setup screen's navy while the boot
elements (logo, name, spinner, stage, hint) fade OUT and the first-time-setup elements fade IN. It plays once, on the first run only.
Frames are REAL: the boot side is drawn by os/splash/wisp-splash.c, the setup side is the setup screen's own render (os/setup/design/list.png); this script only mixes them.
usage: preview_handover.py WISP_SPLASH_BINARY OUT_DIR       writes handover.gif and handover-strip.png (frames at t = 0, .25, .5, .75, 1)
The curve (the contract for the real implementation): s(x) = smoothstep(x) = x*x*(3-2x), x clamped to 0..1, t = elapsed / 3 s. Fade OUT, then fade IN (never a cross-fade):
  ground      = lerp(#000000, #0e141b, s(t))              the black -> navy gradient runs the whole 3 seconds
  boot layer  = 1 - s(t / 0.5)                            logo, name, spinner, stage and hint fade out over the first 1.5 s and are completely gone at t = 0.5
  setup layer = s((t - 0.5) / 0.5)                        the setup screen's elements fade in over the last 1.5 s (operator: 1.5 s out, 1.5 s in, 3 s in all)
Owner: the SETUP program (SDL), not the splash. The splash's last frame is the black screen with the boot elements at full opacity; the setup program maps its window with the
SAME first frame, the splash exits, and the setup program plays the 3 s itself (it has every component: the boot elements are only a badge and three lines of text), because the
gradient has to run independently of the setup elements' opacity, which a black overlay above a running setup window could not do."""
import os, subprocess, sys
from pathlib import Path
from PIL import Image, ImageChops

W, H = 720, 480
NAVY = (0x0e, 0x14, 0x1b)
HERE = Path(__file__).resolve().parent
SETUP_END = HERE.parent / 'setup/design/list.png'
FPS, HOLD_IN, HOLD_OUT, SECONDS = 20, 20, 14, 3.0


def smooth(x):
    x = max(0.0, min(1.0, x)); return x * x * (3 - 2 * x)


def boot_frame(binary, workdir, n):
    fb = Path(workdir) / 'ho.fb'; fb.write_bytes(b'')
    subprocess.run([binary, '--fb', str(fb), '--geom', f'{W}x{H}', '--no-vt', '--no-input', '--fifo', str(Path(workdir) / 'ho.fifo'), '--fps', str(FPS), '--frames', '1',
                    '--frame0', str(n), '--status', 'Getting ready for setup', '--detail', 'This only happens the first time.'], check=True, capture_output=True)
    return Image.frombuffer('RGBA', (W, H), fb.read_bytes(), 'raw', 'BGRA', 0, 1).convert('RGB')


def mix(ground, boot, setup, a_boot, a_setup):
    """ground + the boot elements scaled by a_boot + the setup elements (their difference from the navy ground) scaled by a_setup."""
    base = Image.new('RGB', (W, H), ground)
    boot_part = boot.point(lambda v: int(v * a_boot))                                   # the boot render's ground is black: its pixels ARE its elements
    setup_part = ImageChops.subtract(setup, Image.new('RGB', (W, H), NAVY)).point(lambda v: int(v * a_setup))
    return ImageChops.add(ImageChops.add(base, boot_part), setup_part)


def main(binary, out):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    setup = Image.open(SETUP_END).convert('RGB')
    frames, strip = [], {}
    n = 0
    total = int(SECONDS * FPS)
    for i in range(HOLD_IN + total + HOLD_OUT):
        t = max(0.0, min(1.0, (i - HOLD_IN) / total))
        ground = tuple(int(c * smooth(t)) for c in NAVY)
        a_boot, a_setup = 1 - smooth(t / 0.5), smooth((t - 0.5) / 0.5)
        boot = boot_frame(binary, out, n) if a_boot > 0.002 else Image.new('RGB', (W, H))
        n += 1
        frames.append(mix(ground, boot, setup, a_boot, a_setup))
        for mark in (0.0, 0.25, 0.5, 0.75, 1.0):
            if abs(t - mark) < 0.5 / total and mark not in strip and (i >= HOLD_IN): strip[mark] = frames[-1]
    frames[0].save(out / 'handover.gif', save_all=True, append_images=frames[1:], duration=1000 // FPS, loop=0, optimize=True)
    keys = sorted(strip)
    sheet = Image.new('RGB', (W // 2 * len(keys) + 10 * (len(keys) - 1), H // 2), (60, 60, 60))
    for k, m in enumerate(keys): sheet.paste(strip[m].resize((W // 2, H // 2)), (k * (W // 2 + 10), 0))
    sheet.save(out / 'handover-strip.png')
    print('wrote', out / 'handover.gif', os.path.getsize(out / 'handover.gif') // 1024, 'KB,', len(frames), 'frames;', out / 'handover-strip.png')


if __name__ == '__main__':
    if len(sys.argv) != 3: sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
