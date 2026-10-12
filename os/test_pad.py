"""Runs the pad logic's and the pad I/O's C tests (os/pad/test_logic.c, os/pad/test_io.c) natively under the address and undefined-behaviour sanitizers, checks that the daemon builds for the device (aarch64, -Werror),
and checks the tests themselves: each of a list of rule mutants of pad-logic.c and pad-io.c (a swapped click, a threshold, a missing hysteresis, a missing release ...) must fail them, so a
test that passes whatever the logic does cannot get in. No device, no uinput, no compositor."""
import os, shutil, subprocess, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAD = HERE / 'pad'
WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))

# (what the mutant breaks, the text in pad-logic.c, what it becomes)
MUTANTS = [
    ('R1 is the right click', 'hold(p, o, M_LEFT);  *a = (PadAct){ ACT_HOLD, M_LEFT };', 'hold(p, o, M_RIGHT);  *a = (PadAct){ ACT_HOLD, M_RIGHT };'),
    ('L1 is the left click', 'hold(p, o, M_RIGHT); *a = (PadAct){ ACT_HOLD, M_RIGHT };', 'hold(p, o, M_LEFT); *a = (PadAct){ ACT_HOLD, M_LEFT };'),
    ('R2 is Shift+Tab', 'case PAD_R2: hold(p, o, K_TAB);   *a = (PadAct){ ACT_HOLD, K_TAB };   break;', 'case PAD_R2: hold(p, o, K_LSHIFT); hold(p, o, K_TAB); *a = (PadAct){ ACT_SHIFT_TAB, 0 }; break;'),
    ('Shift goes down after Tab', 'hold(p, o, K_LSHIFT); hold(p, o, K_TAB); *a = (PadAct){ ACT_SHIFT_TAB, 0 }; break;\n    case PAD_L3', 'hold(p, o, K_TAB); hold(p, o, K_LSHIFT); *a = (PadAct){ ACT_SHIFT_TAB, 0 }; break;\n    case PAD_L3'),
    ('Shift+Tab leaves Shift down', 'case ACT_SHIFT_TAB: release(p, o, K_TAB); release(p, o, K_LSHIFT); break;', 'case ACT_SHIFT_TAB: release(p, o, K_TAB); break;'),
    ('the press level is 45 %', '.press = 0.55f', '.press = 0.45f'),
    ('no hysteresis', '.release = 0.35f', '.release = 0.55f'),
    ('a dead zone of 2 %', '.dead = 0.12f', '.dead = 0.02f'),
    ('a dead zone of 30 %', '.dead = 0.12f', '.dead = 0.30f'),
    ('no repeat delay', 'r->next = now + p->cfg.repeat_delay_ms;', 'r->next = now + 100;'),
    ('the left stick does not speed up', 'rep_tick(p, &p->stick, true, now, o, wake)', 'rep_tick(p, &p->stick, false, now, o, wake)'),
    ('the OSK D-pad ramps', 'rep_tick(p, &p->dpad, false, now, o, wake)', 'rep_tick(p, &p->dpad, true, now, o, wake)'),
    ('Back on the press', 'p->b_since = now; *a = (PadAct){ ACT_BACK, 0 };', 'tap(p, o, M_SIDE); p->b_since = now; *a = (PadAct){ ACT_BACK, 0 };'),
    ('a held B is not Escape', 'if (now >= due) { tap(p, o, K_ESC); p->b_since = -1; }', 'if (now >= due) { p->b_since = -1; }'),
    ('twice the hold time', 'int64_t due = p->b_since + p->cfg.hold_ms;', 'int64_t due = p->b_since + 2 * p->cfg.hold_ms;'),
    ('Back after the hold fired', 'if (p->b_since >= 0) { tap(p, o, M_SIDE); p->b_since = -1; }', '{ tap(p, o, M_SIDE); p->b_since = -1; }'),
    ('Y is Space in the browser', 'hold(p, o, M_EXTRA); *a = (PadAct){ ACT_HOLD, M_EXTRA };', 'hold(p, o, K_SPACE); *a = (PadAct){ ACT_HOLD, K_SPACE };'),
    ('Select does something in the desktop layer', 'else if (L == PL_BROWSER) cmd(p, o, "actions");', 'else cmd(p, o, "actions");'),
    ('A types the wrong word', '"osk-type"', '"osk-typo"'),
    ('L3 slows the wrong way', 'p->slow ? p->cfg.slow : 1.f', 'p->slow ? 1.f : p->cfg.slow'),
    ('R3 does not toggle', 'p->scroll_mode = !p->scroll_mode;', 'p->scroll_mode = true;'),
    ('the pointer moves in scroll mode too', 'if (!p->scroll_mode) {', 'if (1) {'),
    ('the horizontal axis is preferred', 'if (axx >= ayy) return', 'if (axx >= p->cfg.press) return'),
    ('the right stick scrolls the wrong way', 'sy -= uy * v;                                               /* stick up', 'sy += uy * v;                                               /* stick up'),
    ('the left stick scrolls the wrong way in the browser', 'sy -= uy * v;\n        }\n    }\n    if (active) {', 'sy += uy * v;\n        }\n    }\n    if (active) {'),
    ('no whole notches', 'v.notch_y = p->hi_y / 120;', 'v.notch_y = 0;'),
    ('a key is up when any source lets go', 'if (--p->held[code] == 0) emit_key(p, o, code, 0);', '{ p->held[code]--; emit_key(p, o, code, 0); }'),
    ('a layer change does not hold the stick', 'p->stick.dir = fmaxf(fabsf(p->ax[PAD_LX]), fabsf(p->ax[PAD_LY])) >= p->cfg.release ? PAD_STICK_LOCKED : 0;', 'p->stick.dir = 0;'),
    ('a held key is never released', 'case ACT_HOLD: release(p, o, a.arg); break;', 'case ACT_HOLD: break;'),
    ('a layer change keeps the OSK repeat', 'p->layer = l;\n    p->dpad.dir = 0;', 'p->layer = l;'),
    ('no limit on a stalled step', 'if (dt > MAX_DT_MS) dt = MAX_DT_MS;', ''),
    ('fractions of a pixel are lost', 'p->fx -= (float)ix;', 'p->fx = 0.f;'),
    ('a quiet pad keeps its old time', 'if (wake < 0) p->t_last = -1;', ''),
    ('release_all forgets the counts', 'if (p->held[c]) emit_key(p, o, c, 0);\n        p->held[c] = 0;', 'if (p->held[c]) emit_key(p, o, c, 0);'),
    ('the D-pad taps instead of holding', 'else { hold(p, o, k); *a = (PadAct){ ACT_HOLD, k }; }', 'else { tap(p, o, k); }'),
    ('Mode does nothing', 'case PAD_MODE:   cmd(p, o, "summon"); break;', 'case PAD_MODE:   break;'),
    ('Start is the menu in the keyboard layer', 'cmd(p, o, L == PL_OSK ? "osk-enter" : "menu")', 'cmd(p, o, "menu")'),
    ('X is not Shift in the keyboard layer', 'cmd(p, o, L == PL_OSK ? "osk-shift" : "osk")', 'cmd(p, o, "osk")'),
    ('the left stick sends arrows in the browser', 'if (p->layer == PL_BROWSER) { p->stick.dir = 0; return; }', ''),
    ('the layer for a window is case sensitive', 'strncasecmp(app_id', 'strncmp(app_id'),
    ('a typed capital leaves Shift down', 'tap(p, o, code);\n    if (mods & PAD_MOD_SHIFT) release(p, o, K_LSHIFT);', 'tap(p, o, code);'),
    ('a typed capital has Shift after the key', 'if (mods & PAD_MOD_SHIFT) hold(p, o, K_LSHIFT);\n    tap(p, o, code);', 'tap(p, o, code);\n    if (mods & PAD_MOD_SHIFT) hold(p, o, K_LSHIFT);'),
    ('Ctrl is not held around a typed key', 'if (mods & PAD_MOD_CTRL) hold(p, o, K_LCTRL);', ''),
    ('Ctrl is not released after a typed key', 'if (mods & PAD_MOD_CTRL) release(p, o, K_LCTRL);', ''),
    ('Alt is held but never released', 'if (mods & PAD_MOD_ALT) release(p, o, K_LALT);', ''),
    ('Ctrl goes down after Shift', 'if (mods & PAD_MOD_CTRL) hold(p, o, K_LCTRL);\n    if (mods & PAD_MOD_ALT) hold(p, o, K_LALT);\n    if (mods & PAD_MOD_SHIFT) hold(p, o, K_LSHIFT);', 'if (mods & PAD_MOD_SHIFT) hold(p, o, K_LSHIFT);\n    if (mods & PAD_MOD_ALT) hold(p, o, K_LALT);\n    if (mods & PAD_MOD_CTRL) hold(p, o, K_LCTRL);'),
    ('any code is typed', 'if (code < 1 || code > PAD_KEY_MAX) return;', ''),
    ('a duplicate press repeats', 'if (p->isdown[code]) return;', ''),
    ('B is not Escape in the desktop layer', 'else { hold(p, o, K_ESC); *a = (PadAct){ ACT_HOLD, K_ESC }; }', 'else { hold(p, o, K_BACKSPACE); *a = (PadAct){ ACT_HOLD, K_BACKSPACE }; }'),
]

