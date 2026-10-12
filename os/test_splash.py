"""wisp-splash on a file-backed fake framebuffer: the pixels are the contract. It must be the setup screen's design language (docs/WISPOS-UI-LANGUAGE.md): the same
tokens (os/ui/wisp-ui.h), the same components, and a left identity column that matches the setup screen's reference render. No device, no root.
Run: python3 -I os/test_splash.py   (needs gcc, Pillow and the Liberation Sans fonts; WISP_UI_FONTS=DIR overrides the host font directory)"""
import os, re, subprocess, tempfile, time, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / 'splash/wisp-splash.c'
UI = HERE / 'ui'
FONTS = os.environ.get('WISP_UI_FONTS', '/usr/share/fonts/truetype/liberation')
W, H = 720, 480
TMP = os.environ.get('TMPDIR')


def token(name):
    return int(re.search(r'#define\s+%s\s+(0x[0-9a-fA-F]+|\d+)' % name, (UI / 'wisp-ui.h').read_text()).group(1), 0)


BG, TEXT, DIM, PANEL, ACCENT, BAD, GLYPH = (token('WISP_UI_' + n) for n in ('BOOT_BG', 'TEXT', 'DIM', 'PANEL', 'ACCENT', 'BAD', 'GLYPH'))
MID = W // 2
STATUS_Y, DETAIL_Y, NAME_Y, BADGE_Y, BADGE_H = (token('WISP_UI_BOOT_' + n) for n in ('STATUS_Y', 'DETAIL_Y', 'NAME_Y', 'BADGE_Y', 'BADGE_H'))
CX, CY, R = MID, token('WISP_UI_BOOT_SPIN_CY'), token('WISP_UI_SPIN_ROW_R') + 4          # the spinner ring: centred, a little larger than in a row


