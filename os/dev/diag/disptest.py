# Display experiments for the RG34XXSP panel (dev only; ctypes/libX11, stdlib).
#   disptest.py pattern LABEL SECONDS [BIGDIGITS]   fullscreen X11 test window: red border, white diagonals, label, moving bar
#   disptest.py vblank SECONDS          measure the real CRTC refresh rate from DRM vblank counters (no DRM master needed)
# The pattern makes "scaled up correctly and fills the panel" checkable from a grim screenshot: the border must touch all four screen edges.
import ctypes, fcntl, os, struct, sys, time

def vblank(seconds, dev='/dev/dri/card1'):
    fd = os.open(dev, os.O_RDWR)
    req = (3 << 30) | (24 << 16) | (ord('d') << 8) | 0x3A   # DRM_IOCTL_WAIT_VBLANK, union of 24 bytes on 64-bit
    def read():
        buf = bytearray(struct.pack('IIQQ', 0, 0, 0, 0))     # type=RELATIVE, sequence=0 -> reply with the current counter
        fcntl.ioctl(fd, req, buf)
        _t, seq, sec, usec = struct.unpack('IIqq', bytes(buf))
        return seq, sec + usec / 1e6
    s0, t0 = read(); time.sleep(seconds); s1, t1 = read()
    print('vblank %d frames in %.3f s = %.2f Hz' % (s1 - s0, t1 - t0, (s1 - s0) / (t1 - t0)))

SEG = {'0': 'abcdef', '1': 'bc', '2': 'abdeg', '3': 'abcdg', '4': 'bcfg', '5': 'acdfg', '6': 'acdefg', '7': 'abc', '8': 'abcdefg', '9': 'abcdfg'}

def digits(x, d, win, gc, text, w, h, color):
    """Draw `text` (digits only) as big seven-segment numerals centred on the screen."""
    dh = int(h * 0.42); dw = dh // 2; t = max(3, dh // 9); gap = dw // 3
    total = len(text) * dw + (len(text) - 1) * gap; x0 = (w - total) // 2; y0 = (h - dh) // 2 - h // 12
    x.XSetForeground(d, gc, color)
    for i, ch in enumerate(text):
        ox = x0 + i * (dw + gap)
        rects = {'a': (ox, y0, dw, t), 'b': (ox + dw - t, y0, t, dh // 2 + t // 2), 'c': (ox + dw - t, y0 + dh // 2 - t // 2, t, dh // 2 + t // 2),
                 'd': (ox, y0 + dh - t, dw, t), 'e': (ox, y0 + dh // 2 - t // 2, t, dh // 2 + t // 2), 'f': (ox, y0, t, dh // 2 + t // 2), 'g': (ox, y0 + dh // 2 - t // 2, dw, t)}
        for seg in SEG.get(ch, ''):
            rx, ry, rw, rh = rects[seg]; x.XFillRectangle(d, win, gc, rx, ry, rw, rh)

def pattern(label, seconds, big=''):
    x = ctypes.CDLL('libX11.so.6')
    x.XOpenDisplay.restype = ctypes.c_void_p; x.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x.XDefaultRootWindow.restype = ctypes.c_ulong; x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x.XCreateSimpleWindow.restype = ctypes.c_ulong
    x.XCreateSimpleWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong] + [ctypes.c_int] * 2 + [ctypes.c_uint] * 3 + [ctypes.c_ulong] * 2
    x.XCreateGC.restype = ctypes.c_void_p; x.XCreateGC.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
    x.XDefaultScreen.argtypes = [ctypes.c_void_p]
    x.XDisplayWidth.argtypes = x.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x.XSetForeground.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    x.XFillRectangle.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p] + [ctypes.c_int] * 2 + [ctypes.c_uint] * 2
    x.XDrawLine.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p] + [ctypes.c_int] * 4
    x.XDrawString.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    x.XMapRaised.argtypes = [ctypes.c_void_p, ctypes.c_ulong]; x.XFlush.argtypes = [ctypes.c_void_p]
    x.XChangeWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
    d = x.XOpenDisplay(None); s = x.XDefaultScreen(d)
    w, h = x.XDisplayWidth(d, s), x.XDisplayHeight(d, s)
    win = x.XCreateSimpleWindow(d, x.XDefaultRootWindow(d), 0, 0, w, h, 0, 0, 0)
    class SWA(ctypes.Structure):
        _fields_ = [('background_pixmap', ctypes.c_ulong), ('background_pixel', ctypes.c_ulong), ('border_pixmap', ctypes.c_ulong), ('border_pixel', ctypes.c_ulong),
                    ('bit_gravity', ctypes.c_int), ('win_gravity', ctypes.c_int), ('backing_store', ctypes.c_int), ('backing_planes', ctypes.c_ulong), ('backing_pixel', ctypes.c_ulong),
                    ('save_under', ctypes.c_int), ('event_mask', ctypes.c_long), ('do_not_propagate_mask', ctypes.c_long), ('override_redirect', ctypes.c_int),
                    ('colormap', ctypes.c_ulong), ('cursor', ctypes.c_ulong)]
    a = SWA(); a.override_redirect = 1
    x.XChangeWindowAttributes(d, win, 1 << 9, ctypes.byref(a))
    gc = x.XCreateGC(d, win, 0, None)
    x.XMapRaised(d, win)
    t_start = time.time(); t_end = t_start + seconds; n = 0
    while time.time() < t_end:
        bright = big and (time.time() - t_start) > seconds / 2   # second half: white screen, where low-refresh flicker is easiest to see
        x.XSetForeground(d, gc, 0xFFFFFF if bright else 0x000000); x.XFillRectangle(d, win, gc, 0, 0, w, h)
        x.XSetForeground(d, gc, 0xFF0000)
        for b in range(3):   # 3 px border exactly on the screen edge
            x.XDrawLine(d, win, gc, 0, b, w - 1, b); x.XDrawLine(d, win, gc, 0, h - 1 - b, w - 1, h - 1 - b)
            x.XDrawLine(d, win, gc, b, 0, b, h - 1); x.XDrawLine(d, win, gc, w - 1 - b, 0, w - 1 - b, h - 1)
        x.XSetForeground(d, gc, 0x000000 if bright else 0xFFFFFF)
        x.XDrawLine(d, win, gc, 0, 0, w - 1, h - 1); x.XDrawLine(d, win, gc, 0, h - 1, w - 1, 0)
        if big: digits(x, d, win, gc, big, w, h, 0x000000 if bright else 0xFFFF00)
        txt = ('%s  X screen %dx%d' % (label, w, h)).encode()
        x.XSetForeground(d, gc, 0x000000 if bright else 0xFFFFFF)
        x.XDrawString(d, win, gc, 12, h - 6, txt, len(txt))
        x.XSetForeground(d, gc, 0x00AA00 if bright else 0x00FF00); bx = (n * 8) % max(1, w - 20)   # moving bar: smoothness shows the real update rate
        x.XFillRectangle(d, win, gc, bx, h - 40, 20, 12)
        x.XFlush(d); n += 1; time.sleep(1 / 60)
    print('pattern %s: %dx%d, %d frames drawn' % (label, w, h, n))

if __name__ == '__main__':
    if sys.argv[1] == 'pattern': pattern(sys.argv[2], float(sys.argv[3]), sys.argv[4] if len(sys.argv) > 4 else '')
    elif sys.argv[1] == 'vblank': vblank(float(sys.argv[2]))
