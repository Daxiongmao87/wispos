"""The download toast's actions (os/rootfs/usr/bin/wisp-download-action): run for real on a temporary card with a fake desktop shell that records the toasts and the host's own tar, xz, bzip2 and 7z.
What is checked: an AppImage ends up in the application menu with a safe name and a path that holds nothing a shell could misread; archives unpack to a folder of their own without climbing out of
it; nothing is left half done; and a name from the web is never trusted. Nothing outside the work directory is written."""
import os, subprocess, tarfile, tempfile, unittest, zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE / 'rootfs'
WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))
SCRIPT = Path(os.environ.get('WISP_DOWNLOAD_ACTION_SCRIPT') or ROOT / 'usr/bin/wisp-download-action')
EXEC = 0o755


class Fixture(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix='wisp-dlact-', dir=WORK / 'tmp'))
        self.addCleanup(lambda: __import__('shutil').rmtree(self.d, True))
        self.down, self.apps, self.entries, self.games = (self.d / n for n in ('downloads', 'apps', 'entries', 'games'))
        self.down.mkdir()
        (self.d / 'dock').write_text('#!/bin/sh\necho "$@" >> "%s/toasts.log"\n' % self.d)
        (self.d / 'viewer').write_text('#!/bin/sh\necho "$@" >> "%s/viewer.log"\n' % self.d)
        for n in ('dock', 'viewer'): os.chmod(self.d / n, EXEC)
        (self.d / 'fuse').write_text('')

    def env(self, **extra):
        return {**os.environ, 'WISP_DOCK': str(self.d / 'dock'), 'WISP_APPS_DIR': str(self.apps), 'WISP_ENTRIES_DIR': str(self.entries), 'WISP_GAMES_DIR': str(self.games),
                'WISP_FUSE_DEV': str(self.d / 'fuse'), 'WISP_VIEWER': str(self.d / 'viewer'), **extra}

    def run_action(self, *args, **extra):
        return subprocess.run(['sh', str(SCRIPT), *args], capture_output=True, text=True, env=self.env(**extra), timeout=120)

    def toasts(self):
        f = self.d / 'toasts.log'; return f.read_text().splitlines() if f.exists() else []

    def appimage(self, name, body=b'\x7fELF fake appimage'):
        p = self.down / name; p.write_bytes(body); return p


