#!/usr/bin/env python3
"""Summarise a `busctl monitor` capture (os/dev/bus-capture.sh): which SteamOS-related names Steam asked for and what it called.
usage: bus-summary.py CAPTURE.txt [...]
Prints, per capture: the method calls to com.steampowered.* (destination, member, count), the services it tried to start (StartServiceByName), and the errors that came back
for them (so "not provided by any .service files" shows what is still missing). Pure text parsing: the capture format is busctl's default."""
import collections, re, sys


def messages(text):
    return re.split(r'\n(?=‣ Type=)', text)


def summarise(text):
    calls, starts, errors = collections.Counter(), [], collections.Counter()
    for m in messages(text):
        kind = re.search(r'Type=(\w+)', m)
        if not kind: continue
        dest = (re.search(r'Destination=(\S+)', m) or [None, '-'])[1]
        member = (re.search(r'Member=(\S+)', m) or [None, '-'])[1]
        if kind.group(1) == 'method_call':
            if member == 'StartServiceByName':
                names = re.findall(r'STRING "([^"]*)"', m)
                if names and 'steampowered' in names[0]: starts.append(names[0])
            elif dest.startswith('com.steampowered.'): calls[(dest, member)] += 1
        elif kind.group(1) == 'error':
            err = re.search(r'ErrorName=(\S+)\s+ErrorMessage="([^"]*)"', m)
            if err and 'steampowered' in err.group(2): errors[(err.group(1), err.group(2))] += 1
    return calls, starts, errors


def main(paths):
    for path in paths:
        calls, starts, errors = summarise(open(path, errors='replace').read())
        print('==', path)
        for (dest, member), n in sorted(calls.items()): print(f'  call   {n:4d}  {dest} {member}')
        for name in starts: print(f'  start        {name}')
        for (name, msg), n in sorted(errors.items()): print(f'  error  {n:4d}  {name}: {msg}')
        if not (calls or starts or errors): print('  (no SteamOS-related traffic)')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]) if len(sys.argv) > 1 else sys.exit(__doc__))
