#!/bin/sh
# Dev (never shipped), runs ON the device: put extra Chromium switches on the Steam UI's helper launch line and restart the UI to apply them (operator 2026-10-13: "apply those chrome flags
# live and restart the webui"). The launch line is the last line of Steam's wrapper steamrtarm64/steamwebhelper.sh.
#   ui-flags.sh set "--disable-smooth-scrolling"   write the flags into the launch line (the line is rewritten, not appended: one set at a time)
#   ui-flags.sh clear                              the launch line without extra flags (still the 0x0f size-neutral form of Valve's taskset line)
#   ui-flags.sh show                               the launch line, and the switches the running browser process was really started with
#   ui-flags.sh restart                            restart the Steam session (UI and client; a running game is lost). `set` and `clear` do NOT restart by themselves.
# THE FILE KEEPS VALVE'S EXACT SIZE (398 bytes): Steam's start-up verification compares file SIZES with its manifest and, on a difference, spends about eight minutes on this device
# re-extracting the package ("Updating Steam... Extracting package...") and puts Valve's original back (measured 2026-10-13). The space the flags take is made by shortening the wrapper's
# `echo` message; `set` refuses flags that do not fit. One flag at a time is the point: switches are not guaranteed to exist or act the same in Valve's CEF build, so check `show` after a restart.
# WRAPPER and ORIG (Valve's original, kept as .wisp-orig) can be overridden for tests.
WRAPPER=${WRAPPER:-/storage/wispos/runtime/steam/steamrtarm64/steamwebhelper.sh}; ORIG=${ORIG:-$WRAPPER.wisp-orig}
case "$1" in
    set|clear)
        [ "$1" = set ] && [ -z "$2" ] && { echo "set needs the flags"; exit 2; }
        FLAGS=""; [ "$1" = set ] && FLAGS="$2"
        FLAGS="$FLAGS" python3 - "$WRAPPER" "$ORIG" <<'EOF'
import os, re, sys
path, orig = sys.argv[1], sys.argv[2]
flags = os.environ.get('FLAGS', '').strip()
size = len(open(orig, 'rb').read())                         # Valve's file: the size Steam expects
text = open(path).read()
exec_re = re.compile(r'^exec (?:taskset 0x[0-9a-f]+ )?\$\(pwd\)/steamwebhelper[^\n]*?"\$@" &> ~/\.steam/steam/logs/steamwebhelper\.log$', re.M)
echo_re = re.compile(r'^echo "(setting LIBGL_KOPPER_DISABLE=true[^"\n]*|[^"\n]*)"$', re.M)
if len(exec_re.findall(text)) != 1 or len(echo_re.findall(text)) != 1:
    sys.exit('the wrapper has an unexpected shape; left alone')
line = 'exec taskset 0x0f $(pwd)/steamwebhelper%s "$@" &> ~/.steam/steam/logs/steamwebhelper.log' % ((' ' + flags) if flags else '')
text = exec_re.sub(lambda m: line, text)
MSG = 'setting LIBGL_KOPPER_DISABLE=true for steamwebhelper to get store working properly'
current = echo_re.search(text).group(1)
room = size - (len(text) - len(current))                   # characters left for the echo message once the rest of the file is what it is
if room < 1:
    sys.exit('the flags do not fit in %d bytes (%d too many); left alone' % (size, 1 - room))
msg = MSG[:room] if room < len(MSG) else MSG + ' ' * (room - len(MSG))
text = echo_re.sub(lambda m: 'echo "%s"' % msg, text)
assert len(text) == size, (len(text), size)
open(path, 'w').write(text)                                 # in place: keeps the inode and the mode
print('launch line now:', line, '| file size', len(text))
EOF
        ;;
    show)
        grep '^exec' "$WRAPPER"; echo "file size $(wc -c < "$WRAPPER") (Valve's: $(wc -c < "$ORIG"))"
        for p in $(pidof steamwebhelper); do
            if tr '\0' ' ' < /proc/$p/cmdline | grep -q -- '-nocrashdialog'; then echo "browser process $p:"; tr '\0' '\n' < /proc/$p/cmdline | grep '^--' | cut -c1-120; fi
        done ;;
    restart)
        systemctl restart wisp-steam; echo "restarted wisp-steam; the UI needs about 3 minutes" ;;
    *) echo "usage: ui-flags.sh set FLAGS | clear | show | restart"; exit 2 ;;
esac
