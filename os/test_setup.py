"""Tests for the first-run Wi-Fi screen (os/setup). The nmcli parsing, failure mapping, internet check and process runner
(nm.h) are compiled natively and run here; the screen's flows run in the emulated VM (os/setup/flow-test.sh)."""
import os, shutil, subprocess, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SETUP = HERE / 'setup'


class NetworkBackendTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('gcc'), 'needs a native gcc')
    def test_nm_header_unit_tests_pass(self):
        with tempfile.TemporaryDirectory(prefix='wispos-nm-', dir=os.environ.get('TMPDIR')) as d:
            exe = Path(d) / 'test_nm'
            subprocess.run(['gcc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-o', str(exe), str(SETUP / 'test_nm.c')], check=True)
            out = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertIn('all nm.h tests passed', out.stdout)


class KeyboardLayoutTests(unittest.TestCase):
    """os/setup/kb.h: which keys each page has and how wide each row is. The symbols page once put ten symbols and `del` in one row: 504 px in a 450 px panel, `del` off the screen."""
    def build(self, d, header=None):
        src = Path(d) / 'src'; src.mkdir(); (src / 'ui').mkdir(); shutil.copy(HERE / 'ui/wisp-ui.h', src / 'ui'); (src / 'setup').mkdir()
        shutil.copy(SETUP / 'test_kb.c', src / 'setup'); (src / 'setup/kb.h').write_text(header if header is not None else (SETUP / 'kb.h').read_text())
        exe = src / 'test_kb'; subprocess.run(['gcc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-o', str(exe), str(src / 'setup/test_kb.c')], check=True, capture_output=True)
        return subprocess.run([str(exe)], capture_output=True, text=True)

    @unittest.skipUnless(shutil.which('gcc'), 'needs a native gcc')
    def test_every_page_fits_the_panel_and_every_key_can_be_reached(self):
        with tempfile.TemporaryDirectory(prefix='wispos-kb-', dir=os.environ.get('TMPDIR')) as d:
            r = self.build(d); self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertIn('all kb.h tests passed', r.stdout)

    @unittest.skipUnless(shutil.which('gcc'), 'needs a native gcc')
    def test_the_old_layout_is_caught_and_so_are_other_ways_to_break_it(self):
        text = (SETUP / 'kb.h').read_text()
        mutants = [('del back in the symbols row (the defect)', 'return row == 3 && !symbols;', 'return row == 3 && (symbols || !symbols);', 'in a 450 px panel'),
                   ('a twelfth symbol in a full row', '"-_=+[]{};:`"', '"-_=+[]{};:`~"', 'FAIL'),
                   ('the tilde missing again', '"!@#$%^&*()~"', '"!@#$%^&*()"', 'cannot be typed'),
                   ('the grave accent missing again', '"-_=+[]{};:`"', '"-_=+[]{};:"', 'cannot be typed'),
                   ('full-size keys on the symbols page', '#define KB_KEY_W_SYM 36', '#define KB_KEY_W_SYM 40', 'FAIL'),
                   ('a special key too wide', '{54, 66, 118, 70, 114}', '{54, 66, 118, 70, 214}', 'special keys'),
                   ('the digit row shortened', '"1234567890", "!@#$%^&*()~"', '"123456789", "!@#$%^&*()~"', 'digit row'),
                   ('a wider gap', '#define KB_GAP 4', '#define KB_GAP 6', 'FAIL')]
        with tempfile.TemporaryDirectory(prefix='wispos-kb-', dir=os.environ.get('TMPDIR')) as d:
            for n, (what, old, new, expect) in enumerate(mutants):
                self.assertEqual(text.count(old), 1, what)
                sub = Path(d) / str(n); sub.mkdir()
                r = self.build(sub, text.replace(old, new)); self.assertNotEqual(r.returncode, 0, what + ': ' + r.stdout); self.assertIn(expect, r.stdout, what)

    @unittest.skipUnless(shutil.which('aarch64-linux-gnu-gcc') and (Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007')) / 'rootfs/sdlinc/SDL2/SDL.h').exists(),
                         'needs the aarch64 cross compiler and the pinned SDL headers a rootfs build leaves in the work tree')
    def test_the_setup_screen_compiles_as_the_image_build_compiles_it(self):
        # the image build uses -O2 -Werror: the optimiser's range warnings (an index it cannot prove is in range) only appear there, not in a syntax-only or -O0 compile
        work = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))
        with tempfile.TemporaryDirectory(prefix='wispos-setupcc-', dir=os.environ.get('TMPDIR')) as d:
            r = subprocess.run(['aarch64-linux-gnu-gcc', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror', '-I', str(work / 'rootfs/sdlinc'), '-c', '-o', str(Path(d) / 'setup.o'), str(SETUP / 'wisp-setup.c')],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-1500:])

    def test_the_setup_screen_uses_the_shared_layout_and_clamps_the_cursor_when_the_page_changes(self):
        c = (SETUP / 'wisp-setup.c').read_text()
        self.assertIn('#include "kb.h"', c)
        for gone in ('ROWS_LOWER', 'ROWS_UPPER', 'ROWS_SYM'): self.assertNotIn(gone, c)                      # one place for the rows
        self.assertGreaterEqual(c.count('clamp_key(a)'), 2)                                                     # after Y and after the 123/abc key: the symbols row is longer than the letters row
        self.assertIn('if (!a->symbols) a->shift = !a->shift', c)                                               # shift means nothing on the symbols page


class HandoverTests(unittest.TestCase):
    """The first-run hand-over (os/setup/handover.h, bootframe.c, wisp-setup --intro): the arithmetic against the contract (os/splash/preview_handover.py) bit for bit, the endpoints, and
    the real program's frames against an independent composition (the setup program's own render and the splash's render, mixed by the contract)."""
    NAVY = (0x0e, 0x14, 0x1b)

    @classmethod
    def setUpClass(cls):
        import sys
        sys.path.insert(0, str(HERE / 'splash'))
        import preview_handover
        cls.P = preview_handover

    def c_mix(self, d, cases):
        exe = Path(d) / 'test_handover'
        subprocess.run(['gcc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-I', str(SETUP), '-o', str(exe), str(SETUP / 'test_handover.c')], check=True, capture_output=True)
        text = ''.join('%.17g %06x %06x\n' % (t, b, s) for t, b, s in cases)
        out = subprocess.run([str(exe)], input=text, capture_output=True, text=True, check=True).stdout.split()
        return [int(x, 16) for x in out]

    def py_mix(self, t, b, s):
        import numpy as np
        px = lambda v: np.array([[[(v >> 16) & 255, (v >> 8) & 255, v & 255]]], dtype=np.uint8)
        o = self.P.mix_arrays(px(b), px(s), t)[0, 0]
        return (int(o[0]) << 16) | (int(o[1]) << 8) | int(o[2])

    @unittest.skipUnless(shutil.which('gcc'), 'needs a native gcc')
    def test_the_c_arithmetic_equals_the_contracts_bit_for_bit(self):
        import random
        rnd = random.Random(20261012); navy = 0x0e141b
        cases = [(t / 20, rnd.randrange(1 << 24), rnd.randrange(1 << 24)) for t in range(21) for _ in range(150)]
        cases += [(rnd.random(), rnd.randrange(1 << 24), (navy & 0xff0000) | rnd.randrange(1 << 16)) for _ in range(500)]
        cases += [(t / 20, 0xffffff, 0xffffff) for t in range(21)] + [(t / 20, 0x000000, 0x000000) for t in range(21)]          # saturation and the darkest setup pixel
        with tempfile.TemporaryDirectory(prefix='wispos-ho-', dir=os.environ.get('TMPDIR')) as d: got = self.c_mix(d, cases)
        bad = [(c, hex(g), hex(self.py_mix(*c))) for c, g in zip(cases, got) if g != self.py_mix(*c)]
        self.assertEqual(bad[:5], [], '%d of %d pixels differ' % (len(bad), len(cases)))

    @unittest.skipUnless(shutil.which('gcc'), 'needs a native gcc')
    def test_the_first_frame_is_the_boot_screen_and_the_last_is_the_setup_screen_exactly(self):
        import random
        rnd = random.Random(7)
        cases = [(0.0, b, s) for b, s in ((rnd.randrange(1 << 24), rnd.randrange(1 << 24)) for _ in range(300))] + [(1.0, b, s) for b, s in ((rnd.randrange(1 << 24), rnd.randrange(1 << 24)) for _ in range(300))]
        with tempfile.TemporaryDirectory(prefix='wispos-ho-', dir=os.environ.get('TMPDIR')) as d: got = self.c_mix(d, cases)
        for (t, b, s), g in zip(cases, got):
            self.assertEqual(g, b if t == 0 else s, (t, hex(b), hex(s)))                                      # t=0: the boot pixel; t=1: the setup pixel, even when it is darker than the navy ground

    @unittest.skipUnless(shutil.which('gcc'), 'needs a native gcc')
    def test_the_ground_and_the_two_layers_follow_the_curves(self):
        P = self.P
        with tempfile.TemporaryDirectory(prefix='wispos-ho-', dir=os.environ.get('TMPDIR')) as d:
            got = self.c_mix(d, [(t / 10, 0x000000, 0x0e141b) for t in range(11)])                              # no elements on either side: only the ground shows
        for t, g in zip(range(11), got):
            want = tuple(int(c * P.smooth(t / 10)) for c in self.NAVY); self.assertEqual(((g >> 16) & 255, (g >> 8) & 255, g & 255), want, t)
        self.assertEqual(P.levels(0.5)[1:], (0.0, 0.0))                                                          # at t = 0.5 both layers are out: fade out THEN fade in, never a cross-fade
        for x in (0.05, 0.2, 0.4): self.assertEqual(P.levels(x)[2], 0.0 if x <= 0.5 else P.levels(x)[2]); self.assertGreater(P.levels(x)[1], 0.0)
        for x in (0.6, 0.8, 0.95): self.assertEqual(P.levels(x)[1], 0.0); self.assertGreater(P.levels(x)[2], 0.0)

    def test_the_marker_makes_it_play_once_and_the_unit_asks_for_it(self):
        c = (SETUP / 'wisp-setup.c').read_text()
        self.assertIn('if (intro && access(intro, F_OK) != 0)', c)                                              # no marker: it plays
        self.assertIn('fputs("played\\n", f)', c)                                                                # and writes the marker when it is over
        self.assertIn('O_WRONLY | O_NONBLOCK', c)                                                               # the boot screen's FIFO is opened without ever blocking on a missing reader
        unit = (HERE / 'rootfs/usr/lib/systemd/system/wisp-setup.service').read_text()
        self.assertIn('--intro /storage/.config/wispos/intro-played', unit)

    def test_nothing_in_the_screen_can_be_pressed_while_it_arrives(self):
        c = (SETUP / 'wisp-setup.c').read_text()
        self.assertIn('if (HO && HO->active) return;', c.split('static void press(App *a, Btn b) {', 1)[1][:200])                  # at the top of press(): every way a button arrives (pad, keys, scripts)


class SetupScreenSourceTests(unittest.TestCase):
    TEXT = (SETUP / 'wisp-setup.c').read_text()

    def test_no_shell_is_ever_used_for_network_commands(self):
        for forbidden in ('system(', 'popen('):
            self.assertNotIn(forbidden, self.TEXT + (SETUP / 'nm.h').read_text())

    def test_the_screen_cannot_be_closed_or_skipped(self):
        self.assertIn('a setup screen is not closable', self.TEXT)
        self.assertNotIn('skip', self.TEXT.lower().replace('skipped', ''))

    def test_it_exits_zero_only_when_steam_is_reachable(self):
        self.assertIn('already online', self.TEXT)
        self.assertIn('nm_steam_reply_ok', self.TEXT)

    def test_palette_is_steams_own(self):
        # The palette lives in the shared design tokens (os/ui/wisp-ui.h, docs/WISPOS-UI-LANGUAGE.md); this screen must use them, not keep a copy.
        header = (HERE / 'ui/wisp-ui.h').read_text()
        for name, value in (('BG', '0x0e141b'), ('PANEL', '0x23262e'), ('ACCENT', '0x1a9fff'), ('DIM', '0x8b929a')):
            self.assertRegex(header, r'#define\s+WISP_UI_%s\s+%s\b' % (name, value))
        for token in ('WISP_UI_BG', 'WISP_UI_PANEL', 'WISP_UI_ACCENT', 'WISP_UI_DIM'):
            self.assertIn(token, self.TEXT)
        self.assertIn('wisp-ui.h', self.TEXT)

    def test_unit_runs_before_steam_and_never_gives_up(self):
        unit = (HERE / 'rootfs/usr/lib/systemd/system/wisp-setup.service').read_text()
        self.assertIn('Before=wisp-steam.service', unit)
        self.assertIn('TimeoutStartSec=infinity', unit)
        self.assertIn('Restart=on-failure', unit)
        self.assertIn('wisp-setup.service', (HERE / 'rootfs/usr/lib/systemd/system/wisp-steam.service').read_text())
        self.assertTrue((HERE / 'rootfs/usr/lib/systemd/system/wispos.target.wants/wisp-setup.service').exists())

    def test_sdl_headers_are_hash_pinned_in_the_build(self):
        build = (HERE / 'build-rootfs.sh').read_text()
        for digest in ('5f5993c530f084535c65a6879e9b26ad441169b3e25d789d83287040a9ca5165',
                       '9dc71ed93487521b107a2c4a9ca6bf43fb62f6bddd5c26b055e6b91418a22053',
                       '8f486bbfbcf8464dd58c9e5d93394ab0255ce68b51c5a966a918244820a76ddc'):
            self.assertIn(digest, build)
        self.assertIn('-Werror', build)


if __name__ == '__main__':
    unittest.main()
