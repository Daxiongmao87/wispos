# Vsync-locked display test for the RG34XXSP panel (dev only; python3 + ctypes/libdrm, no compositor).
# Needs DRM master: stop sway first (`systemctl stop sway`), start it again afterwards (kmsflip.sh does both).
#   kmsflip.py HZ [HZ ...]   for each target rate: program a custom mode (the panel's own 26 MHz pixel clock and horizontal timing, longer vertical blanking),
#                            then draw ONE frame per refresh and flip on the display's own "flip complete" event, for 4 s on black (moving bar) and 4 s on white (flicker).
# It reports, from the kernel's per-flip timestamps: measured refresh rate, number of refreshes that were skipped (flip event sequence jumped), and min/max interval.
# Zero skips and a steady interval mean software and hardware are aligned; a smooth bar on the panel then shows whether the panel itself honours the rate.
import ctypes, fcntl, mmap, os, struct, sys, time
from ctypes import c_uint16 as u16, c_uint32 as u32, c_int, c_void_p, POINTER, byref

drm = ctypes.CDLL('libdrm.so.2', use_errno=True)

class Mode(ctypes.Structure):
    _fields_ = [('clock', u32), ('hdisplay', u16), ('hsync_start', u16), ('hsync_end', u16), ('htotal', u16), ('hskew', u16),
                ('vdisplay', u16), ('vsync_start', u16), ('vsync_end', u16), ('vtotal', u16), ('vscan', u16),
                ('vrefresh', u32), ('flags', u32), ('type', u32), ('name', ctypes.c_char * 32)]

class Conn(ctypes.Structure):
    _fields_ = [('connector_id', u32), ('encoder_id', u32), ('connector_type', u32), ('connector_type_id', u32), ('connection', c_int),
                ('mmWidth', u32), ('mmHeight', u32), ('subpixel', c_int), ('count_modes', c_int), ('modes', POINTER(Mode)), ('count_props', c_int),
                ('props', c_void_p), ('prop_values', c_void_p), ('count_encoders', c_int), ('encoders', c_void_p)]

class Enc(ctypes.Structure):
    _fields_ = [('encoder_id', u32), ('encoder_type', u32), ('crtc_id', u32), ('possible_crtcs', u32), ('possible_clones', u32)]

class Res(ctypes.Structure):
    _fields_ = [('count_fbs', c_int), ('fbs', POINTER(u32)), ('count_crtcs', c_int), ('crtcs', POINTER(u32)), ('count_connectors', c_int),
                ('connectors', POINTER(u32)), ('count_encoders', c_int), ('encoders', POINTER(u32))]

drm.drmModeGetResources.restype = POINTER(Res); drm.drmModeGetResources.argtypes = [c_int]
drm.drmModeGetConnector.restype = POINTER(Conn); drm.drmModeGetConnector.argtypes = [c_int, u32]
drm.drmModeGetEncoder.restype = POINTER(Enc); drm.drmModeGetEncoder.argtypes = [c_int, u32]
drm.drmModeAddFB.argtypes = [c_int, u32, u32, ctypes.c_uint8, ctypes.c_uint8, u32, u32, POINTER(u32)]
drm.drmModeSetCrtc.argtypes = [c_int, u32, u32, u32, u32, POINTER(u32), c_int, POINTER(Mode)]
drm.drmModePageFlip.argtypes = [c_int, u32, u32, u32, c_void_p]

IOWR = lambda nr, size: (3 << 30) | (size << 16) | (ord('d') << 8) | nr
SEG = {'0': 'abcdef', '1': 'bc', '2': 'abdeg', '3': 'abcdg', '4': 'bcfg', '5': 'acdfg', '6': 'acdefg', '7': 'abc', '8': 'abcdefg', '9': 'abcdfg'}

def xrgb(r, g, b): return struct.pack('<I', (r << 16) | (g << 8) | b)

class Canvas:
    def __init__(self, fd, w, h):
        self.w, self.h = w, h
        buf = bytearray(struct.pack('IIIIIIQ', h, w, 32, 0, 0, 0, 0))
        fcntl.ioctl(fd, IOWR(0xB2, 32), buf)
        _h, _w, _bpp, _fl, self.handle, self.pitch, self.size = struct.unpack('IIIIIIQ', bytes(buf))
        fb = u32(); assert drm.drmModeAddFB(fd, w, h, 24, 32, self.pitch, self.handle, byref(fb)) == 0, 'AddFB failed'
        self.fb = fb.value
        m = bytearray(struct.pack('IIQ', self.handle, 0, 0)); fcntl.ioctl(fd, IOWR(0xB3, 16), m)
        self.mm = mmap.mmap(fd, self.size, offset=struct.unpack('IIQ', bytes(m))[2])

def fill(img, pitch, x, y, w, h, color):
    row = color * w
    for yy in range(max(0, y), y + h): img[yy * pitch + x * 4: yy * pitch + (x + w) * 4] = row