class InstallTests(Fixture):
    def test_an_appimage_becomes_an_application_with_a_clean_name_and_a_menu_entry(self):
        f = self.appimage('Foo-1.2.3-aarch64.AppImage')
        r = self.run_action('install', str(f)); self.assertEqual(r.returncode, 0, r.stderr)
        dest = self.apps / 'Foo-1.2.3-aarch64.AppImage'
        self.assertTrue(dest.exists() and os.access(dest, os.X_OK), 'moved into place and executable')
        self.assertFalse(f.exists(), 'it is the same file, moved: the card is not written twice')
        entry = (self.entries / 'appimage-Foo-1.2.3-aarch64.desktop').read_text()
        self.assertEqual(entry, '[Desktop Entry]\nType=Application\nName=Foo\nExec=%s\nTerminal=false\nCategories=Utility;\n' % dest)
        self.assertTrue(any(t.startswith('--ctl toast Installed|Foo is in the application menu.') for t in self.toasts()), self.toasts())

    def test_without_fuse_the_program_is_started_extracting_itself(self):
        f = self.appimage('Bar.AppImage')
        r = self.run_action('install', str(f), WISP_FUSE_DEV=str(self.d / 'no-such-device')); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('Exec=env APPIMAGE_EXTRACT_AND_RUN=1 %s\n' % (self.apps / 'Bar.AppImage'), (self.entries / 'appimage-Bar.desktop').read_text())

    def test_names_are_reduced_to_safe_characters_before_they_are_a_path_or_a_command_line(self):
        f = self.appimage('My App (v2) $(touch pwned)!.AppImage')
        r = self.run_action('install', str(f)); self.assertEqual(r.returncode, 0, r.stderr)
        made = sorted(p.name for p in self.apps.iterdir())
        self.assertEqual(len(made), 1); self.assertRegex(made[0], r'^[A-Za-z0-9._-]+\.AppImage$')
        entries = list(self.entries.iterdir()); self.assertEqual(len(entries), 1)
        text = entries[0].read_text()
        exec_line = [l for l in text.splitlines() if l.startswith('Exec=')][0]
        self.assertNotRegex(exec_line[5:], r'[$`;&|<>()"\'\\ ]', 'nothing a shell or the entry parser could misread')
        self.assertRegex([l for l in text.splitlines() if l.startswith('Name=')][0], r'^Name=[A-Za-z0-9 +]+$')
        self.assertFalse((self.d / 'pwned').exists() or Path('pwned').exists())

    def test_the_same_program_again_replaces_the_old_one(self):
        self.run_action('install', str(self.appimage('Foo.AppImage', b'\x7fELF one')))
        self.run_action('install', str(self.appimage('Foo.AppImage', b'\x7fELF two')))
        self.assertEqual((self.apps / 'Foo.AppImage').read_bytes(), b'\x7fELF two'); self.assertEqual(len(list(self.entries.iterdir())), 1)

    def test_label_heuristics(self):
        for stem, want in (('Some_Tool-v3.4-x86_64', 'Some Tool'), ('Foo-aarch64', 'Foo'), ('2048-game', '2048 game'), ('Plain', 'Plain'), ('Foo.Bar-1.0', 'Foo Bar'), ('v1.2.3', 'v1')):
            for p in list(self.entries.glob('*')): p.unlink()
            r = self.run_action('install', str(self.appimage(stem + '.AppImage'))); self.assertEqual(r.returncode, 0, r.stderr)
            name = [l for l in next(self.entries.iterdir()).read_text().splitlines() if l.startswith('Name=')][0]
            self.assertEqual(name, 'Name=' + want, stem)

    def test_a_missing_file_or_a_link_is_refused(self):
        r = self.run_action('install', str(self.down / 'gone.AppImage')); self.assertEqual(r.returncode, 1)
        self.assertTrue(any('not there any more' in t for t in self.toasts()))
        target = self.appimage('real.AppImage'); link = self.down / 'link.AppImage'; link.symlink_to(target)
        r = self.run_action('install', str(link)); self.assertEqual(r.returncode, 1)
        self.assertFalse(self.apps.exists() and list(self.apps.iterdir()), 'a link is not followed out of the downloads folder')

    def test_a_card_that_cannot_be_written_is_reported(self):
        f = self.appimage('Foo.AppImage')
        blocker = self.d / 'file-not-dir'; blocker.write_text('x')
        r = self.run_action('install', str(f), WISP_APPS_DIR=str(blocker / 'apps')); self.assertEqual(r.returncode, 1)
        self.assertTrue(any('Cannot write to the card' in t for t in self.toasts()))
        self.assertTrue(f.exists(), 'the download is left where it was')

    def test_usage(self):
        for args in ((), ('install',), ('frobnicate', str(self.down / 'x'))):
            r = self.run_action(*args)
            self.assertIn(r.returncode, (1, 2), args)
        self.assertEqual(self.run_action().returncode, 2)


