"""The first-run setup program (os/setup/wisp-setup.c) as the image builds it (aarch64), RUN under qemu-user with SDL's dummy video driver and software renderer, and its pictures read back:
 - the list screen equals the committed design picture (os/setup/design/list.png) pixel for pixel, so what is rendered here is what the design shows;
 - the symbols page keeps every key inside the panel (the old layout ran `del` off the screen: shown by building that layout and watching the picture change);
 - the first-run hand-over (`--demo handover:T`): its first frame is the boot screen's picture and its last the setup screen's, and every frame in between equals an INDEPENDENT composition
   (the splash's own render and this program's own list render, mixed by the contract's arithmetic in os/splash/preview_handover.py);
 - the whole program in --intro mode with fakes for nmcli and curl: it plays for three seconds, writes its marker, and does not play again.
Needs what the shell tests need (qemu-user, the built SYSTEM tree with SDL, the pinned SDL headers a rootfs build leaves in the work tree, the aarch64 cross compiler); skipped with the reason."""
import os, shutil, subprocess, sys, tempfile, time, unittest
from pathlib import Path
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'splash'))
WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))
TREE = WORK / 'rootfs/tree'
SDLINC = WORK / 'rootfs/sdlinc'
QEMU = '/usr/bin/qemu-aarch64-static'
SETUP = HERE / 'setup'
NAVY = np.array([0x0e, 0x14, 0x1b])


def why_skipped():
    if not Path(QEMU).exists(): return 'no qemu-user at %s' % QEMU
    if not shutil.which('aarch64-linux-gnu-gcc'): return 'no aarch64 cross compiler'
    if not (TREE / 'usr/lib/libSDL2-2.0.so.0').exists(): return 'no built SYSTEM tree with SDL at %s' % TREE
    if not (SDLINC / 'SDL2/SDL.h').exists(): return 'no pinned SDL headers at %s (a rootfs build leaves them)' % SDLINC
    if not shutil.which('gcc'): return 'no native gcc'
    return None


def sh(cmd, **kw): return subprocess.run(cmd, capture_output=True, text=True, errors='replace', **kw)


def bmp(path): return np.asarray(Image.open(path).convert('RGB'))