# the same for pad-io.c, against test_io.c
IO_MUTANTS = [
    ('a kernel auto-repeat is a press', 'if (e->value == 2) return;', ''),
    ('the hat left and right are swapped', 'int neg = which ? PAD_DUP : PAD_DLEFT, pos = which ? PAD_DDOWN : PAD_DRIGHT;', 'int neg = which ? PAD_DUP : PAD_DRIGHT, pos = which ? PAD_DDOWN : PAD_DLEFT;'),
    ('a hat change does not release the old direction', 'if (prev < 0) pad_button(&io->pad, now, neg, false, o);', ''),
    ('an inverted axis is not inverted', 'if (s->r.invert[a]) n = -n;', ''),
    ('the middle of the range is zero', 'double mid = ((double)lo + hi) / 2.0, half = ((double)hi - lo) / 2.0;', 'double mid = 0.0, half = ((double)hi - lo) / 2.0;'),
    ('values past the range are not clamped', 'n = n < -1.0 ? -1.0 : n > 1.0 ? 1.0 : n;', ''),
    ('an axis without a range is used', 'if (hi <= lo) return;', ''),
    ('no frame end after a key', 'add_ev(kb, &nk, EV_KEY, v->code, v->value);\n            add_ev(kb, &nk, EV_SYN, SYN_REPORT, 0);', 'add_ev(kb, &nk, EV_KEY, v->code, v->value);'),
    ('mouse buttons go to the keyboard', 'add_ev(ms, &nm, EV_KEY, v->code, v->value);\n            add_ev(ms, &nm, EV_SYN, SYN_REPORT, 0);', 'add_ev(kb, &nk, EV_KEY, v->code, v->value);\n            add_ev(kb, &nk, EV_SYN, SYN_REPORT, 0);'),
    ('no legacy wheel notches', 'if (v->notch_y) add_ev(ms, &nm, EV_REL, REL_WHEEL, v->notch_y);', ''),
    ('no hi-res wheel', 'if (v->dy) add_ev(ms, &nm, EV_REL, REL_WHEEL_HI_RES, v->dy);', ''),
    ('the pointer Y is dropped', 'if (v->dy) add_ev(ms, &nm, EV_REL, REL_Y, v->dy);', ''),
    ('the last pad leaving leaves keys down', 'if (padio_sources(io) == 0) { pad_release_all(&io->pad, o); flush(io, o, now); }', ''),
    ('any pad leaving releases everything', 'if (padio_sources(io) == 0) { pad_release_all(&io->pad, o); flush(io, o, now); }', '{ pad_release_all(&io->pad, o); flush(io, o, now); }'),
    ('a dry run also writes to the devices', 'if (io->dry) {', 'if (0) {'),
    ('a typed key ignores its modifiers', 'else pad_type(&io->pad, (int)code, mods, o);', 'else pad_type(&io->pad, (int)code, 0, o);'),
    ('a repeated modifier is accepted', 'if (!bit || (*mods & bit)) return false;', 'if (!bit) return false;'),
    ('an unknown modifier is ignored', 'if (!bit || (*mods & bit)) return false;', 'if (*mods & bit) return false;'),
    ('a modifier that is a prefix of the word counts', 'n == 5 && !strncmp(s, "shift", 5)', 'n >= 5 && !strncmp(s, "shift", 5)'),
    ('focus does not change the layer', 'io->focus, sizeof io->focus, "%s", line[5] ? line + 6 : "");\n        relayer(io);', 'io->focus, sizeof io->focus, "%s", line[5] ? line + 6 : "");'),
    ('the keyboard open does not change the layer', 'io->osk = line[5] == \'n\';\n        relayer(io);', 'io->osk = line[5] == \'n\';'),
    ('osk off is on', 'io->osk = line[5] == \'n\';', 'io->osk = true;'),
    ('a bad key code is typed', 'code < 1 || code > PAD_KEY_MAX ||', 'code < 0 ||'),
    ('a long line is kept', 'else {\n            note("dropping a shell connection that sent a line that is too long");\n            drop_client(io, i);\n            return;\n        }', 'else { }'),
    ('stop leaves the socket', 'if (io->sock_path[0]) unlink(io->sock_path);', ''),
    ('stop leaves keys down', 'pad_release_all(&io->pad, &o);\n    flush(io, &o, io->clock());', ''),
    ('a plain file at the socket path is removed', 'if (lstat(path, &st) == 0 && S_ISSOCK(st.st_mode)) unlink(path);', 'if (lstat(path, &st) == 0) unlink(path);'),
    ('the socket is world accessible', 'chmod(path, 0600)', 'chmod(path, 0666)'),
    ('a stale socket stays', 'if (lstat(path, &st) == 0 && S_ISSOCK(st.st_mode)) unlink(path);', ''),
    ('a shell that stops reading is kept', 'drop_client(io, i);\n        }\n    }\n}\n\nstatic void flush', 'note("x");\n        }\n    }\n}\n\nstatic void flush'),
    ('the wait ignores the logic\'s tick', 'if (timeout < 0 || t < timeout) timeout = t;', ''),
]


