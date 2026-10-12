"""Runs the shell renderer's C tests (os/dock/test_render.c: shapes, blending, text placement against Pillow's numbers, images, the PNG reader's limits) natively under the address and
undefined-behaviour sanitizers, and checks the renderer builds for the device. The picture comparison against the locked design is test_dock_view.py. No device, no Wayland."""
import os, shutil, subprocess, tempfile, unittest
from pathlib import Path
from PIL import Image

HERE = Path(__file__).resolve().parent
DOCK = HERE / 'dock'
WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))
FONT_DIRS = ('/usr/share/fonts/truetype/liberation', '/usr/share/fonts/liberation')
INC = ['-I/usr/include/freetype2']


@unittest.skipUnless(shutil.which('gcc'), 'needs a native gcc')
class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fonts = next((d for d in FONT_DIRS if (Path(d) / 'LiberationSans-Regular.ttf').exists()), None)
        assert fonts, 'no Liberation fonts on the host'
        cls.d = Path(tempfile.mkdtemp(prefix='wisp-render-', dir=WORK / 'tmp'))
        tiny = Image.new('RGBA', (2, 2)); tiny.putdata([(255, 0, 0, 255), (0, 255, 0, 128), (0, 0, 0, 0), (0, 0, 255, 255)]); tiny.save(cls.d / 'tiny.png')
        Image.new('RGBA', (1025, 1), (1, 2, 3, 4)).save(cls.d / 'wide.png')
        (cls.d / 'garbage.png').write_bytes(bytes(range(256)) * 8)
        cls.exe = cls.d / 'test_render'
        r = subprocess.run(['gcc', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined', '-fno-sanitize-recover=undefined'] + INC +
                           ['-o', str(cls.exe), str(DOCK / 'render.c'), str(DOCK / 'test_render.c'), '-lfreetype', '-lpng', '-lm'], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        cls.run_ = subprocess.run([str(cls.exe), fonts, str(cls.d / 'tiny.png'), str(cls.d / 'wide.png'), str(cls.d / 'garbage.png')], capture_output=True, text=True, timeout=300)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, True)

    def test_all_checks_pass_with_no_sanitizer_report(self):
        self.assertEqual(self.run_.returncode, 0, self.run_.stdout[-3000:] + self.run_.stderr[-3000:])
        self.assertRegex(self.run_.stdout, r'\d{2,} checks, 0 failed')
        self.assertNotIn('runtime error', self.run_.stderr)

    def test_the_text_anchors_are_the_numbers_pillow_gives(self):
        """The C test hard-codes the rows of 'H' for each anchor; this is where they come from."""
        from PIL import ImageDraw, ImageFont
        import numpy as np
        d = next(d for d in FONT_DIRS if (Path(d) / 'LiberationSans-Regular.ttf').exists())
        f = ImageFont.truetype(d + '/LiberationSans-Regular.ttf', 18, layout_engine=ImageFont.Layout.BASIC)
        self.assertEqual(f.getmetrics(), (17, 4)); self.assertEqual(f.getlength('H'), 13.0)
        got = {}
        for anchor in ('la', 'lm', 'ls'):
            im = Image.new('L', (100, 100), 0); ImageDraw.Draw(im).text((50, 50), 'H', font=f, fill=255, anchor=anchor)
            ys = np.nonzero(np.asarray(im) > 128)[0]; got[anchor] = (int(ys.min()) - 50, int(ys.max()) + 1 - 50)
        self.assertEqual(got, {'la': (5, 17), 'lm': (-5, 7), 'ls': (-12, 0)})

    @unittest.skipUnless(shutil.which('aarch64-linux-gnu-gcc'), 'needs the aarch64 cross compiler')
    def test_it_builds_for_the_device_without_warnings(self):
        # the device build uses pinned headers of FreeType and libpng (os/dock/fetch-headers.sh); here the host's, only to check the source
        r = subprocess.run(['gcc', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror', '-fsyntax-only'] + INC + [str(DOCK / 'render.c')], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == '__main__':
    unittest.main()
