"""The browser launcher (os/rootfs/usr/bin/wisp-browser) and what it installs: run for real against a fake download (a tarball built here), a fake desktop shell that records the toasts, and
a temporary card. Nothing is downloaded and nothing outside the work directory is written. Also checks the shipped files: the lock (a pinned version, URL and hash that agree), the Firefox
policies (what they must and must not do), the preferences and the start page."""
import hashlib, json, os, re, subprocess, tempfile, time, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE / 'rootfs'
WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))
SCRIPT = Path(os.environ.get('WISP_BROWSER_SCRIPT') or ROOT / 'usr/bin/wisp-browser')       # a mutant of it, when the tests test themselves
MAKE_EXEC = 0o755


class Fixture(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix='wisp-browser-', dir=WORK / 'tmp'))
        self.addCleanup(lambda: __import__('shutil').rmtree(self.d, True))
        self.card = self.d / 'card'; self.card.mkdir()
        self.version = '153.4.0esr'
        self.make_tarball(self.version)
        self.write_lock()
        (self.d / 'curl').write_text('''#!/bin/sh
echo "$@" >> "%(d)s/curl.log"
case "$*" in
  *-fsI*) [ -z "$OFFLINE" ] || exit 6; exit 0;;
esac
out=""; while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out=$2; shift; done
[ -z "$SLOW" ] || sleep "$SLOW"
[ -z "$DOWNLOAD_FAILS" ] || exit 22
cp "%(d)s/download.tar.xz" "$out"
''' % {'d': self.d})
        (self.d / 'dock').write_text('#!/bin/sh\necho "$@" >> "%(d)s/toasts.log"\n' % {'d': self.d})
        for n in ('curl', 'dock'): os.chmod(self.d / n, MAKE_EXEC)
        (self.d / 'policies.json').write_text((ROOT / 'usr/lib/wispos/browser-policies.json').read_text())
        (self.d / 'user.js').write_text((ROOT / 'usr/lib/wispos/browser-user.js').read_text())

    def make_tarball(self, version, corrupt=False):
        src = self.d / 'tar-src'; (src / 'firefox/distribution').mkdir(parents=True, exist_ok=True)
        fx = src / 'firefox/firefox'
        fx.write_text('#!/bin/sh\necho "firefox %s: $@" >> "%s/launched.log"\necho "MOZ_ENABLE_WAYLAND=$MOZ_ENABLE_WAYLAND" >> "%s/launched.log"\n' % (version, self.d, self.d))
        os.chmod(fx, MAKE_EXEC)
        (src / 'firefox/distribution/distribution.ini').write_text('[Global]\nid=mozilla\n')
        t = self.d / 'download.tar.xz'
        subprocess.run(['tar', '-C', str(src), '-cJf', str(t), 'firefox'], check=True)
        if corrupt: t.write_bytes(t.read_bytes()[:-40] + b'x' * 40)
        self.sha = hashlib.sha256(t.read_bytes()).hexdigest()

    def write_lock(self, version=None, sha=None):
        v = version or self.version
        (self.d / 'lock.json').write_text(json.dumps({'name': 'Firefox ESR', 'version': v, 'url': 'https://example.invalid/firefox-%s.tar.xz' % v, 'sha256': sha or self.sha}, indent=1))

    def env(self, extra=None, min_mb='1'):
        return {**os.environ, 'WISP_BROWSER_LOCK': str(self.d / 'lock.json'), 'WISP_BROWSER_ROOT': str(self.card / 'browser'), 'WISP_BROWSER_POLICIES': str(self.d / 'policies.json'),
                'WISP_BROWSER_USERJS': str(self.d / 'user.js'), 'WISP_BROWSER_DOCK': str(self.d / 'dock'), 'WISP_BROWSER_CURL': str(self.d / 'curl'), 'WISP_BROWSER_MIN_MB': min_mb, **(extra or {})}

    def run_browser(self, *args, env=None, min_mb='1'):
        return subprocess.run(['sh', str(SCRIPT), *args], capture_output=True, text=True, env=self.env(env, min_mb), timeout=120)

    def toasts(self):
        f = self.d / 'toasts.log'; return f.read_text().splitlines() if f.exists() else []

    def launched(self):
        f = self.d / 'launched.log'; return f.read_text().splitlines() if f.exists() else []

    def downloads(self):
        f = self.d / 'curl.log'; return [l for l in f.read_text().splitlines() if '-fsI' not in l] if f.exists() else []


