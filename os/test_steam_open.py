"""wisp-steam-open (os/rootfs/usr/bin/wisp-steam-open): hands a steam:// URL to the RUNNING Steam client the way Steam itself takes one: the client binary started again with `-ifrunning URL`, with the
running client's own environment. Proved with a fake /proc (one process named `steam` with an environ file) and a fake client binary that records its arguments, directory and one environment value:
the right binary, `-ifrunning`, the URL and that environment; nothing is started when Steam is not running (exit 1); anything that is not a steam:// URL is refused (exit 2) so the tool cannot be used
to pass arbitrary arguments; a missing binary is a clean failure. Mutants must all be caught."""
import os, shutil, subprocess, sys, tempfile, time, unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROGRAM = Path(os.environ.get('WISP_STEAM_OPEN_PATH', HERE / 'rootfs/usr/bin/wisp-steam-open'))
WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))


class Behaviour(unittest.TestCase):
    def setUp(self):
        (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
        self.d = Path(tempfile.mkdtemp(prefix='wispos-steamopen-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, self.d, True)
        self.root, self.proc, self.log = self.d / 'steam', self.d / 'proc', self.d / 'client.log'
        (self.root / 'steamrtarm64').mkdir(parents=True); self.proc.mkdir()
        client = self.root / 'steamrtarm64' / 'steam'
        client.write_text('#!/bin/bash\necho "args: $*" > "%s"\necho "cwd: $PWD" >> "%s"\necho "bus: $DBUS_SESSION_BUS_ADDRESS" >> "%s"\n' % (self.log, self.log, self.log)); client.chmod(0o755)

    def process(self, pid, comm, environ=b''):
        (self.proc / str(pid)).mkdir(); (self.proc / str(pid) / 'comm').write_text(comm + '\n'); (self.proc / str(pid) / 'environ').write_bytes(environ)

    def run_tool(self, *args):
        r = subprocess.run([sys.executable, '-I', str(PROGRAM), *args], capture_output=True, text=True, timeout=20,
                           env={**os.environ, 'WISP_STEAM_ROOT': str(self.root), 'WISP_PROC_ROOT': str(self.proc)})
        for _ in range(40 if r.returncode == 0 else 0):                           # the client is started and not waited for: wait for its record only after a success
            if self.log.exists() and self.log.read_text().count('\n') >= 3: break
            time.sleep(0.05)
        return r

    def test_the_url_goes_to_the_client_with_the_running_clients_environment(self):
        self.process(100, 'dbus-run-sessi'); self.process(101, 'steam', b'HOME=/x\0DBUS_SESSION_BUS_ADDRESS=unix:path=/tmp/bus-xyz\0LANG=C\0'); self.process(102, 'steamwebhelper')
        r = self.run_tool('steam://nav/games/details/217200')
        self.assertEqual(r.returncode, 0, r.stderr)
        text = self.log.read_text()
        self.assertIn('args: -ifrunning steam://nav/games/details/217200', text)
        self.assertIn('cwd: %s' % self.root, text)
        self.assertIn('bus: unix:path=/tmp/bus-xyz', text)                         # the environment of the process named steam, not of this tool

    def test_nothing_is_started_when_steam_is_not_running(self):
        self.process(100, 'dbus-run-sessi'); self.process(102, 'steamwebhelper')
        r = self.run_tool('steam://open/main'); self.assertEqual(r.returncode, 1); self.assertFalse(self.log.exists())

    def test_only_steam_urls_are_accepted(self):
        self.process(101, 'steam', b'HOME=/x\0')
        for bad in ('http://example.com', '-ifrunning', 'steam://', 'steam://a b', 'steam://a;b', 'file:///etc/passwd', 'STEAM://x', ''):
            self.log.unlink(missing_ok=True); r = self.run_tool(bad); self.assertEqual(r.returncode, 2, bad); self.assertFalse(self.log.exists(), bad)
        self.assertEqual(self.run_tool().returncode, 2); self.assertEqual(self.run_tool('steam://open/main', 'steam://x').returncode, 2)       # exactly one argument

    def test_a_missing_client_binary_is_a_clean_failure(self):
        self.process(101, 'steam', b'HOME=/x\0'); (self.root / 'steamrtarm64' / 'steam').unlink()
        r = self.run_tool('steam://open/main'); self.assertEqual(r.returncode, 1); self.assertIn('cannot start', r.stderr)

    def test_an_unreadable_environment_is_a_clean_failure(self):
        self.process(101, 'steam'); (self.proc / '101' / 'environ').unlink()
        r = self.run_tool('steam://open/main'); self.assertEqual(r.returncode, 1); self.assertIn('environment', r.stderr)


MUTANTS = [
    ('no URL check', "if len(argv) != 1 or not re.fullmatch(r'steam://[A-Za-z0-9_./:?=&%#@+-]+', argv[0]):", "if False:"),
    ('a prefix of the URL is enough', "re.fullmatch(r'steam://[A-Za-z0-9_./:?=&%#@+-]+', argv[0])", "re.match(r'steam://[A-Za-z0-9_./:?=&%#@+-]+', argv[0])"),
    ('extra arguments are accepted', "if len(argv) != 1 or not", "if len(argv) < 1 or not"),
    ('the wrong flag', "'-ifrunning', argv[0]", "'-ifrunnning', argv[0]"),
    ('the tool runs in the wrong directory', "cwd=ROOT, env=env", "cwd='/', env=env"),
    ('the tool keeps its own environment', "cwd=ROOT, env=env", "cwd=ROOT, env=None"),
    ('any process is Steam', "if f.read().strip() == 'steam': return pid", "return pid"),
    ('the helper process counts as Steam', "if f.read().strip() == 'steam': return pid", "if f.read().strip().startswith('steam'): return pid"),
    ('a missing client is not caught', "    except OSError as e:\n        print('wisp-steam-open: cannot start the client: %s' % e, file=sys.stderr); return 1\n", "    except KeyError as e:\n        return 1\n"),
    ('success when Steam is not running', "        print('wisp-steam-open: Steam is not running', file=sys.stderr); return 1", "        print('wisp-steam-open: Steam is not running', file=sys.stderr); return 0"),
]


class Mutants(unittest.TestCase):
    def test_every_mutant_is_caught(self):
        if 'WISP_STEAM_OPEN_PATH' in os.environ: self.skipTest('running a mutant already')
        (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix='wispos-sopmut-', dir=WORK / 'tmp')); self.addCleanup(shutil.rmtree, tmp, True)
        text = PROGRAM.read_text(); survivors = []
        for i, (what, old, new) in enumerate(MUTANTS):
            self.assertEqual(text.count(old), 1, 'mutant %r: the pattern must occur exactly once (%d)' % (what, text.count(old)))
            m = tmp / ('m%d' % i); m.write_text(text.replace(old, new)); m.chmod(0o755)
            r = subprocess.run([sys.executable, '-I', str(Path(__file__).resolve()), 'Behaviour'], env={**os.environ, 'WISP_STEAM_OPEN_PATH': str(m)}, capture_output=True, text=True, timeout=600)
            if r.returncode == 0: survivors.append(what)
        self.assertEqual(survivors, [], 'mutants that no test noticed')


if __name__ == '__main__':
    unittest.main()