class ExtractTests(Fixture):
    def test_a_zip_with_one_folder_is_unwrapped_and_left_where_it_was(self):
        z = self.down / 'Doom-1.0.zip'
        with zipfile.ZipFile(z, 'w') as f:
            f.writestr('Doom-1.0/doom.wad', 'wad'); f.writestr('Doom-1.0/readme.txt', 'hi')
        r = self.run_action('extract', str(z)); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(p.name for p in (self.games / 'Doom-1.0').iterdir()), ['doom.wad', 'readme.txt'], 'the folder it brought is the folder, not a folder inside a folder')
        self.assertTrue(z.exists(), 'the archive is kept')
        self.assertEqual(sorted(p.name for p in self.games.iterdir()), ['Doom-1.0'], 'no work folder is left')
        self.assertTrue(any(t.startswith('--ctl toast Extracting') for t in self.toasts()) and any(t.startswith('--ctl toast Extracted|Doom-1.0 is in') for t in self.toasts()), self.toasts())

    def test_every_supported_kind_unpacks_files_that_were_loose_in_it(self):
        src = self.d / 'src'; src.mkdir(); (src / 'a.txt').write_text('a'); (src / 'b.txt').write_text('b')
        made = {}
        for name, cmd in (('pack.tar', ['tar', '-C', str(src), '-cf']), ('pack.tar.gz', ['tar', '-C', str(src), '-czf']), ('pack.tgz', ['tar', '-C', str(src), '-czf']), ('pack.tar.xz', ['tar', '-C', str(src), '-cJf']),
                          ('pack.tar.bz2', ['tar', '-C', str(src), '-cjf']), ('pack.7z', ['7z', 'a', '-bd'])):
            p = self.down / name
            args = cmd + [str(p)] + (['a.txt', 'b.txt'] if cmd[0] == 'tar' else [str(src / 'a.txt'), str(src / 'b.txt')])
            subprocess.run(args, check=True, capture_output=True, cwd=str(src)); made[name] = p
        for name, p in made.items():
            r = self.run_action('extract', str(p)); self.assertEqual(r.returncode, 0, '%s: %s' % (name, r.stderr))
        for stem in ('pack', 'pack-2', 'pack-3', 'pack-4', 'pack-5', 'pack-6'):
            self.assertEqual(sorted(x.name for x in (self.games / stem).iterdir()), ['a.txt', 'b.txt'], stem)

    def test_an_existing_folder_is_never_written_into(self):
        z = self.down / 'g.zip'
        with zipfile.ZipFile(z, 'w') as f: f.writestr('x.txt', 'new')
        (self.games / 'g').mkdir(parents=True); (self.games / 'g/x.txt').write_text('old')
        r = self.run_action('extract', str(z)); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.games / 'g/x.txt').read_text(), 'old'); self.assertEqual((self.games / 'g-2/x.txt').read_text(), 'new')

    def test_links_that_point_out_of_the_folder_are_removed_and_inside_ones_kept(self):
        t = self.down / 'links.tar'
        src = self.d / 'src'; src.mkdir(); (src / 'real.txt').write_text('r')
        (src / 'abs').symlink_to('/etc/passwd'); (src / 'up').symlink_to('../../etc'); (src / 'inside').symlink_to('real.txt')
        subprocess.run(['tar', '-C', str(src), '-cf', str(t), 'real.txt', 'abs', 'up', 'inside'], check=True)
        r = self.run_action('extract', str(t)); self.assertEqual(r.returncode, 0, r.stderr)
        out = self.games / 'links'
        self.assertEqual(sorted(p.name for p in out.iterdir()), ['inside', 'real.txt'])
        self.assertTrue((out / 'inside').is_symlink() and os.readlink(out / 'inside') == 'real.txt')

    def test_an_entry_that_climbs_out_of_the_folder_does_not_get_out(self):
        z = self.down / 'evil.zip'
        with zipfile.ZipFile(z, 'w') as f:
            f.writestr('ok.txt', 'fine'); f.writestr('../escaped.txt', 'bad'); f.writestr('/tmp/wisp-absolute-escape.txt', 'bad')
        r = self.run_action('extract', str(z))
        self.assertFalse((self.games / 'escaped.txt').exists() or (self.d / 'escaped.txt').exists() or Path('/tmp/wisp-absolute-escape.txt').exists(), 'nothing outside the extraction folder was written')
        for p in self.d.rglob('escaped.txt'): self.assertIn(self.games / 'evil', p.parents, 'the tools neutralise the climb: whatever was written is inside the extraction folder (%s)' % p)

    def test_a_damaged_archive_leaves_nothing_and_says_so(self):
        z = self.down / 'bad.zip'; z.write_bytes(b'PK\x03\x04 this is not a zip file at all')
        r = self.run_action('extract', str(z)); self.assertEqual(r.returncode, 1)
        self.assertTrue(any('Could not unpack' in t for t in self.toasts()), self.toasts())
        self.assertEqual(list(self.games.iterdir()), [], 'no folder, no work folder')
        t = self.down / 'bad.tar.gz'; t.write_bytes(b'\x1f\x8b not gzip')
        self.assertEqual(self.run_action('extract', str(t)).returncode, 1); self.assertEqual(list(self.games.iterdir()), [])

    def test_an_empty_archive_and_an_unknown_kind_are_reported(self):
        e = self.down / 'empty.tar'; subprocess.run(['tar', '-cf', str(e), '-T', '/dev/null'], check=True)
        r = self.run_action('extract', str(e)); self.assertEqual(r.returncode, 1); self.assertTrue(any('was empty' in t for t in self.toasts()))
        self.assertEqual(list(self.games.iterdir()), [])
        u = self.down / 'thing.iso'; u.write_bytes(b'x')
        r = self.run_action('extract', str(u)); self.assertEqual(r.returncode, 1); self.assertTrue(any('do not know how to unpack' in t for t in self.toasts()))

    def test_a_hostile_archive_name_is_only_ever_a_safe_folder_name(self):
        z = self.down / 'bad name $(touch pwned) `x`.zip'
        with zipfile.ZipFile(z, 'w') as f: f.writestr('a.txt', 'a')
        r = self.run_action('extract', str(z)); self.assertEqual(r.returncode, 0, r.stderr)
        made = [p.name for p in self.games.iterdir()]; self.assertEqual(len(made), 1); self.assertRegex(made[0], r'^[A-Za-z0-9._-]+$')
        self.assertFalse((self.d / 'pwned').exists() or Path('pwned').exists())