@unittest.skipIf(why_skipped(), why_skipped() or '')
class SetupRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
        cls.d = Path(tempfile.mkdtemp(prefix='wispos-setuprender-', dir=WORK / 'tmp'))
        gen = sh(['python3', '-I', str(HERE / 'ui/gen_assets.py'), '--fonts', str(TREE / 'usr/share/fonts/liberation'), '--badge', str(SETUP / 'assets/wispos-badge.png'), '--out', str(cls.d / 'wisp-ui-assets.h')])
        assert gen.returncode == 0, gen.stderr
        cls.bin = cls.build(cls.d / 'wisp-setup', SETUP)
        r = sh(['gcc', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror', '-I' + str(HERE / 'splash'), '-I' + str(HERE / 'ui'), '-I' + str(cls.d), '-o', str(cls.d / 'wisp-splash'),
                str(HERE / 'splash/wisp-splash.c'), '-lm'])
        assert r.returncode == 0, r.stderr

    @classmethod
    def tearDownClass(cls): shutil.rmtree(cls.d, True)

    @classmethod
    def build(cls, out, srcdir):
        lib = str(TREE / 'usr/lib')
        r = sh(['aarch64-linux-gnu-gcc', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror', '-I' + str(SDLINC), '-I' + str(srcdir), '-I' + str(HERE / 'splash'), '-I' + str(HERE / 'ui'), '-I' + str(cls.d),
                '-o', str(out), str(srcdir / 'wisp-setup.c'), str(SETUP / 'bootframe.c'), lib + '/libSDL2-2.0.so.0', lib + '/libSDL2_ttf-2.0.so.0', lib + '/libSDL2_image-2.0.so.0', '-lm', '-lpthread',
                '-Wl,-rpath-link,' + lib])
        assert r.returncode == 0, r.stderr
        return out

    def run_setup(self, binary, *args, env=None, timeout=240):
        cmd = [QEMU, '-cpu', 'cortex-a53', str(TREE / 'usr/lib/ld-linux-aarch64.so.1'), '--library-path', str(TREE / 'usr/lib'), str(binary), '--fonts', str(TREE / 'usr/share/fonts/liberation'),
               '--badge', str(SETUP / 'assets/wispos-badge.png')] + list(args)
        return sh(cmd, env={**os.environ, 'SDL_VIDEODRIVER': 'dummy', 'SDL_AUDIODRIVER': 'dummy', **(env or {})}, timeout=timeout)

    def demo(self, state, binary=None):
        out = self.d / ('demo-%s.bmp' % state.replace(':', '-'))
        r = self.run_setup(binary or self.bin, '--demo', state, '--out', str(out)); self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return bmp(out)

    def splash(self, status, detail, frame0=0):
        fb = self.d / 'splash.fb'; fb.write_bytes(b'')
        r = sh([str(self.d / 'wisp-splash'), '--fb', str(fb), '--geom', '720x480', '--no-vt', '--no-input', '--fifo', str(self.d / 'splash.fifo'), '--fps', '12', '--frames', '1', '--frame0', str(frame0),
                '--status', status, '--detail', detail]); self.assertEqual(r.returncode, 0, r.stderr)
        return np.frombuffer(fb.read_bytes(), dtype=np.uint8).reshape(480, 720, 4)[:, :, [2, 1, 0]].copy()

    def test_the_list_screen_is_the_committed_design_picture_pixel_for_pixel(self):
        want = np.asarray(Image.open(SETUP / 'design/list.png').convert('RGB'))
        self.assertTrue((self.demo('list') == want).all())

    def test_the_large_spinner_sits_left_of_the_bar_in_every_phase_flush_with_it(self):
        # LOCKED by the operator 2026-10-14 (docs/WISPOS-UI-LANGUAGE.md "Large spinner"): in the downloading AND the unpacking phase a 47 px spinner (ring radius 19, 9 px anti-aliased dots)
        # whose top edge is the bar's top (y 170) and whose bottom edge is the bottom of the digits (y 217), left edge on the panel's left edge (x 250); the bar and the number start at
        # x 311 and the bar ends at x 700. Pixel rows 170..216 and columns 250..296 are the 47 px.
        for state in ('download', 'extract'):
            shot = self.demo(state)
            area = shot[150:240, 250:310]
            ink = (area != NAVY).any(axis=2)
            rows = 150 + np.where(ink.any(axis=1))[0]; cols = 250 + np.where(ink.any(axis=0))[0]
            self.assertEqual((int(rows.min()), int(rows.max()), int(cols.min()), int(cols.max())), (170, 216, 250, 296), '%s: the spinner is not the locked 47 px square' % state)
            bar = (shot[175, 300:720] != NAVY).any(axis=1); xs = 300 + np.where(bar)[0]                       # right of the spinner (its dots end at x 296)
            self.assertEqual((int(xs.min()), int(xs.max())), (311, 699), '%s: the bar must run x 311..699 (the spinner takes the left 61 px)' % state)

    def test_the_symbols_page_keeps_every_key_inside_the_panel(self):
        shot = self.demo('symbols')
        self.assertEqual(int((shot[166:382, 701:720] != NAVY).any(axis=2).sum()), 0, 'something is drawn right of the panel in the keyboard band')
        row = shot[166 + 3 * 44:166 + 3 * 44 + 40]                                                                # the last character row: ten keys, centred in the 450 px panel
        keys = (row[:, 250:] != np.array([0x23, 0x26, 0x2e])).any(axis=2) & (row[:, 250:] != NAVY).any(axis=2)      # right of the identity column: not the panel surface and not the ground
        cols = 250 + np.where(keys.any(axis=0))[0]
        self.assertGreaterEqual(int(cols.min()), 250); self.assertLessEqual(int(cols.max()), 699)
        self.assertEqual(tuple(shot[350, 320]), (0, 0, 0))                                                         # the second special key is a black pill: "del" took shift's place

    def test_the_old_layout_would_show_in_the_picture(self):
        # the defect, reproduced: del back in the symbols row. The program built that way draws keys outside the panel, so the test above would fail on it.
        d = self.d / 'old'; shutil.copytree(SETUP, d)
        text = (d / 'kb.h').read_text(); old = 'return row == 3 && !symbols;'; self.assertIn(old, text)
        (d / 'kb.h').write_text(text.replace(old, 'return row == 3 && (symbols || !symbols);'))
        binary = self.build(self.d / 'wisp-setup-old', d)
        shot = self.demo('symbols', binary)
        self.assertGreater(int((shot[166:382, 701:720] != NAVY).any(axis=2).sum()), 100, 'the old layout did not overflow, so the test would not have caught it')

    def test_every_frame_of_the_handover_equals_the_independent_composition(self):
        import preview_handover as P
        setup = self.demo('list'); boot = self.splash('Getting ready for setup', 'This only happens the first time.')
        first = self.demo('handover:0'); last = self.demo('handover:1')
        self.assertTrue((first == boot).all(), 'the first frame is not the boot screen\'s own picture'); self.assertTrue((last == setup).all(), 'the last frame is not the setup screen')
        for t in (0.1, 0.25, 0.5, 0.6, 0.75, 0.9):
            got = self.demo('handover:%s' % t); want = P.mix_arrays(boot, setup, t)
            self.assertTrue((got == want).all(), 't=%s: %d pixels differ from the contract' % (t, int((got != want).any(axis=2).sum())))
        half = self.demo('handover:0.5')                                                                           # at 0.5 both layers are out: only the half-grown ground is on the screen
        self.assertTrue((half == (NAVY * P.smooth(0.5)).astype(np.uint8)).all())

    def run_intro(self, marker, script, timeout=90):
        fakes = SETUP / 'test'; fd = self.d / 'fakes'; shutil.rmtree(fd, True); fd.mkdir()
        for n in ('fake-nmcli', 'fake-curl', 'fake-installer'): shutil.copy(fakes / n, fd / n); (fd / n).chmod(0o755)
        env = {'WISP_NMCLI': str(fd / 'fake-nmcli'), 'WISP_CURL': str(fd / 'fake-curl'), 'WISP_INSTALLER': str(fd / 'fake-installer'), 'WISP_READY': str(fd / 'READY'), 'FAKE_DIR': str(fd)}
        t0 = time.time()
        r = self.run_setup(self.bin, '--intro', str(marker), '--script', script, '--timeout', '60', env=env, timeout=timeout)
        return r, time.time() - t0

    def test_the_program_plays_the_intro_once_and_writes_its_marker(self):
        marker = self.d / 'intro-played'
        if marker.exists(): marker.unlink()
        r, took = self.run_intro(marker, 'wait:4500 quit')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertTrue(marker.exists(), 'the marker was not written when the hand-over ended')
        self.assertGreaterEqual(took, 4.0)
        first = marker.stat().st_mtime; time.sleep(0.01)
        r, took = self.run_intro(marker, 'wait:500 quit')
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr); self.assertEqual(marker.stat().st_mtime, first, 'it played again')

    def test_buttons_pressed_while_the_screen_arrives_do_nothing(self):
        marker = self.d / 'intro-played2'
        if marker.exists(): marker.unlink()
        r, _ = self.run_intro(marker, 'down down down a wait:4500 quit')
        self.assertNotIn('state=password', r.stdout); self.assertNotIn('state=connecting', r.stdout)             # the presses came during the first second: ignored


if __name__ == '__main__':
    unittest.main()
