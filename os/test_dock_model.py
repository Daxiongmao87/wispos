"""Runs the dock model's C tests (os/dock/test_model.c) natively under the address and undefined-behaviour sanitizers, checks that the model also builds for the device
(aarch64, -Werror), and checks the model's layout of the locked scenario against the locked picture os/dock/design/desktop-window.png: every tile the C code places sits on a
tile of the picture and the pixels between tiles are the strip, so a change to a token or to the layout arithmetic that the picture does not show fails here.
No device, no Wayland, no network."""
import os, re, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
DOCK = HERE / 'dock'
WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))
BG = (14, 20, 27)          # WISP_UI_BG, the colour of a tile's well
STRIP_H, PANEL_H = 44, 480


@unittest.skipUnless(shutil.which('gcc'), 'needs a native gcc')
class ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = Path(tempfile.mkdtemp(prefix='wisp-dockmodel-', dir=WORK / 'tmp'))
        cls.exe = cls.d / 'test_model'
        r = subprocess.run(['gcc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=undefined',
                            '-o', str(cls.exe), str(DOCK / 'model.c'), str(DOCK / 'test_model.c')], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        run = subprocess.run([str(cls.exe)], capture_output=True, text=True, env={**os.environ, 'TMPDIR': str(cls.d)})
        cls.proc, cls.out = run, run.stdout

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, True)

    def test_all_checks_pass_with_no_sanitizer_report(self):
        self.assertEqual(self.proc.returncode, 0, self.out[-2000:] + self.proc.stderr[-2000:])
        self.assertRegex(self.out, r'\d{3,} checks, 0 failed')
        self.assertNotIn('runtime error', self.proc.stderr)

    @unittest.skipUnless(shutil.which('aarch64-linux-gnu-gcc'), 'needs the aarch64 cross compiler')
    def test_it_builds_for_the_device_without_warnings(self):
        r = subprocess.run(['aarch64-linux-gnu-gcc', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror', '-c', '-o', os.devnull, str(DOCK / 'model.c')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_the_layout_of_the_locked_scenario_sits_on_the_locked_picture(self):
        items = [l.split() for l in self.out.splitlines() if l.startswith('LAYOUT ')]
        self.assertEqual(len(items), 10)
        img = np.asarray(Image.open(DOCK / 'design/desktop-window.png').convert('RGB'), np.int16)
        top = PANEL_H - STRIP_H
        near = lambda px, ref, tol: all(abs(int(a) - b) <= tol for a, b in zip(px, ref))
        tiles = [(int(i[3]), int(i[4]), int(i[5]), int(i[6])) for i in items if i[1] in ('0', '1', '2', '3')]
        self.assertEqual(len(tiles), 6)                                         # Applications, three pins, the other window, Game mode
        for x, y, w, h in tiles:
            for dx, dy in ((2, 2), (w - 3, 2), (2, h - 3), (w - 3, h - 3), (w // 2, 3)):
                self.assertTrue(near(img[top + y + dy, x + dx], BG, 4), 'tile at %d: pixel (%d,%d) is %s, not the tile well' % (x, dx, dy, img[top + y + dy, x + dx]))
            for gap in (x - 3, x + w + 2):                                       # the strip just beside a tile is not a tile well
                self.assertFalse(near(img[top + y + 12, gap], BG, 8), 'strip expected at x=%d beside the tile at %d' % (gap, x))
        sep = [i for i in items if i[1] == '5']
        self.assertEqual(len(sep), 3)
        status = [i for i in items if i[1] == '4'][0]
        sx = int(status[3])
        self.assertFalse(near(img[top + 20, sx + 2], BG, 6), 'the status group has no tile well')
        for i in sep:                                                             # a divider is a line a little lighter than the strip
            x, y, h = int(i[3]), int(i[4]), int(i[6])
            self.assertGreater(int(img[top + y + h // 2, x].sum()), int(img[top + y + h // 2, x + 6].sum()) + 15, 'divider at %d' % x)


if __name__ == '__main__':
    unittest.main()
