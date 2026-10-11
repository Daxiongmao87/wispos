# Steam's display-resolution choice, answered the way gamescope answers it (dev first; python3 + ctypes/libX11 + swaymsg).
# Contract (gamescope src/steamcompmgr.cpp update_mode_atoms, src/Backends/DRMBackend.cpp get_saved_mode; read 2026-10-09):
#   - for an EXTERNAL display gamescope sets on the X root window: GAMESCOPE_DISPLAY_IS_EXTERNAL = 1 (CARDINAL/32) and
#     GAMESCOPE_DISPLAY_MODE_LIST_EXTERNAL = "WxH@R WxH@R ..." (STRING/8). Steam builds its Resolution choice from that list.
#   - the chosen mode is saved by Steam in the file named by the environment variable GAMESCOPE_MODE_SAVE_FILE, one line per display:
#       "<display description>:<W>x<H>@<R> <broadcast_mode>"        (format read by get_saved_mode: "%255[^:]:%dx%d@%d %u")
#     and Steam then sets the root property GAMESCOPE_DISPLAY_MODE_NUDGE, which gamescope deletes after re-reading the file.
# Here the "modes" are the panel's native 720x480 and the half resolution 360x240; selecting one sets the sway output scale (1 or 2). NOT verified against a real
# Steam yet: whether Steam shows a Resolution option for this display, and which description it writes, are observed on the device.
#   gsmodes.py [--save-file PATH] [--panel 720x480] [--default 360x240]
import ctypes, os, re, select, subprocess, sys, time

PANEL = (720, 480)
ATOMS = ('GAMESCOPE_DISPLAY_IS_EXTERNAL', 'GAMESCOPE_DISPLAY_MODE_LIST_EXTERNAL', 'GAMESCOPE_DISPLAY_MODE_NUDGE')