class LauncherTests(Fixture):
    def test_the_first_launch_downloads_verifies_unpacks_and_starts_the_browser(self):
        r = self.run_browser('https://example.org/')
        self.assertEqual(r.returncode, 0, r.stderr)
        b = self.card / 'browser'
        self.assertTrue((b / 'firefox-153.4.0esr/firefox/firefox').exists())
        self.assertTrue((b / 'firefox-153.4.0esr/.complete').exists())
        self.assertEqual(json.loads((b / 'firefox-153.4.0esr/firefox/distribution/policies.json').read_text())['policies']['DefaultDownloadDirectory'], '/storage/downloads')
        self.assertTrue((b / 'firefox-153.4.0esr/firefox/distribution/distribution.ini').exists(), 'the browser\'s own files are kept beside the policies')
        self.assertEqual(self.launched(), ['firefox 153.4.0esr: --profile %s --class wisp-browser --name wisp-browser https://example.org/' % (b / 'profile'), 'MOZ_ENABLE_WAYLAND=1'])
        self.assertEqual(len(self.downloads()), 1); self.assertIn('-o %s' % (b / '.download/firefox.tar.xz'), self.downloads()[0])
        self.assertFalse((b / '.download').exists() or (b / '.new').exists(), 'no half-done work is left on the card')
        t = self.toasts()
        self.assertTrue(t[0].startswith('--ctl toast Browser|Downloading Firefox'), t)
        self.assertTrue(any('Unpacking' in x for x in t) and t[-1].startswith('--ctl toast Browser|Starting the browser'), t)

    def test_a_later_launch_just_starts_it_without_downloading_or_asking_the_network(self):
        self.run_browser()
        for n in ('toasts.log', 'curl.log', 'launched.log'): (self.d / n).unlink()
        r = self.run_browser(); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.downloads(), [])
        self.assertFalse((self.d / 'curl.log').exists(), 'it does not even ask the network')
        self.assertEqual(len(self.toasts()), 1); self.assertIn('Starting', self.toasts()[0])
        self.assertEqual(len(self.launched()), 2)

    def test_the_profile_gets_the_preferences_and_the_card_is_written_only_when_they_change(self):
        self.run_browser()
        uj = self.card / 'browser/profile/user.js'
        self.assertEqual(uj.read_text(), (ROOT / 'usr/lib/wispos/browser-user.js').read_text())
        old = os.stat(uj).st_mtime_ns; time.sleep(0.05)
        self.run_browser()
        self.assertEqual(os.stat(uj).st_mtime_ns, old, 'unchanged preferences are not rewritten')
        (self.d / 'user.js').write_text('user_pref("a", 1);\n'); self.run_browser()
        self.assertEqual(uj.read_text(), 'user_pref("a", 1);\n', 'a new release\'s preferences reach an existing profile')

    def test_a_corrupt_download_installs_nothing_and_is_reported_and_the_next_try_works(self):
        good_sha = self.sha
        self.make_tarball(self.version, corrupt=True); self.write_lock(sha=good_sha)
        r = self.run_browser(); self.assertEqual(r.returncode, 1)
        self.assertIn('checksum mismatch', r.stderr)
        self.assertTrue(any('checksum mismatch' in t for t in self.toasts()))
        b = self.card / 'browser'
        self.assertFalse((b / 'firefox-153.4.0esr').exists(), 'nothing was installed'); self.assertFalse((b / '.download').exists() or (b / '.new').exists())
        self.assertEqual(self.launched(), [])
        self.make_tarball(self.version); self.write_lock()
        r = self.run_browser(); self.assertEqual(r.returncode, 0, r.stderr); self.assertTrue((b / 'firefox-153.4.0esr/.complete').exists())

    def test_offline_is_reported_before_anything_is_downloaded(self):
        r = self.run_browser(env={'OFFLINE': '1'})
        self.assertEqual(r.returncode, 1); self.assertTrue(any('No connection' in t for t in self.toasts()), self.toasts())
        self.assertEqual(self.downloads(), []); self.assertEqual(self.launched(), [])
        self.assertFalse((self.card / 'browser/firefox-153.4.0esr').exists())

    def test_a_failed_download_is_reported_and_leaves_no_partial_files(self):
        r = self.run_browser(env={'DOWNLOAD_FAILS': '1'})
        self.assertEqual(r.returncode, 1); self.assertTrue(any('download failed' in t for t in self.toasts()))
        b = self.card / 'browser'
        self.assertFalse((b / '.download').exists() or (b / '.new').exists())
        self.assertEqual(sorted(p.name for p in b.iterdir() if p.name != '.lock'), [])

    def test_too_little_room_is_reported_before_anything_is_downloaded(self):
        r = self.run_browser(min_mb='999999999')
        self.assertEqual(r.returncode, 1); self.assertTrue(any('Not enough room' in t for t in self.toasts()))
        self.assertEqual(self.downloads(), [])

    def test_a_half_installed_browser_is_installed_again(self):
        b = self.card / 'browser'; (b / 'firefox-153.4.0esr/firefox').mkdir(parents=True)
        fx = b / 'firefox-153.4.0esr/firefox/firefox'
        fx.write_text('#!/bin/sh\necho broken > "%s/broken"\n' % self.d); os.chmod(fx, MAKE_EXEC)
        r = self.run_browser(); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.d / 'broken').exists(), 'the unfinished copy (no completion mark) was not run')
        self.assertEqual(len(self.downloads()), 1); self.assertTrue((b / 'firefox-153.4.0esr/.complete').exists())

    def test_a_newer_lock_replaces_the_old_build_and_keeps_the_profile(self):
        self.run_browser()
        b = self.card / 'browser'; (b / 'profile/places.sqlite').write_text('history')
        self.version = '154.0esr'; self.make_tarball(self.version); self.write_lock()
        r = self.run_browser(); self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((b / 'firefox-154.0esr/.complete').exists())
        self.assertFalse((b / 'firefox-153.4.0esr').exists(), 'one version on the card at a time')
        self.assertEqual((b / 'profile/places.sqlite').read_text(), 'history', 'the user\'s profile is not touched')

    def test_two_launches_at_once_download_once(self):
        env = self.env({'SLOW': '1'})
        ps = [subprocess.Popen(['sh', str(SCRIPT)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
        for p in ps:
            p.communicate(timeout=120); self.assertEqual(p.returncode, 0)
        self.assertEqual(len(self.downloads()), 1, 'the second launch waited for the first and found the browser installed')
        self.assertEqual(len(self.launched()), 4)

    def test_a_package_that_does_not_hold_the_browser_is_refused_even_with_the_right_checksum(self):
        src = self.d / 'other-src'; (src / 'something').mkdir(parents=True); (src / 'something/else').write_text('x')
        t = self.d / 'download.tar.xz'
        subprocess.run(['tar', '-C', str(src), '-cJf', str(t), 'something'], check=True)
        self.sha = hashlib.sha256(t.read_bytes()).hexdigest(); self.write_lock()
        r = self.run_browser(); self.assertEqual(r.returncode, 1)
        self.assertTrue(any('not what was expected' in x for x in self.toasts()), self.toasts())
        b = self.card / 'browser'
        self.assertFalse((b / 'firefox-153.4.0esr').exists() or (b / '.new').exists() or (b / '.download').exists())

    def test_an_incomplete_lock_is_refused_with_the_reason(self):
        (self.d / 'lock.json').write_text('{"version": "1"}')
        r = self.run_browser(); self.assertEqual(r.returncode, 1); self.assertIn('lock file is incomplete', r.stderr)

    def test_without_the_desktop_shell_the_message_goes_to_stderr(self):
        r = self.run_browser(env={'WISP_BROWSER_DOCK': '/nonexistent/wisp-dock', 'OFFLINE': '1'})
        self.assertEqual(r.returncode, 1); self.assertIn('wisp-browser:', r.stderr)


class ShippedFilesTests(unittest.TestCase):
    def test_the_lock_pins_one_build_whose_version_url_and_hash_agree(self):
        lock = json.loads((ROOT / 'usr/share/wispos/browser.lock.json').read_text())
        self.assertRegex(lock['sha256'], r'^[0-9a-f]{64}$')
        self.assertTrue(lock['url'].startswith('https://archive.mozilla.org/pub/firefox/releases/%s/linux-aarch64/' % lock['version']), 'Mozilla\'s own aarch64 build')
        self.assertTrue(lock['url'].endswith('firefox-%s.tar.xz' % lock['version']))
        self.assertTrue(lock['version'].endswith('esr'), 'ESR: it honours the extension settings of the policies')
        self.assertGreater(lock['size'], 50_000_000)

    def test_the_policies_keep_the_browser_from_phoning_home_updating_itself_or_asking_questions(self):
        p = json.loads((ROOT / 'usr/lib/wispos/browser-policies.json').read_text())['policies']
        for k in ('DisableAppUpdate', 'DisableTelemetry', 'DisableFirefoxStudies', 'DisablePocket', 'DontCheckDefaultBrowser', 'NoDefaultBookmarks'): self.assertIs(p[k], True, k)
        self.assertEqual(p['OverrideFirstRunPage'], ''); self.assertEqual(p['DefaultDownloadDirectory'], '/storage/downloads'); self.assertIs(p['PromptForDownloadLocation'], False)
        self.assertEqual(p['Homepage']['URL'], 'file:///usr/share/wispos/startpage/index.html')
        self.assertEqual(p['ExtensionSettings']['uBlock0@raymondhill.net']['installation_mode'], 'force_installed')

    def test_the_preferences_are_the_documented_ones_and_the_homepage_matches_the_policy(self):
        js = (ROOT / 'usr/lib/wispos/browser-user.js').read_text()
        prefs = dict(re.findall(r'user_pref\("([^"]+)",\s*([^)]*)\);', js))
        for k in ('layout.css.devPixelsPerPx', 'browser.cache.disk.enable', 'browser.sessionstore.interval', 'browser.tabs.unloadOnLowMemory', 'browser.download.dir', 'browser.startup.homepage'):
            self.assertIn(k, prefs, k)
        self.assertEqual(prefs['browser.cache.disk.enable'], 'false', 'the card is not a cache')
        self.assertGreaterEqual(int(prefs['browser.sessionstore.interval']), 300000, 'a session save is rare')
        self.assertEqual(prefs['browser.download.dir'], '"/storage/downloads"')
        self.assertEqual(prefs['browser.startup.homepage'], '"file:///usr/share/wispos/startpage/index.html"')

    def test_the_start_page_is_static_uses_the_palette_and_has_a_search_and_tiles(self):
        html = (ROOT / 'usr/share/wispos/startpage/index.html').read_text()
        self.assertNotIn('<script', html)
        self.assertIn('--bg: #0e141b', html); self.assertIn('Liberation Sans', html)
        self.assertEqual(html.count('class="tile"'), 8); self.assertIn('type="search"', html)
        for url in re.findall(r'href="(https?://[^"]+)"', html): self.assertTrue(url.startswith('https://'), url)

    def test_the_launcher_is_executable_and_posix_shell(self):
        self.assertTrue(os.access(SCRIPT, os.X_OK)); self.assertTrue(SCRIPT.read_text().startswith('#!/bin/sh\n'))
        r = subprocess.run(['sh', '-n', str(SCRIPT)], capture_output=True, text=True); self.assertEqual(r.returncode, 0, r.stderr)


MUTANTS = [
    ('the checksum is not checked', '[ "$got" = "$sha" ] ||', '[ 1 ] ||'),
    ('an unfinished install is run', '[ -f "$dir/.complete" ] && [ -x "$bin" ] || install_browser', '[ -x "$bin" ] || install_browser'),
    ('there is no installer lock', 'flock -x 9', 'true'),
    ('the preferences are rewritten every time', 'cmp -s "$USERJS" "$ROOT/profile/user.js" 2>/dev/null ||', 'false ||'),
    ('the network is not asked first', '"$CURL" -fsI --max-time 10 "$url" >/dev/null 2>&1 || die "No connection. Connect Wi-Fi (quick settings) and try the browser again."', 'true'),
    ('a failed download leaves its files', '|| { rm -rf "$ROOT/.download" "$ROOT/.new"; die "The download failed."; }', '|| die "The download failed."'),
    ('old builds stay on the card', 'for old in "$ROOT"/firefox-*; do [ "$old" = "$dir" ] || rm -rf "$old"; done', 'true'),
    ('the policies are not written', 'cp "$POLICIES" "$ROOT/.new/firefox/distribution/policies.json" ||', 'true ||'),
    ('the browser has no class of its own', '--class wisp-browser --name wisp-browser', ''),
    ('free space is not checked', '[ "${free_mb:-0}" -ge "$MIN_MB" ] ||', 'true ||'),
    ('Wayland is not asked for', 'export MOZ_ENABLE_WAYLAND=1', 'true'),
    ('the profile is the default one', '--profile "$ROOT/profile"', ''),
    ('a bad package is accepted', '[ -x "$ROOT/.new/firefox/firefox" ] ||', 'true ||'),
]


class MutantTests(unittest.TestCase):
    def test_every_edit_of_the_launcher_is_noticed(self):
        src = (ROOT / 'usr/bin/wisp-browser').read_text(); survived = []
        d = Path(tempfile.mkdtemp(prefix='wisp-browser-mut-', dir=WORK / 'tmp')); self.addCleanup(lambda: __import__('shutil').rmtree(d, True))
        for i, (what, old, new) in enumerate(MUTANTS):
            self.assertEqual(src.count(old), 1, 'the mutant "%s" matches %d times' % (what, src.count(old)))
            m = d / ('mutant%d' % i); m.write_text(src.replace(old, new))
            r = subprocess.run(['python3', '-I', str(Path(__file__)), 'LauncherTests'], capture_output=True, text=True, env={**os.environ, 'WISP_BROWSER_SCRIPT': str(m)}, timeout=300)
            if r.returncode == 0: survived.append(what)
        self.assertEqual(survived, [], 'the tests do not notice these changes to wisp-browser')


if __name__ == '__main__':
    unittest.main()
