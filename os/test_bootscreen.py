"""The Wayland boot screen (os/splash/wisp-bootscreen.c) against a REAL compositor: the image's own sway 1.11 (aarch64, under qemu-user, headless, 720 x 480), the same rig the desktop
shell is tested with (os/test_dock_shell.py). What is proved: it draws THE SAME SCREEN the framebuffer splash draws (every pixel outside the spinner equals the splash's own render of the
same state: the drawing code is shared, os/splash/scene.h); it is a full-output overlay that moves no window and takes no input; it draws at the output's native pixel size when sway runs
the panel at scale 2; status lines, the failure screen and the boot log arrive over its FIFO; and, because it must never be able to cover the real UI, every way out works: a window
appearing, `done`, SIGTERM, a ceiling in seconds, no display, `wisp.splash=off`. The device build (aarch64, as os/build-rootfs.sh compiles it) runs too.
Needs what the shell tests need (qemu-user, the built SYSTEM tree with sway and grim, the host's libwayland and wayland-scanner, Liberation fonts); skipped with the reason otherwise."""
import json, os, shutil, signal, socket, subprocess, sys, tempfile, time, unittest
from pathlib import Path
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import test_dock_shell as T

SPLASH = HERE / 'splash'
UI = HERE / 'ui'
WORK, TREE, FONTS = T.WORK, T.TREE, T.FONTS
W, H = 720, 480
SRC = Path(os.environ.get('WISP_BOOTSCREEN_C', SPLASH / 'wisp-bootscreen.c'))      # the override is how the mutant check below runs these tests against a changed copy


def token(name):
    import re
    return int(re.search(r'#define\s+%s\s+(0x[0-9a-fA-F]+|\d+)' % name, (UI / 'wisp-ui.h').read_text()).group(1), 0)


CX, CY = W // 2, token('WISP_UI_BOOT_SPIN_CY')
BOX = token('WISP_UI_SPIN_ROW_R') + 4 + token('WISP_UI_SPIN_DOT_R') + 1                      # the spinner's bounding box, as the program damages it
BG = token('WISP_UI_BOOT_BG')