def mode_list(panel=PANEL, refresh=60):
    """The list Steam shows: the native panel mode first, then the half-resolution mode (width and height divided by 2)."""
    w, h = panel
    return ' '.join('%dx%d@%d' % (mw, mh, refresh) for mw, mh in ((w, h), (w // 2, h // 2)))


def parse_saved_mode(text):
    """The most recent saved mode in Steam's save file (last matching line wins), as (width, height, refresh), or None. Same line format as gamescope's get_saved_mode."""
    last = None
    for line in text.splitlines():
        m = re.match(r'^[^:]+:(\d+)x(\d+)@(\d+)(?:\s+\d+)?\s*$', line.strip())
        if m: last = tuple(int(g) for g in m.groups())
    return last


def scale_for_mode(width, height, panel=PANEL):
    """sway output scale that makes X clients see a width x height screen on the panel: an exact divisor only (1 or 2 here); anything else is refused (None)."""
    if width <= 0 or height <= 0 or panel[0] % width or panel[1] % height: return None
    s = panel[0] // width
    return s if s == panel[1] // height and s in (1, 2) else None


def apply_scale(scale):
    env = dict(os.environ, XDG_RUNTIME_DIR='/var/run/0-runtime-dir', SWAYSOCK='/var/run/0-runtime-dir/sway-ipc.0.sock')
    r = subprocess.run(['swaymsg', 'output', 'DSI-1', 'scale', str(scale)], env=env, capture_output=True, text=True)
    return '"success": true' in r.stdout or '"success":true' in r.stdout


class XRoot:
    """The little of libX11 this needs: set root properties, wait for property changes."""
    class PropEvent(ctypes.Structure):
        _fields_ = [('type', ctypes.c_int), ('serial', ctypes.c_ulong), ('send_event', ctypes.c_int), ('display', ctypes.c_void_p), ('window', ctypes.c_ulong),
                    ('atom', ctypes.c_ulong), ('time', ctypes.c_ulong), ('state', ctypes.c_int)]

    def __init__(self):
        x = self.x = ctypes.CDLL('libX11.so.6')
        x.XOpenDisplay.restype = ctypes.c_void_p; x.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x.XDefaultRootWindow.restype = ctypes.c_ulong; x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        x.XInternAtom.restype = ctypes.c_ulong; x.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        x.XChangeProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        x.XDeleteProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
        x.XSelectInput.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_long]
        x.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]; x.XPending.argtypes = [ctypes.c_void_p]; x.XFlush.argtypes = [ctypes.c_void_p]
        x.XConnectionNumber.argtypes = [ctypes.c_void_p]
        self.dpy = x.XOpenDisplay(None)
        if not self.dpy: raise SystemExit('cannot open the X display (DISPLAY=%r)' % os.environ.get('DISPLAY'))
        self.root = x.XDefaultRootWindow(self.dpy); self.atom = {n: x.XInternAtom(self.dpy, n.encode(), 0) for n in ATOMS}
        x.XSelectInput(self.dpy, self.root, 1 << 22)                          # PropertyChangeMask

    def set_string(self, name, text):
        b = text.encode() + b'\0'; self.x.XChangeProperty(self.dpy, self.root, self.atom[name], 31, 8, 0, b, len(b)); self.x.XFlush(self.dpy)       # XA_STRING = 31

    def set_cardinal(self, name, value):
        v = (ctypes.c_ulong * 1)(value); self.x.XChangeProperty(self.dpy, self.root, self.atom[name], 6, 32, 0, v, 1); self.x.XFlush(self.dpy)        # XA_CARDINAL = 6

    def delete(self, name): self.x.XDeleteProperty(self.dpy, self.root, self.atom[name]); self.x.XFlush(self.dpy)

    def changed(self, timeout):
        """Names of our atoms whose value was set during the wait (timeout seconds)."""
        out = []
        if not self.x.XPending(self.dpy): select.select([self.x.XConnectionNumber(self.dpy)], [], [], timeout)
        buf = ctypes.create_string_buffer(192)
        while self.x.XPending(self.dpy):
            self.x.XNextEvent(self.dpy, buf); ev = self.PropEvent.from_buffer_copy(buf.raw[:ctypes.sizeof(self.PropEvent)])
            if ev.type == 28 and ev.state == 0:                                   # PropertyNotify, NewValue
                out += [n for n, a in self.atom.items() if a == ev.atom]
        return out


def main(argv):
    opts = dict(zip(argv[::2], argv[1::2])); save = opts.get('--save-file', os.environ.get('GAMESCOPE_MODE_SAVE_FILE', '/storage/wispos/config/gamescope-modes'))
    panel = tuple(int(v) for v in opts.get('--panel', '720x480').split('x')); default = tuple(int(v) for v in opts.get('--default', '360x240').split('x'))
    log = lambda m: print('%s %s' % (time.strftime('%H:%M:%S'), m), flush=True)
    xr = XRoot(); lst = mode_list(panel)
    xr.set_cardinal('GAMESCOPE_DISPLAY_IS_EXTERNAL', 1); xr.set_string('GAMESCOPE_DISPLAY_MODE_LIST_EXTERNAL', lst); log('published modes: %s (save file %s)' % (lst, save))
    chosen = default
    if os.path.exists(save):
        chosen = parse_saved_mode(open(save).read())[:2] if parse_saved_mode(open(save).read()) else default
    s = scale_for_mode(*chosen, panel=panel); log('applying %dx%d: scale %s -> %s' % (chosen[0], chosen[1], s, apply_scale(s) if s else 'refused'))
    while True:
        for name in xr.changed(1.0):
            if name == 'GAMESCOPE_DISPLAY_MODE_NUDGE':
                xr.delete('GAMESCOPE_DISPLAY_MODE_NUDGE')
                m = parse_saved_mode(open(save).read()) if os.path.exists(save) else None
                s = scale_for_mode(m[0], m[1], panel) if m else None
                log('nudge: saved mode %s -> scale %s -> %s' % (m, s, apply_scale(s) if s else 'ignored'))


if __name__ == '__main__': main(sys.argv[1:])
