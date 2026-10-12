#!/usr/bin/env python3
"""Run every host test of the repository and say plainly what passed, failed or was skipped.

    os/run-tests.py [-j N] [--serial-rerun] [--only SUBSTRING] [--list]

Runs, each as its own process with TMPDIR on the HDD work tree (nothing goes to /tmp or the root filesystem): os/test_*.py, steam/test_*.py, tools/test_*.py,
initramfs/test_*.py, and image/selfcheck.sh. N files at a time (default 4: several of the suites build native programs or start a headless compositor, and a few check timings,
which a loaded machine can spoil). A file that fails when run alongside others is run again ALONE when --serial-rerun is given and is then reported as `PASS (alone)`: that is a
flaky or load-sensitive test and it is listed as such at the end, never silently counted as a pass. Exit status is 0 only when every file passed (alone or not).
Not included (they boot images and take minutes each): os/qemu-test.sh, os/qemu-update-test.sh, os/boot/qemu_splash_test.py. A file whose tests were all skipped (a tool the host
lacks) is reported as SKIP with the reason, not as a pass.
Output: one line per file with seconds; the failing files' output at the end; the last line is the verdict."""
import argparse, concurrent.futures, os, re, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = Path(os.environ.get('WISP_WORK', '/mnt/hdd/wispos-staging/wispos-os-20261007'))
TIMEOUT = 1800


def files():
    out = []
    for pattern in ('os/test_*.py', 'steam/test_*.py', 'tools/test_*.py', 'initramfs/test_*.py'): out += sorted(ROOT.glob(pattern))
    return out + [f for f in (ROOT / 'image/selfcheck.sh',) if f.exists()]          # the public tree has no image/ directory


def run(path, env):
    t0 = time.time()
    cmd = ['bash', str(path)] if path.suffix == '.sh' else [sys.executable, '-I', str(path)]
    try: r = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=TIMEOUT, errors='replace')
    except subprocess.TimeoutExpired as e: return path, 'FAIL', time.time() - t0, 'timed out after %d s\n%s' % (TIMEOUT, (e.stdout or '')[-2000:] if isinstance(e.stdout, str) else '')
    text = r.stdout + r.stderr
    skipped = re.search(r'skipped=(\d+)', text); ran = re.search(r'^Ran (\d+) tests?', text, re.M)
    if r.returncode != 0: status = 'FAIL'
    elif ran and skipped and int(skipped.group(1)) >= int(ran.group(1)) > 0: status = 'SKIP'
    else: status = 'PASS'
    return path, status, time.time() - t0, text


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('-j', type=int, default=4); ap.add_argument('--serial-rerun', action='store_true'); ap.add_argument('--only', default=''); ap.add_argument('--list', action='store_true')
    a = ap.parse_args(argv)
    todo = [f for f in files() if a.only in str(f)]
    if a.list:
        for f in todo: print(f.relative_to(ROOT))
        return 0
    if not str(WORK).startswith('/mnt/hdd/'): sys.exit('run-tests: the work directory must be on the HDD')
    (WORK / 'tmp').mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'TMPDIR': str(WORK / 'tmp'), 'PYTHONDONTWRITEBYTECODE': '1'}
    t0 = time.time(); results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=a.j) as ex:
        for path, status, secs, text in ex.map(lambda f: run(f, env), todo):
            results[path] = [status, secs, text]; print('%-4s %6.1fs  %s' % (status, secs, path.relative_to(ROOT)), flush=True)
    flaky = []
    if a.serial_rerun:
        for path, (status, secs, text) in list(results.items()):
            if status != 'FAIL': continue
            _, again, secs2, text2 = run(path, env)
            print('%-4s %6.1fs  %s  (rerun alone)' % ('PASS' if again != 'FAIL' else 'FAIL', secs2, path.relative_to(ROOT)), flush=True)
            if again != 'FAIL': results[path] = ['PASS (alone)', secs2, text2]; flaky.append(path)
            else: results[path][2] = text + '\n--- rerun alone:\n' + text2
    bad = [p for p, (s, _, _) in results.items() if s == 'FAIL']
    for p in bad: print('\n===== %s =====\n%s' % (p.relative_to(ROOT), results[p][2][-3000:]))
    n = {k: sum(1 for s, _, _ in results.values() if s == k) for k in ('PASS', 'PASS (alone)', 'SKIP', 'FAIL')}
    print('\n%d files: %d passed, %d passed only when run alone%s, %d skipped, %d failed  (%.0f s)' % (len(results), n['PASS'], n['PASS (alone)'], (' (' + ', '.join(str(p.relative_to(ROOT)) for p in flaky) + ')') if flaky else '', n['SKIP'], n['FAIL'], time.time() - t0))
    for p, (s, _, text) in results.items():
        if s == 'SKIP': print('  skipped: %s' % p.relative_to(ROOT))
    print('VERDICT: ' + ('FAIL' if bad else 'PASS'))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