class OpenTests(Fixture):
    def test_a_picture_is_shown_in_the_viewer(self):
        p = self.down / 'cat.png'; p.write_bytes(b'\x89PNG')
        r = self.run_action('open', str(p)); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((self.d / 'viewer.log').read_text().strip(), str(p))


MUTANTS = [
    ('the program is not made executable', 'chmod 755 "$dest" ||', 'true ||'),
    ('the name is not made safe', "safe() { printf '%s' \"$1\" | tr -c 'A-Za-z0-9._-' '_' | cut -c1-60; }", "safe() { printf '%s' \"$1\"; }"),
    ('the entry has no Terminal line', 'Terminal=false\\nCategories=Utility;\\n', 'Categories=Utility;\\n'),
    ('without FUSE the program is run as it is', 'if [ -e "$FUSE" ]; then run=$dest; else run="env APPIMAGE_EXTRACT_AND_RUN=1 $dest"; fi', 'run=$dest'),
    ('a link is followed', '[ -f "$file" ] && [ ! -L "$file" ] ||', '[ -f "$file" ] ||'),
    ('an existing games folder is written into', 'while [ -e "$dest" ]; do dest=$GAMES/$s-$n; n=$((n + 1)); done', 'true'),
    ('a link that points out is kept', 'case "$t" in /*|*..*) rm -f "$l" ;; esac', 'true'),
    ('a lone folder is not unwrapped', 'if [ "$count" -eq 1 ] && [ -d "$tmp/$top" ] && [ ! -L "$tmp/$top" ]; then', 'if false; then'),
    ('a failed unpack leaves its work folder', '|| { rm -rf "$tmp"; die "Extract" "Could not unpack $s. The archive may be damaged."; }', '|| die "Extract" "Could not unpack $s. The archive may be damaged."'),
    ('an empty archive is called done', '[ -n "$(ls -A "$tmp" 2>/dev/null)" ] || { rm -rf "$tmp"; die "Extract" "The archive was empty."; }', 'true'),
    ('an unknown kind is tried anyway', '*) die "Extract" "I do not know how to unpack that kind of archive." ;;', '*) kind=7z ;;'),
    ('the archive is deleted after extracting', 'say "Extracted" "$s is in $GAMES." 8 ;;', 'rm -f "$file"; say "Extracted" "$s is in $GAMES." 8 ;;'),
    ('the viewer gets nothing', 'exec "$VIEWER" "$file" ;;', 'exec "$VIEWER" ;;'),
    ('the toast is not shown when done', 'say "Installed" "$label is in the application menu." 6 ;;', 'true ;;'),
]


class MutantTests(unittest.TestCase):
    def test_every_edit_of_the_script_is_noticed(self):
        src = (ROOT / 'usr/bin/wisp-download-action').read_text(); survived = []
        d = Path(tempfile.mkdtemp(prefix='wisp-dlact-mut-', dir=WORK / 'tmp')); self.addCleanup(lambda: __import__('shutil').rmtree(d, True))
        for i, (what, old, new) in enumerate(MUTANTS):
            self.assertEqual(src.count(old), 1, 'the mutant "%s" matches %d times' % (what, src.count(old)))
            m = d / ('mutant%d' % i); m.write_text(src.replace(old, new))
            r = subprocess.run(['python3', '-I', str(Path(__file__)), 'InstallTests', 'ExtractTests', 'OpenTests'], capture_output=True, text=True, env={**os.environ, 'WISP_DOWNLOAD_ACTION_SCRIPT': str(m)}, timeout=300)
            if r.returncode == 0: survived.append(what)
        self.assertEqual(survived, [], 'the tests do not notice these changes to wisp-download-action')

    def test_the_script_is_executable_and_posix_shell(self):
        s = ROOT / 'usr/bin/wisp-download-action'
        self.assertTrue(os.access(s, os.X_OK)); self.assertTrue(s.read_text().startswith('#!/bin/sh\n'))
        self.assertEqual(subprocess.run(['sh', '-n', str(s)], capture_output=True, text=True).returncode, 0)


if __name__ == '__main__':
    unittest.main()
