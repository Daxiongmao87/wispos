"""The Images launcher (os/rootfs/usr/bin/wisp-images), run for real against a fake viewer and a fake desktop shell that records toasts. Found on the handheld 2026-10-15: the Images tile ran `swayimg`
with no files, which read the (empty) current directory, printed "No images to view, exit" to a journal nobody reads and left the user with a tile that did nothing. The launcher opens the pictures
in the Downloads folder, and says so in a toast when there are none."""
import os, subprocess, tempfile, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE / 'rootfs'
WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))
SCRIPT = ROOT / 'usr/bin/wisp-images'


class ImagesLauncherTests(unittest.TestCase):
    def setUp(self):
        (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
        self.d = Path(tempfile.mkdtemp(prefix='wisp-images-', dir=WORK / 'tmp'))
        self.addCleanup(lambda: __import__('shutil').rmtree(self.d, True))
        self.pics = self.d / 'downloads'; self.pics.mkdir()
        (self.d / 'viewer').write_text('#!/bin/sh\nfor a in "$@"; do echo "$a" >> "%s/viewed.log"; done\n' % self.d)
        (self.d / 'dock').write_text('#!/bin/sh\necho "$@" >> "%s/toasts.log"\n' % self.d)
        for n in ('viewer', 'dock'): os.chmod(self.d / n, 0o755)

    def run_launcher(self):
        env = {**os.environ, 'WISP_IMAGES_DIR': str(self.pics), 'WISP_IMAGES_VIEWER': str(self.d / 'viewer'), 'WISP_IMAGES_DOCK': str(self.d / 'dock')}
        return subprocess.run(['sh', str(SCRIPT)], env=env, capture_output=True, text=True)

    def log(self, name):
        p = self.d / name
        return p.read_text().splitlines() if p.exists() else []

    def test_an_empty_folder_says_so_in_a_toast_and_starts_no_viewer(self):
        r = self.run_launcher(); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.log('viewed.log'), [], 'no viewer for nothing to view')
        toasts = self.log('toasts.log'); self.assertEqual(len(toasts), 1)
        self.assertTrue(toasts[0].startswith('--ctl toast Images|No pictures in Downloads'), toasts[0])

    def test_a_folder_with_only_other_files_counts_as_empty(self):
        (self.pics / 'notes.txt').write_text('x'); (self.pics / 'setup.exe').write_text('x')
        self.run_launcher(); self.assertEqual(self.log('viewed.log'), []); self.assertEqual(len(self.log('toasts.log')), 1)

    def test_pictures_are_opened_in_name_order_whatever_the_case_of_the_extension(self):
        for n in ('b.PNG', 'a.jpg', 'c.jpeg', 'd.webp', 'e.gif', 'f.bmp', 'readme.txt'): (self.pics / n).write_text('x')
        r = self.run_launcher(); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual([Path(x).name for x in self.log('viewed.log')], ['a.jpg', 'b.PNG', 'c.jpeg', 'd.webp', 'e.gif', 'f.bmp'])
        self.assertEqual(self.log('toasts.log'), [], 'no toast when there is something to show')

    def test_a_missing_folder_is_an_empty_one(self):
        self.pics.rmdir(); r = self.run_launcher(); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(self.log('toasts.log')), 1)

    def test_the_tile_runs_the_launcher_and_the_check_demands_it(self):
        entry = (ROOT / 'usr/share/applications/wisp-images.desktop').read_text()
        self.assertIn('Exec=wisp-images\n', entry); self.assertIn('StartupWMClass=swayimg', entry)
        self.assertIn('usr/bin/wisp-images', (HERE / 'check_rootfs.py').read_text())
        self.assertTrue(os.access(SCRIPT, os.X_OK), 'the launcher is executable')


if __name__ == '__main__':
    unittest.main()
