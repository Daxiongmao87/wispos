"""Dev: re-run the exact game command Steam launched, from an environment dump written by a wrapper launch option, so a launch problem can be reproduced and
iterated on over SSH without the UI. The dump is made by setting the game's launch option to `<script> %command%`, where the script writes `date`, `pwd=...`, `argv: ...`,
`--- env` (the output of `env | sort`) and `--- fds` to a file in the game's compatdata directory and then `exec "$@"` (see docs/WISPOS-NOTES.md, Worms Armageddon).
usage: replay_exact.py DUMP_FILE [SECONDS] [KEY=VALUE ...]     KEY=VALUE overrides/adds environment (for example PROTON_LOG=1 PROTON_LOG_DIR=/path WINEDEBUG=err+all)
The command is rebuilt from the dump's argv by the `--` separators and by the known tool paths (paths contain spaces, so argv cannot simply be split on blanks).
Output goes to <dump>.replay.log. Exit status is printed; a still-running command is killed after SECONDS (default 120)."""
import os, re, subprocess, sys, time


def parse(text):
    """(cwd, argv_text, env dict) from a dump."""
    cwd = re.search(r'^pwd=(.*)$', text, re.M).group(1)
    argv = re.search(r'^argv: (.*)$', text, re.M).group(1)
    env_part = text.split('--- env\n', 1)[1].split('\n--- fds', 1)[0]
    env = {}
    for line in env_part.splitlines():
        k, sep, v = line.partition('=')
        if sep and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', k): env[k] = v
    return cwd, argv, env


def split_argv(argv):
    """Split Steam's launch command on its ' -- ' separators, keeping paths with blanks intact: wrapper, reaper+SteamLaunch+AppId, entry-point+verb, proton+verb+exe."""
    parts = argv.split(' -- ')
    out = []
    for i, part in enumerate(parts):
        if i == 0: out += [part]                                              # steam-launch-wrapper
        elif i == 1: out += part.split(' ')                                   # reaper SteamLaunch AppId=N (no blanks in these)
        elif i == 2: out += part.split(' ')                                   # _v2-entry-point --verb=...
        else:
            m = re.match(r'^(.*/proton) (\S+) (.*)$', part)                 # <tool dir>/proton <verb> <exe path with blanks>
            out += list(m.groups()) if m else [part]
        if i < len(parts) - 1: out.append('--')
    return out


def main(argv):
    dump = argv[0]; seconds = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 120
    over = dict(a.split('=', 1) for a in argv[1:] if '=' in a)
    cwd, command, env = parse(open(dump, errors='replace').read())
    env.update(over)
    cmd = split_argv(command)
    out = dump + '.replay.log'
    log = open(out, 'wb'); log.write((repr(cmd) + '\n').encode()); log.flush()
    t0 = time.time(); p = subprocess.Popen(cmd, env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    try: rc = p.wait(timeout=seconds)
    except subprocess.TimeoutExpired: p.kill(); rc = 'killed after %ds (still running)' % seconds
    log.write(('\n--- exit %s after %.1fs\n' % (rc, time.time() - t0)).encode()); log.close()
    print('exit', rc, 'log', out)


if __name__ == '__main__': main(sys.argv[1:])