def background(pitch, w, h, bright, label):
    img = bytearray((xrgb(255, 255, 255) if bright else xrgb(0, 0, 0)) * (pitch // 4 * h))
    fill(img, pitch, 0, 0, w, 3, xrgb(255, 0, 0)); fill(img, pitch, 0, h - 3, w, 3, xrgb(255, 0, 0))
    fill(img, pitch, 0, 0, 3, h, xrgb(255, 0, 0)); fill(img, pitch, w - 3, 0, 3, h, xrgb(255, 0, 0))
    dh = int(h * 0.42); dw = dh // 2; t = max(3, dh // 9); gap = dw // 3; x0 = (w - (len(label) * dw + (len(label) - 1) * gap)) // 2; y0 = (h - dh) // 2 - 40
    col = xrgb(0, 0, 0) if bright else xrgb(255, 255, 0)
    for i, ch in enumerate(label):
        ox = x0 + i * (dw + gap)
        rects = {'a': (ox, y0, dw, t), 'b': (ox + dw - t, y0, t, dh // 2 + t // 2), 'c': (ox + dw - t, y0 + dh // 2 - t // 2, t, dh // 2 + t // 2), 'd': (ox, y0 + dh - t, dw, t),
                 'e': (ox, y0 + dh // 2 - t // 2, t, dh // 2 + t // 2), 'f': (ox, y0, t, dh // 2 + t // 2), 'g': (ox, y0 + dh // 2 - t // 2, dw, t)}
        for s in SEG[ch]: fill(img, pitch, *rects[s], col)
    return bytes(img)

def run(fd, conn_id, crtc, native, canvases, hz, phase_seconds=4.0):
    mode = Mode.from_buffer_copy(native)
    vt = int(26000000 / native.htotal / hz + 0.5); mode.vtotal = vt; mode.vsync_end = vt - 16; mode.vsync_start = mode.vsync_end - 4; mode.vrefresh = int(hz)
    mode.name = ('%dHz' % hz).encode()
    w, h = native.hdisplay, native.vdisplay; pitch = canvases[0].pitch
    bg = {False: background(pitch, w, h, False, str(hz)), True: background(pitch, w, h, True, str(hz))}
    cid = u32(conn_id)
    canvases[0].mm[:] = bg[False]
    assert drm.drmModeSetCrtc(fd, crtc, canvases[0].fb, 0, 0, byref(cid), 1, byref(mode)) == 0, 'SetCrtc failed (errno %d)' % ctypes.get_errno()
    time.sleep(1.0)
    step = max(1, round(360 / hz)); x = 10; front = 0; last_seq = None; last_t = None; ints = []; skips = 0; frames = 0
    t0 = time.time(); total = phase_seconds * 2
    while time.time() - t0 < total:
        bright = (time.time() - t0) > phase_seconds
        back = canvases[1 - front]
        back.mm[:] = bg[bright]
        bar = xrgb(0, 160, 0) if bright else xrgb(0, 255, 0); row = bar * 24
        for yy in range(h - 120, h - 20): back.mm[yy * pitch + x * 4: yy * pitch + (x + 24) * 4] = row
        x += step
        if x > w - 40: x = 10
        assert drm.drmModePageFlip(fd, crtc, back.fb, 1, None) == 0, 'PageFlip failed (errno %d)' % ctypes.get_errno()
        ev = os.read(fd, 64)
        _typ, _len, _ud, sec, usec, seq, _crtc = struct.unpack('IIQIIII', ev[:32])
        t = sec + usec / 1e6
        if last_seq is not None:
            skips += max(0, seq - last_seq - 1); ints.append((t - last_t) * 1000)
        last_seq, last_t = seq, t; front = 1 - front; frames += 1
    span = (last_t - (last_t - sum(ints) / 1000)) if ints else 0
    meas = (len(ints) / (sum(ints) / 1000)) if ints else 0
    print('target %2d Hz: measured %.2f Hz from flip timestamps, %d flips, skipped refreshes %d, interval min %.1f ms / max %.1f ms (ideal %.1f ms)'
          % (hz, meas, frames, skips, min(ints), max(ints), 1000.0 / hz), flush=True)

def main():
    rates = [int(a) for a in sys.argv[1:]] or [60, 50, 48, 45, 40, 36, 31]
    fd = os.open('/dev/dri/card1', os.O_RDWR)
    res = drm.drmModeGetResources(fd).contents
    conn = None
    for i in range(res.count_connectors):
        c = drm.drmModeGetConnector(fd, res.connectors[i])
        if c.contents.connection == 1 and c.contents.count_modes > 0: conn = c; break
    assert conn, 'no connected connector'
    native = Mode.from_buffer_copy(conn.contents.modes[0]); conn_id = conn.contents.connector_id
    crtc = 0
    if conn.contents.encoder_id: crtc = drm.drmModeGetEncoder(fd, conn.contents.encoder_id).contents.crtc_id
    if not crtc: crtc = res.crtcs[0]
    print('connector %d, crtc %d, native %dx%d clock %d kHz htotal %d vtotal %d' % (conn_id, crtc, native.hdisplay, native.vdisplay, native.clock, native.htotal, native.vtotal), flush=True)
    canvases = [Canvas(fd, native.hdisplay, native.vdisplay), Canvas(fd, native.hdisplay, native.vdisplay)]
    try:
        for hz in rates: run(fd, conn_id, crtc, native, canvases, hz)
    finally:
        cid = u32(conn_id); canvases[0].mm[:] = bytes(canvases[0].size)
        drm.drmModeSetCrtc(fd, crtc, canvases[0].fb, 0, 0, byref(cid), 1, byref(native))
        print('native mode restored', flush=True)

if __name__ == '__main__': main()
