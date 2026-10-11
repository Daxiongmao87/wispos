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
