#!/bin/bash
# Dev: set or clear Steam launch options, ON the device (copy to /storage/wispos/dev/). usage: set-launch-option.sh APPID 'OPTION with %command%' [APPID2 'OPTION2' ...]   (empty option = clear it; ONE Steam restart for all pairs)
# Why: a launch option is the way to pass a DIAGNOSTIC environment to one game (e.g. `PROTON_LOG=1 WINEDEBUG=err+all %command%`) without touching the shipped defaults. It is never
# shipped (docs/WISPOS-NORTH-STAR.md, general compatibility): this helper exists so the diagnostic can be put in and, just as important, taken out again.
# Steam rewrites userdata/*/config/localconfig.vdf while it runs, so the script stops wisp-steam, edits, and starts it again (no reboot; the UI is back after ~90-110 s). It turns the
# product watchdog off for that gap and back on at the end. It replaces any existing LaunchOptions line of an app, and refuses if the app's block is not in the file.
[ $# -ge 2 ] || { echo "usage: set-launch-option.sh APPID 'OPTION' [APPID2 'OPTION2' ...]"; exit 2; }
touch /run/wisp-steam-watch-off
systemctl stop wisp-steam; n=0; while [ -n "$(pidof steam)" ] && [ $n -lt 30 ]; do sleep 2; n=$((n+1)); done
sh /storage/wispos/dev/kill-game.sh >/dev/null 2>&1
F=$(ls /storage/wispos/home/.local/share/Steam/userdata/*/config/localconfig.vdf | head -1)
cp "$F" "$F.before-launch-option"
while [ $# -ge 2 ]; do
python3 - "$F" "$1" "$2" <<'PY'
import re, sys
p, app, opt = sys.argv[1:4]; s = open(p).read()
m = re.search(r'(\t+)"%s"\n\1\{\n' % re.escape(app), s)
if not m: sys.exit('app %s has no block in %s: refusing to edit' % (app, p))
ind = m.group(1)
end = s.index('\n%s}\n' % ind, m.end())                      # this app's block ends at its closing brace, same indentation
block = s[m.end():end + 1]
block = re.sub(r'\t+"LaunchOptions"\t\t"[^\n]*\n', '', block)
if opt: block = '%s\t"LaunchOptions"\t\t"%s"\n' % (ind, opt.replace('\\', '\\\\').replace('"', '\\"')) + block
open(p, 'w').write(s[:m.end()] + block + s[end + 1:])
print('app %s launch option: %s' % (app, opt or '(cleared)'))
PY
shift 2
done
grep -a "LaunchOptions" "$F" | head -n 6
systemctl start wisp-steam --no-block; echo "steam started $(date +%T)"
rm -f /run/wisp-steam-watch-off