@unittest.skipUnless(shutil.which('gcc'), 'needs a native gcc')
class PadLogicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = Path(tempfile.mkdtemp(prefix='wisp-pad-', dir=WORK / 'tmp'))
        cls.exe = cls.d / 'test_logic'
        r = subprocess.run(['gcc', '-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=undefined',
                            '-o', str(cls.exe), str(PAD / 'pad-logic.c'), str(PAD / 'test_logic.c'), '-lm'], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        cls.run_ = subprocess.run([str(cls.exe)], capture_output=True, text=True, timeout=300)
        cls.io_exe = cls.d / 'test_io'
        r = subprocess.run(['gcc', '-std=gnu11', '-O1', '-Wall', '-Wextra', '-Werror', '-g', '-fsanitize=address,undefined', '-fno-sanitize-recover=undefined',
                            '-o', str(cls.io_exe), str(PAD / 'pad-io.c'), str(PAD / 'pad-logic.c'), str(PAD / 'test_io.c'), '-lm'], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        cls.io_run = subprocess.run([str(cls.io_exe)], capture_output=True, text=True, timeout=300, env={**os.environ, 'TMPDIR': str(cls.d)})

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, True)

    def test_all_checks_pass_with_no_sanitizer_report(self):
        self.assertEqual(self.run_.returncode, 0, self.run_.stdout[-3000:] + self.run_.stderr[-2000:])
        self.assertRegex(self.run_.stdout, r'\d{3,} checks, 0 failed')
        self.assertNotIn('runtime error', self.run_.stderr)

    def test_the_io_checks_pass_with_no_sanitizer_report(self):
        self.assertEqual(self.io_run.returncode, 0, self.io_run.stdout[-3000:] + self.io_run.stderr[-2000:])
        self.assertRegex(self.io_run.stdout, r'\d{3,} checks, 0 failed')
        self.assertNotIn('runtime error', self.io_run.stderr)

    @unittest.skipUnless(shutil.which('aarch64-linux-gnu-gcc'), 'needs the aarch64 cross compiler')
    def test_the_daemon_builds_for_the_device_without_warnings(self):
        r = subprocess.run(['aarch64-linux-gnu-gcc', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror', '-o', os.devnull,
                            str(PAD / 'wisp-pad.c'), str(PAD / 'pad-io.c'), str(PAD / 'pad-logic.c'), '-lm'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def _mutants(self, mutants, target, others, tests):
        src = (PAD / target).read_text()
        survived, broken = [], []
        for i, (what, old, new) in enumerate(mutants):
            self.assertIn(old, src, 'the mutant "%s" no longer matches %s' % (what, target))
            self.assertEqual(src.count(old), 1, 'the mutant "%s" matches more than once' % what)
            m = self.d / ('%s-mutant%d.c' % (target, i))
            m.write_text(src.replace(old, new))
            exe = self.d / ('%s-mutant%d' % (target, i))
            r = subprocess.run(['gcc', '-std=gnu11', '-O1', '-I', str(PAD), '-w', '-o', str(exe), str(m)] + [str(PAD / o) for o in others] + [str(PAD / tests), '-lm'],
                               capture_output=True, text=True)
            if r.returncode != 0:
                broken.append('%s: %s' % (what, r.stderr[:200]))
                continue
            try:
                run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120, env={**os.environ, 'TMPDIR': str(self.d)})
            except subprocess.TimeoutExpired:
                continue                                           # a mutant that hangs the tests is caught
            if run.returncode == 0:
                survived.append(what)
        self.assertEqual(broken, [], 'mutants that do not compile test nothing')
        self.assertEqual(survived, [], 'the tests do not notice these changes to %s' % target)

    def test_every_rule_mutant_of_the_logic_is_caught(self):
        self._mutants(MUTANTS, 'pad-logic.c', [], 'test_logic.c')

    def test_every_rule_mutant_of_the_io_is_caught(self):
        self._mutants(IO_MUTANTS, 'pad-io.c', ['pad-logic.c'], 'test_io.c')


if __name__ == '__main__':
    unittest.main()