class Splash(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(prefix='wispos-splash-', dir=TMP))
        gen = subprocess.run(['python3', '-I', str(UI / 'gen_assets.py'), '--fonts', FONTS, '--badge', str(HERE / 'setup/assets/wispos-badge.png'), '--out', str(cls.dir / 'wisp-ui-assets.h')],
                             capture_output=True, text=True)
        assert gen.returncode == 0, gen.stderr
        cls.bin = cls.dir / 'wisp-splash'
        out = subprocess.run(['gcc', '-O2', '-Wall', '-Wextra', '-Werror', '-I', str(UI), '-I', str(cls.dir), '-o', str(cls.bin), str(SRC), '-lm'], capture_output=True, text=True)
        assert out.returncode == 0, out.stderr

    def run_splash(self, *args, frames=None, geom=f'{W}x{H}', fifo_lines=(), fb='fb', timeout=20, fps=12):
        """Run the program; FIFO lines are written once it has created the FIFO (a float is a pause in seconds). Returns (returncode, framebuffer bytes)."""
        fbp = self.dir / fb; fifo = self.dir / (fb + '.fifo')
        fbp.write_bytes(b'')                                                        # the program sizes a regular file itself
        if fifo.exists(): fifo.unlink()
        cmd = [str(self.bin), '--fb', str(fbp), '--geom', geom, '--no-vt', '--no-input', '--fifo', str(fifo), '--fps', str(fps), *args]
        if frames is not None: cmd += ['--frames', str(frames)]
        p = subprocess.Popen(cmd, stderr=subprocess.PIPE)
        if fifo_lines:
            for _ in range(200):
                if fifo.exists(): break
                time.sleep(0.01)
            fd = os.open(fifo, os.O_WRONLY)
            time.sleep(0.2)
            for line in fifo_lines:
                if isinstance(line, float): time.sleep(line); continue
                os.write(fd, (line + '\n').encode()); time.sleep(0.2)
            os.close(fd)
        try: rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            p.kill(); self.fail('wisp-splash did not exit')
        finally: p.stderr.close()
        return rc, fbp.read_bytes()

    @staticmethod
    def px(buf, x, y, w=W): return int.from_bytes(buf[(y * w + x) * 4:(y * w + x) * 4 + 4], 'little') & 0xffffff

    @staticmethod
    def region(buf, x0, y0, x1, y1, w=W): return b''.join(buf[(y * w + x0) * 4:(y * w + x1) * 4] for y in range(y0, y1))

    def any_px(self, buf, x0, y0, x1, y1, pred): return any(pred(self.px(buf, x, y)) for y in range(y0, y1) for x in range(x0, x1))

    # ---------------------------------------------------------------- the language
    def test_the_tokens_are_the_setup_screens_palette(self):
        self.assertEqual((token('WISP_UI_BG'), token('WISP_UI_ACCENT'), token('WISP_UI_BAD'), token('WISP_UI_OK')), (0x0e141b, 0x1a9fff, 0xd94126, 0x59bf40))
        docpath = HERE.parent / 'docs/WISPOS-UI-LANGUAGE.md'
        if not docpath.exists(): self.skipTest('docs/WISPOS-UI-LANGUAGE.md is not part of the public repository')          # the palette values above are already pinned; this only compares them with the document
        doc = docpath.read_text()
        for name in ('BG', 'PANEL', 'LINE', 'DIM', 'DIM2', 'GLYPH', 'ACCENT', 'OK', 'BAD'):
            self.assertIn('#%06x' % token('WISP_UI_' + name), doc, name)              # the document and the header agree
        self.assertIn('#000000', doc)                                                 # the boot ground is documented as the one exception
        setup = (HERE / 'setup/wisp-setup.c').read_text()
        self.assertIn('wisp-ui.h', setup); self.assertNotRegex(setup, r'\{14, 20, 27')  # the setup screen uses the header, no private copy of the palette

    def bbox_x(self, buf, y0, y1):
        xs = [x for y in range(y0, y1) for x in range(W) if self.px(buf, x, y) != BG]
        return (min(xs), max(xs)) if xs else None

    def test_boot_ground_is_black_and_the_column_is_centred(self):
        _, buf = self.run_splash('--status', 'Starting Steam', '--detail', 'This can take a few minutes the first time.', frames=3)
        self.assertEqual(self.px(buf, 3, 3), 0); self.assertEqual(self.px(buf, W - 4, H // 2), 0)    # black, not the setup screen's navy
        for name, (y0, y1) in (('badge', (BADGE_Y, BADGE_Y + BADGE_H)), ('name', (NAME_Y, NAME_Y + 30)), ('status', (STATUS_Y, STATUS_Y + 26)), ('detail', (DETAIL_Y, DETAIL_Y + 20)),
                               ('hint', (452, 472)), ('spinner', (CY - R - 4, CY + R + 4))):
            lo, hi = self.bbox_x(buf, y0, y1)
            self.assertLessEqual(abs(lo - (W - 1 - hi)), 4, '%s is not centred (%d..%d)' % (name, lo, hi))

    # ---------------------------------------------------------------- the screen
    def test_background_and_the_logo(self):
        rc, buf = self.run_splash(frames=1)
        self.assertEqual(rc, 0)
        self.assertEqual(self.px(buf, 3, 3), BG); self.assertEqual(self.px(buf, W - 4, H - 4), BG)
        self.assertTrue(self.any_px(buf, 280, BADGE_Y, 440, BADGE_Y + BADGE_H, lambda c: c != BG))   # the badge
        self.assertTrue(self.any_px(buf, 290, NAME_Y, 430, NAME_Y + 30, lambda c: c == TEXT))      # "WispOS" in Title

    def test_stage_below_the_logo_and_hint_pill(self):
        _, buf = self.run_splash('--status', 'Starting Steam', frames=1)
        self.assertTrue(self.any_px(buf, 100, STATUS_Y, 620, STATUS_Y + 28, lambda c: c == TEXT))   # the current stage, in Body, below the logo
        self.assertTrue(self.any_px(buf, 280, 452, 440, 472, lambda c: c == PANEL))               # the key pill
        self.assertTrue(self.any_px(buf, 280, 452, 440, 472, lambda c: c not in (BG, PANEL)))     # its letters and the label

    def test_spinner_is_the_setup_screens_ring_of_round_dots(self):
        _, f1 = self.run_splash(frames=1, fb='a')
        self.assertEqual(self.px(f1, CX, CY - R), ACCENT)                                          # frame 0: the head dot, at 12 o'clock, full opacity
        self.assertEqual(self.px(f1, CX, CY), BG)                                                  # the ring is empty in the middle
        self.assertEqual(self.px(f1, CX - 3, CY - R - 3), BG)                                      # the dot is round: its corner is not filled
        _, f2 = self.run_splash(frames=2, fb='b')
        box = (CX - 16, CY - 16, CX + 16, CY + 16)
        self.assertNotEqual(self.region(f1, *box), self.region(f2, *box))                          # it moves
        self.assertEqual(self.px(f2, CX + 7, CY - 12), ACCENT)                                     # the head is now the dot at 1 o'clock
        self.assertNotEqual(self.px(f2, CX, CY - R), ACCENT)                                       # and the old head has begun to fade
        self.assertEqual(self.region(f1, 0, 0, W, 200), self.region(f2, 0, 0, W, 200))             # nothing else is redrawn or flickers

    def test_commands_change_the_stage_and_the_detail(self):
        _, a = self.run_splash('--status', 'Network', frames=3, fb='a')
        _, b = self.run_splash(frames=60, fifo_lines=['status Display', 'detail This can take a few minutes the first time.', 'quit'], fb='b')
        self.assertNotEqual(self.region(a, 100, STATUS_Y, 620, STATUS_Y + 28), self.region(b, 100, STATUS_Y, 620, STATUS_Y + 28))
        self.assertTrue(self.any_px(b, 100, DETAIL_Y, 620, DETAIL_Y + 20, lambda c: c != BG))      # the Small dim detail line

    def test_hint_command_changes_the_pill(self):
        _, a = self.run_splash(frames=3, fb='a')
        _, b = self.run_splash(frames=60, fifo_lines=['hint START Skip', 'quit'], fb='b')
        self.assertNotEqual(self.region(a, 250, 452, 470, 472), self.region(b, 250, 452, 470, 472))

    def test_failure_is_the_setup_screens_failure_treatment(self):
        _, buf = self.run_splash(frames=60, fifo_lines=['fail No system image was found on the boot card', 'quit'])
        self.assertEqual(self.px(buf, MID - 12, CY - 2), BAD)                                      # the red disc, where the spinner was
        self.assertTrue(self.any_px(buf, 250, STATUS_Y, 470, STATUS_Y + 40, lambda c: c == TEXT))   # "Boot problem"
        self.assertTrue(self.any_px(buf, 100, DETAIL_Y + 8, 620, DETAIL_Y + 30, lambda c: c != BG))  # the reason in Small dim
        self.assertFalse(self.any_px(buf, CX - 24, CY - 24, CX + 24, CY + 24, lambda c: c == ACCENT))   # a failed boot does not spin
        lo, hi = self.bbox_x(buf, CY - 24, CY + 20); self.assertLessEqual(abs(lo - (W - 1 - hi)), 4)  # and the disc is centred

    def test_a_failure_reveals_the_log_by_itself(self):
        log = self.dir / 'fail.log'
        log.write_text('init: cannot find the system image\ninit: powering off in 5 s\n')
        _, shown = self.run_splash('--log-file', str(log), frames=2000, fifo_lines=['fail Boot problem', 2.6, 'quit'], fb='shown', timeout=40, fps=30)
        self.assertTrue(self.any_px(shown, 0, 0, 300, 20, lambda c: c != BG))                      # the log text is up after 2 s without any button
        self.assertEqual(self.px(shown, MID - 12, CY - 2), BG)                                     # and the failure screen is gone

    def test_log_toggle_replaces_the_screen_and_toggles_back(self):
        log = self.dir / 'boot.log'
        log.write_text('\n'.join('line %d of the kernel log' % i for i in range(60)) + '\n')
        _, on = self.run_splash('--log-file', str(log), frames=120, fifo_lines=['log on', 'quit'], fb='on')
        self.assertTrue(self.any_px(on, 0, 0, 200, 20, lambda c: c != BG))
        self.assertEqual(self.px(on, CX, CY - R), BG)                                              # no spinner in the log view
        _, back = self.run_splash('--log-file', str(log), frames=200, fifo_lines=['log on', 'log toggle', 'quit'], fb='back')
        self.assertTrue(self.any_px(back, CX - 16, CY - 16, CX + 16, CY + 16, lambda c: c != BG))  # the spinner is back
        self.assertTrue(self.any_px(back, 290, NAME_Y, 430, NAME_Y + 30, lambda c: c == TEXT))     # and the logo

    def test_ready_exits_cleanly_and_fast(self):
        t0 = time.time()
        rc, _ = self.run_splash(fifo_lines=['status Almost ready', 'ready'], timeout=5)
        self.assertEqual(rc, 0); self.assertLess(time.time() - t0, 4)

    def test_long_text_is_cut_inside_the_panel(self):
        _, buf = self.run_splash('--status', 'x' * 300, frames=2)
        lo, hi = self.bbox_x(buf, STATUS_Y, STATUS_Y + 28)
        self.assertGreaterEqual(lo, 20); self.assertLessEqual(hi, W - 21)                        # cut with "..." inside the margins

    def test_dev_builds_say_so_in_red(self):
        _, buf = self.run_splash('--dev', frames=1)
        self.assertTrue(self.any_px(buf, 290, token('WISP_UI_BOOT_DEVMARK_Y'), 430, token('WISP_UI_BOOT_DEVMARK_Y') + 18, lambda c: c != BG and (c >> 16) > 0x80 and (c & 0xff) < 0x60))

    def test_tiny_canvas_never_writes_outside_the_buffer(self):
        rc, buf = self.run_splash('--status', 'a long status line on a tiny screen', frames=5, geom='64x64', fb='tiny')
        self.assertEqual(rc, 0); self.assertEqual(len(buf), 64 * 64 * 4)

    def test_bad_arguments_are_refused(self):
        for args in (['--bogus'], ['--fb']):
            self.assertEqual(subprocess.run([str(self.bin), *args], capture_output=True).returncode, 2)
        self.assertEqual(subprocess.run([str(self.bin), '--fb', str(self.dir / 'regular'), '--no-vt', '--no-input'], capture_output=True).returncode, 1)   # a regular file needs --geom

    def test_preview_png(self):                                                                  # for a human look; only when asked for
        target = os.environ.get('WISP_SPLASH_PREVIEW')
        if not target: self.skipTest('set WISP_SPLASH_PREVIEW=/path/to/dir to write PNG previews')
        from PIL import Image
        for name, args, lines, frames in (('splash', ['--status', 'Starting Steam', '--detail', 'This can take a few minutes the first time.'], (), 6),
                                          ('fail', [], ['fail No system image was found on the boot card', 'quit'], 80), ('dev', ['--dev', '--status', 'Starting the display'], (), 6)):
            _, buf = self.run_splash(*args, frames=None if lines else frames, fifo_lines=lines, fb='p_' + name)
            Image.frombuffer('RGBA', (W, H), buf, 'raw', 'BGRA', 0, 1).convert('RGB').save(os.path.join(target, name + '.png'))


if __name__ == '__main__':
    unittest.main()