class BootRig(T.Rig):
    """The shell tests' rig without the shell: a headless sway (at the given output scale), screenshots, and the programs under test."""
    def __init__(self, scale=1, res='720x480'):
        super().__init__(); self.scale = scale; self.res = res; self.notify = None

    def start_sway(self):
        conf = self.d / 'sway.conf'
        conf.write_text('output HEADLESS-1 resolution %s position 0 0 scale %d\noutput * bg #0e141b solid_color\ndefault_border none\ndefault_floating_border none\nfocus_follows_mouse no\nxwayland disable\n' % (self.res, self.scale))
        env = {**self.env, 'WLR_BACKENDS': 'headless', 'WLR_RENDERER': 'pixman', 'WLR_LIBINPUT_NO_DEVICES': '1', 'LIBSEAT_BACKEND': 'noop'}
        self.sway_log = (self.d / 'sway.log').open('w')
        self.sway = subprocess.Popen(self.run_aarch64('sway', '-c', str(conf)), env=env, stdout=self.sway_log, stderr=subprocess.STDOUT, start_new_session=True)
        self.env['SWAYSOCK'] = str(self.rt / ('sway-ipc.%d.sock' % os.getuid()))
        self.wait(lambda: (self.rt / 'wayland-1').exists() and self.swaymsg('-t', 'get_version').returncode == 0, 120, 'sway to start (see %s)' % (self.d / 'sway.log'))

    def build_programs(self):
        r = T.sh(['bash', str(T.DOCK / 'prepare-build.sh'), str(WORK)]); assert r.returncode == 0, r.stderr
        gen, di = WORK / 'dockgen', WORK / 'dockinc'
        assets = self.d / 'assets'; assets.mkdir()
        r = T.sh(['python3', '-I', str(UI / 'gen_assets.py'), '--fonts', FONTS, '--badge', str(HERE / 'setup/assets/wispos-badge.png'), '--out', str(assets / 'wisp-ui-assets.h')]); assert r.returncode == 0, r.stderr
        P = lambda n: str(gen / (n + '-protocol.c'))
        protos = [P('wlr-layer-shell-unstable-v1'), P('wlr-foreign-toplevel-management-unstable-v1'), P('xdg-shell')]
        inc = ['-I' + str(gen), '-I' + str(di), '-I' + str(SPLASH), '-I' + str(UI), '-I' + str(assets)]
        r = T.sh(['gcc', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined', '-fno-sanitize-recover=undefined'] + inc + ['-o', str(self.bin / 'wisp-bootscreen'),
                  str(SRC)] + protos + ['-lwayland-client', '-lm']); assert r.returncode == 0, r.stderr
        r = T.sh(['gcc', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror', '-I' + str(SPLASH), '-I' + str(UI), '-I' + str(assets), '-o', str(self.bin / 'wisp-splash'), str(SPLASH / 'wisp-splash.c'), '-lm'])
        assert r.returncode == 0, r.stderr
        r = T.sh(['gcc', '-std=gnu11', '-O1', '-g', '-Wall', '-Wextra', '-Werror', '-I' + str(gen), '-o', str(self.bin / 'test_client'), str(T.DOCK / 'test_client.c'), P('xdg-shell'), '-lwayland-client'])
        assert r.returncode == 0, r.stderr
        self.device_build = None
        if shutil.which('aarch64-linux-gnu-gcc'):
            lib = str(TREE / 'usr/lib')
            r = T.sh(['aarch64-linux-gnu-gcc', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror'] + inc + ['-o', str(self.bin / 'wisp-bootscreen-aarch64'), str(SRC)] + protos
                     + [lib + '/libwayland-client.so.0', '-lm', '-Wl,-rpath-link,' + lib]); assert r.returncode == 0, r.stderr
            self.device_build = self.bin / 'wisp-bootscreen-aarch64'

    # ------------------------------------------------------------------ the program
    def start(self, *args, device=False, fifo=True, env=None, scale_arg=None):
        """Start the boot screen; returns the Popen. READY=1 arrives on self.notify."""
        if self.notify is None:
            path = str(self.rt / 'notify.sock'); self.notify = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM); self.notify.bind(path); self.notify.settimeout(1.0); self.notify_path = path
        while True:                                                                               # drop notifications left over from an earlier start
            try: self.notify.recv(256)
            except OSError: break
        self.fifo = self.rt / 'bs.fifo'
        if self.fifo.exists(): self.fifo.unlink()
        cmd = (self.run_aarch64_file(self.device_build) if device else [str(self.bin / 'wisp-bootscreen')])
        cmd += ['--fifo', str(self.fifo), '--splash-fifo', str(self.rt / 'no-such-splash.fifo'), '--cmdline', str(self.d / 'cmdline'), '--no-input'] + list(args)
        (self.d / 'cmdline').write_text('boot=LABEL=ROCKNIX quiet\n') if not (self.d / 'cmdline').exists() else None
        if getattr(self, 'bs_log', None) and not self.bs_log.closed: self.bs_log.close()
        self.bs_log = (self.d / 'bootscreen.log').open('w')
        e = {**self.env, 'NOTIFY_SOCKET': self.notify_path, **(env or {})}
        self.bs = subprocess.Popen(cmd, env=e, stdout=self.bs_log, stderr=subprocess.STDOUT)
        return self.bs

    def ready(self, timeout=120):
        end = time.time() + timeout
        while time.time() < end:
            try:
                if b'READY=1' in self.notify.recv(256): return True
            except OSError: pass
            if self.bs.poll() is not None: return False
        return False

    def say(self, line):
        fd = os.open(str(self.fifo), os.O_RDWR | os.O_NONBLOCK)
        try: os.write(fd, (line + '\n').encode())
        finally: os.close(fd)

    def stop_bs(self):
        if getattr(self, 'bs', None) and self.bs.poll() is None:
            self.bs.terminate()
            try: self.bs.wait(15)
            except subprocess.TimeoutExpired: self.bs.kill()
        if getattr(self, 'bs_log', None): self.bs_log.close()
        self.bs = None

    def stop(self):
        if self.notify is not None: self.notify.close(); self.notify = None
        super().stop()

    def logtext(self): return (self.d / 'bootscreen.log').read_text(errors='replace')

    def splash_render(self, *args, frame0=0):
        """The framebuffer splash's own render of a state: (H, W, 3) uint8 RGB."""
        fb = self.d / 'splash.fb'; fb.write_bytes(b'')
        r = T.sh([str(self.bin / 'wisp-splash'), '--fb', str(fb), '--geom', '%dx%d' % (W, H), '--no-vt', '--no-input', '--fifo', str(self.d / 'splash.fifo'), '--fps', '12', '--frames', '1', '--frame0', str(frame0)] + list(args))
        assert r.returncode == 0, r.stderr
        a = np.frombuffer(fb.read_bytes(), dtype=np.uint8).reshape(H, W, 4)
        return a[:, :, [2, 1, 0]].copy()                                                          # BGRA to RGB

    def shot(self): return self.grim()


def outside_spinner(a):
    m = np.ones(a.shape[:2], dtype=bool); m[CY - BOX:CY + BOX + 1, CX - BOX:CX + BOX + 1] = False
    return a[m]


@unittest.skipIf(T.why_skipped(), T.why_skipped() or '')
class BootScreenTests(unittest.TestCase):
    rig = None
    SCALE = 1

    @classmethod
    def setUpClass(cls):
        cls.rig = BootRig(cls.SCALE)
        try: cls.rig.build_programs(); cls.rig.start_sway()
        except BaseException: cls.rig.stop(); raise

    @classmethod
    def tearDownClass(cls):
        if cls.rig: cls.rig.stop_bs(); cls.rig.stop()

    def tearDown(self):
        self.rig.stop_bs()
        for n in ('cmdline',): (self.rig.d / n).unlink(missing_ok=True)

    def test_it_draws_the_screen_the_splash_draws_and_covers_the_whole_output(self):
        r = self.rig; r.start('--status', 'Starting the display', '--detail', 'Hello', '--keep-on-window')
        self.assertTrue(r.ready(), r.logtext())
        time.sleep(0.5)
        shot = r.shot(); want = r.splash_render('--status', 'Starting the display', '--detail', 'Hello')
        self.assertEqual(shot.shape, (H, W, 3))
        self.assertTrue((outside_spinner(shot) == outside_spinner(want)).all(), 'the pixels outside the spinner differ from the splash\'s render (%d of %d)' % ((outside_spinner(shot) != outside_spinner(want)).any(axis=1).sum(), outside_spinner(want).shape[0]))
        ring = shot[CY - BOX:CY + BOX, CX - BOX:CX + BOX]; bg = np.array([(BG >> 16) & 255, (BG >> 8) & 255, BG & 255])
        self.assertTrue((np.abs(ring.astype(int) - bg).sum(axis=2) > 60).sum() > 100, 'the spinner ring is not there')           # blue dots on black

    def test_the_spinner_turns(self):
        r = self.rig; r.start('--keep-on-window', '--fps', '8')
        self.assertTrue(r.ready(), r.logtext())
        seen = set()
        for _ in range(6):
            seen.add(r.shot()[CY - BOX:CY + BOX, CX - BOX:CX + BOX].tobytes()); time.sleep(0.2)
        self.assertGreaterEqual(len(seen), 3, 'the ring did not change between screenshots')

    def test_status_lines_arrive_over_the_fifo_and_change_only_what_they_name(self):
        r = self.rig; r.start('--status', 'First', '--keep-on-window'); self.assertTrue(r.ready(), r.logtext())
        r.say('status Starting Steam'); r.say('detail This can take a while'); time.sleep(1.5)
        want = r.splash_render('--status', 'Starting Steam', '--detail', 'This can take a while'); shot = r.shot()
        self.assertTrue((outside_spinner(shot) == outside_spinner(want)).all())

    def test_a_failure_shows_the_red_screen_and_opens_the_log_by_itself(self):
        r = self.rig; log = r.d / 'boot.log'; log.write_text('\n'.join('[%5d.000] line number %d of the boot log' % (i, i) for i in range(70)) + '\n')
        r.start('--keep-on-window', '--log-file', str(log)); self.assertTrue(r.ready(), r.logtext())
        r.say('fail The display did not start'); time.sleep(1.0)
        shot = r.shot()
        red = ((shot[:, :, 0] > 200) & (shot[:, :, 1] < 80) & (shot[:, :, 2] < 80)).sum()
        self.assertGreater(red, 200, 'no red failure disc')                                           # the setup screen's red "!"
        time.sleep(2.0)
        after = r.shot()                                                                              # two seconds later the log has replaced the screen
        self.assertFalse((after == shot).all()); self.assertLess(((after[:, :, 0] > 200) & (after[:, :, 1] < 80)).sum(), 50)
        self.assertGreater((after.sum(axis=2) > 300).sum(), 500, 'the log text is not on the screen')

    def test_the_log_toggle_shows_the_last_lines_of_the_log_and_goes_back(self):
        r = self.rig; log = r.d / 'boot.log'; log.write_text('first\nsecond line\nthird line of the log\n')
        r.start('--keep-on-window', '--log-file', str(log), '--status', 'Starting'); self.assertTrue(r.ready(), r.logtext())
        time.sleep(0.3); splash = r.shot()
        r.say('log on'); time.sleep(1.5); view = r.shot()
        self.assertGreater((view.sum(axis=2) > 300).sum(), 80); self.assertFalse((outside_spinner(view) == outside_spinner(splash)).all())
        r.say('log off'); time.sleep(1.5); back = r.shot()
        self.assertTrue((outside_spinner(back) == outside_spinner(splash)).all(), 'the boot screen did not come back')

    def test_it_ends_the_moment_a_window_appears_and_the_window_is_visible(self):
        r = self.rig; r.start('--status', 'Starting Steam'); self.assertTrue(r.ready(), r.logtext())
        c = T.Client(r, 'steam', color='2a3b4c')
        try:
            end = time.time() + 60
            while time.time() < end and r.bs.poll() is None: time.sleep(0.25)
            self.assertEqual(r.bs.poll(), 0, 'the boot screen did not end when a window appeared\n' + r.logtext())
            self.assertIn('a window appeared', r.logtext())
            time.sleep(1.0); shot = r.shot()
            self.assertTrue((np.abs(shot[100:300, 100:300].astype(int) - np.array([0x2a, 0x3b, 0x4c])).sum(axis=2) < 6).mean() > 0.9, 'the window is not what is on the screen')
        finally: c.stop()

    def test_an_overlay_moves_no_window_and_takes_no_input(self):
        r = self.rig; c = T.Client(r, 'underneath', color='3a2b1c')
        try:
            r.wait(lambda: r.window('underneath'), 60, 'the test window')
            before = r.window('underneath')['rect']
            r.start('--keep-on-window'); self.assertTrue(r.ready(), r.logtext())
            self.assertEqual(r.window('underneath')['rect'], before)                                   # exclusive zone -1: nothing is moved
            self.assertEqual(r.swaymsg('-r', '-t', 'get_tree').returncode, 0)
        finally: c.stop()

    def test_done_ends_it(self):
        r = self.rig; r.start('--keep-on-window'); self.assertTrue(r.ready(), r.logtext())
        r.say('done'); self.assertEqual(r.bs.wait(30), 0)

    def test_sigterm_ends_it_cleanly_and_the_screen_goes_back_to_what_was_under_it(self):
        r = self.rig; before = r.shot()
        r.start('--keep-on-window'); self.assertTrue(r.ready(), r.logtext()); time.sleep(0.3)
        self.assertFalse((r.shot() == before).all())                                                   # (the boot screen is really up)
        r.bs.terminate(); self.assertEqual(r.bs.wait(30), 0)
        time.sleep(0.5); self.assertTrue((r.shot() == before).all(), 'something of the overlay is left on the screen')

    def test_it_gives_up_after_its_ceiling_even_if_no_window_ever_comes(self):
        r = self.rig; t0 = time.time(); r.start('--keep-on-window', '--max-seconds', '3'); self.assertTrue(r.ready(), r.logtext())
        self.assertEqual(r.bs.wait(60), 0); self.assertIn('giving up the overlay', r.logtext())

    def test_a_long_wait_changes_the_words_and_points_at_the_log(self):
        r = self.rig; r.start('--keep-on-window', '--slow-after', '2', '--status', 'Starting Steam'); self.assertTrue(r.ready(), r.logtext())
        time.sleep(4.0)
        want = r.splash_render('--status', 'This is taking longer than usual', '--detail', 'Press SELECT to see what the system is doing.')
        self.assertTrue((outside_spinner(r.shot()) == outside_spinner(want)).all())

    def test_the_off_switch_and_the_dev_mark_come_from_the_kernel_command_line(self):
        r = self.rig
        (r.d / 'cmdline').write_text('boot=LABEL=ROCKNIX quiet wisp.splash=off\n')
        r.start(); self.assertEqual(r.bs.wait(30), 0); self.assertIn('wisp.splash=off', r.logtext())
        (r.d / 'cmdline').write_text('boot=LABEL=ROCKNIX wisp.dev\n')
        r.start('--keep-on-window'); self.assertTrue(r.ready(), r.logtext()); time.sleep(0.4)
        want = r.splash_render('--dev'); self.assertTrue((outside_spinner(r.shot()) == outside_spinner(want)).all())                  # the red DEV BUILD mark

    def test_it_tells_the_framebuffer_splash_it_can_go_once_its_own_first_frame_is_up(self):
        r = self.rig; fifo = r.rt / 'splash.fifo'
        if fifo.exists(): fifo.unlink()
        os.mkfifo(str(fifo)); rfd = os.open(str(fifo), os.O_RDONLY | os.O_NONBLOCK)
        try:
            r.start('--keep-on-window'); r.bs.kill(); r.bs.wait()                                      # (start() points --splash-fifo elsewhere; restart with the real one)
            cmd = [str(r.bin / 'wisp-bootscreen'), '--fifo', str(r.rt / 'bs2.fifo'), '--splash-fifo', str(fifo), '--cmdline', str(r.d / 'cmdline'), '--no-input', '--keep-on-window']
            (r.d / 'cmdline').write_text('quiet\n')
            r.bs = subprocess.Popen(cmd, env={**r.env, 'NOTIFY_SOCKET': r.notify_path}, stdout=r.bs_log, stderr=subprocess.STDOUT)
            self.assertTrue(r.ready(), r.logtext())
            time.sleep(0.5); got = b''
            try: got = os.read(rfd, 64)
            except OSError: pass
            self.assertEqual(got, b'ready\n')
        finally: os.close(rfd)

    def test_its_fifo_is_removed_when_it_ends(self):
        r = self.rig; r.start('--keep-on-window'); self.assertTrue(r.ready(), r.logtext()); self.assertTrue(r.fifo.exists())
        r.say('done'); self.assertEqual(r.bs.wait(30), 0); self.assertFalse(r.fifo.exists())

    def test_without_a_display_it_says_so_and_exits_1(self):
        env = {**self.rig.env, 'WAYLAND_DISPLAY': 'wayland-nowhere'}
        p = subprocess.run([str(self.rig.bin / 'wisp-bootscreen'), '--wait', '1', '--no-input', '--fifo', str(self.rig.rt / 'x.fifo'), '--cmdline', '/nonexistent'], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 1); self.assertIn('no Wayland display', p.stderr)

    def test_the_build_for_the_device_runs_under_qemu_and_draws_the_same_screen(self):
        r = self.rig
        if not r.device_build: self.skipTest('no aarch64 cross compiler')
        r.start('--keep-on-window', '--status', 'Starting the display', device=True); self.assertTrue(r.ready(300), r.logtext())
        time.sleep(1.0); shot = r.shot(); want = r.splash_render('--status', 'Starting the display')
        self.assertTrue((outside_spinner(shot) == outside_spinner(want)).all())


@unittest.skipIf(T.why_skipped(), T.why_skipped() or '')
class BootScreenAtScaleTwo(unittest.TestCase):
    """Game mode runs the panel at scale 2 (360 x 240 logical): the screen must be drawn at the panel's own pixels, not upscaled from a smaller picture."""
    rig = None

    @classmethod
    def setUpClass(cls):
        cls.rig = BootRig(2)
        try: cls.rig.build_programs(); cls.rig.start_sway()
        except BaseException: cls.rig.stop(); raise

    @classmethod
    def tearDownClass(cls):
        if cls.rig: cls.rig.stop_bs(); cls.rig.stop()

    def test_it_draws_the_native_pixels_at_output_scale_two(self):
        r = self.rig; r.start('--keep-on-window', '--status', 'Starting the display'); self.assertTrue(r.ready(), r.logtext())
        time.sleep(0.8)
        shot = r.shot()                                                                                # grim's default: one screenshot pixel per physical pixel of the output
        self.assertEqual(shot.shape[:2], (H, W), 'the screenshot is %s' % (shot.shape,))
        want = r.splash_render('--status', 'Starting the display')
        self.assertTrue((outside_spinner(shot) == outside_spinner(want)).all(), 'drawn at scale 2 but not pixel for pixel the splash\'s picture')


@unittest.skipIf(T.why_skipped(), T.why_skipped() or '')
class BootScreenOnALargerOutput(unittest.TestCase):
    """An output bigger than the 720 x 480 the screen is made for (an external display): the screen sits in the middle of black, whole and unscaled."""
    rig = None

    @classmethod
    def setUpClass(cls):
        cls.rig = BootRig(1, '1280x720')
        try: cls.rig.build_programs(); cls.rig.start_sway()
        except BaseException: cls.rig.stop(); raise

    @classmethod
    def tearDownClass(cls):
        if cls.rig: cls.rig.stop_bs(); cls.rig.stop()

    def test_the_screen_is_centred_on_a_larger_output(self):
        r = self.rig; r.start('--keep-on-window', '--status', 'Starting the display'); self.assertTrue(r.ready(), r.logtext()); time.sleep(0.8)
        shot = r.shot(); self.assertEqual(shot.shape[:2], (720, 1280))
        want = np.zeros((720, 1280, 3), dtype=np.uint8); ox, oy = (1280 - W) // 2, (720 - H) // 2
        want[oy:oy + H, ox:ox + W] = r.splash_render('--status', 'Starting the display')
        m = np.ones((720, 1280), dtype=bool); m[oy + CY - BOX:oy + CY + BOX + 1, ox + CX - BOX:ox + CX + BOX + 1] = False
        self.assertTrue((shot[m] == want[m]).all(), 'not the splash\'s picture in the middle of black')


MUTANTS = [
    ('it does not end when a window appears', "    if (end_on_window) { note(\"a window appeared: the boot screen ends\"); S.running = false; }", "    (void)end_on_window;"),
    ('the buffer scale is ignored', "int pw = S.lw * S.scale, ph = S.lh * S.scale;", "int pw = S.lw, ph = S.lh;"),
    ('the framebuffer splash is never told', "if (fd >= 0) { (void)!write(fd, \"ready\\n\", 6); close(fd); }", "if (fd >= 0) close(fd);"),
    ('the off switch is ignored', "if (strstr(line, \"wisp.splash=off\"))", "if (0 && strstr(line, \"wisp.splash=off\"))"),
    ('the ceiling is ignored', "if (max_s > 0 && now - t0 >= max_s)", "if (0 && max_s > 0 && now - t0 >= max_s)"),
    ('done on the FIFO does nothing', "else if (!strcmp(inbuf, \"done\") || !strcmp(inbuf, \"quit\")) S.running = false;", "else if (!strcmp(inbuf, \"quit\")) S.running = false;"),
    ('a failure never opens the log', "if (failed_shown && S.mode == M_SPLASH && now - fail_at >= 2) { toggle = true; failed_shown = false; }", "if (0) { toggle = true; failed_shown = false; }"),
    ('the scene is not centred on a larger output', "S.ox = pw > WISP_UI_W ? (pw - WISP_UI_W) / 2 : 0;", "S.ox = 0;"),
    ('the FIFO is left behind', "    unlink(fifopath);", "    (void)fifopath;"),
    ('the slow-start words never change', "if (!slow_shown && slow_after > 0 && !S.scr.failed && now - t0 >= slow_after) {", "if (0 && !slow_shown && slow_after > 0 && !S.scr.failed && now - t0 >= slow_after) {"),
]


class Mutants(unittest.TestCase):
    """Each rule of the program changed in turn: the compositor tests (run in parallel against the changed copy) must fail every time."""
    def test_every_mutant_is_caught(self):
        if 'WISP_BOOTSCREEN_C' in os.environ: self.skipTest('running a mutant already')
        if T.why_skipped(): self.skipTest(T.why_skipped())
        import concurrent.futures
        tmp = Path(tempfile.mkdtemp(prefix='wispos-bsmut-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, tmp, True)
        text = SRC.read_text(); jobs = []
        for i, (what, old, new) in enumerate(MUTANTS):
            self.assertEqual(text.count(old), 1, 'mutant %r: the pattern must occur exactly once' % what)
            m = tmp / ('mutant%d.c' % i); m.write_text(text.replace(old, new)); jobs.append((what, m))
        def run(job):
            what, m = job
            r = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), 'BootScreenTests', 'BootScreenAtScaleTwo', 'BootScreenOnALargerOutput'], env={**os.environ, 'WISP_BOOTSCREEN_C': str(m)},
                               capture_output=True, text=True, timeout=1800)
            return what, r.returncode
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex: results = list(ex.map(run, jobs))
        self.assertEqual([w for w, rc in results if rc == 0], [], 'mutants that no test noticed')


if __name__ == '__main__':
    unittest.main()
