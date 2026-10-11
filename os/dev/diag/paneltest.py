# Operator's on-panel refresh/frame-rate test, started from the pad (dev only; python3 + ctypes/libdrm via kmsflip.py, no compositor while it runs).
#   paneltest.py watch   background watcher: HOLD SELECT + START FOR 3 SECONDS to run the test. Press B to abort a running test.
#   paneltest.py run [seed]   run the test now (stops Steam and sway, restores both afterwards).
# For each refresh rate (60, 50, 48, 45, 40, 36, 31 Hz) it shows every whole frame rate that divides it evenly and is >= 10 fps, in shuffled order, 6 s each
# (3 s dark with a moving bar, 3 s white). On screen: the refresh rate (HZ, yellow, top) and the frame rate (FPS, green, bottom). A new frame is drawn only every
# (Hz / fps)th refresh and flipped exactly on that refresh, so software frame rate and hardware refresh are aligned by construction; any refresh it misses is counted.
# Results go to stdout (the watcher logs them to /storage/wispos/logs/paneltest.log).
import fcntl, os, random, select, struct, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kmsflip as K
from ctypes import byref, c_uint32 as u32

HZ = [60, 50, 48, 45, 40, 36, 31]
SEG = dict(K.SEG, H='bcefg', Z='abdeg', F='aefg', P='abefg', S='acdfg', E='adefg')
PAD = '/dev/input/event4'; BTN_B, BTN_SELECT, BTN_START = 0x131, 0x13a, 0x13b

def divisors(hz): return [f for f in range(10, hz + 1) if hz % f == 0]

def text7(img, pitch, x, y, s, dh, color):
    dw = dh // 2; t = max(3, dh // 9); gap = dw // 3
    for i, ch in enumerate(s):
        ox = x + i * (dw + gap)
        rects = {'a': (ox, y, dw, t), 'b': (ox + dw - t, y, t, dh // 2 + t // 2), 'c': (ox + dw - t, y + dh // 2 - t // 2, t, dh // 2 + t // 2), 'd': (ox, y + dh - t, dw, t),
                 'e': (ox, y + dh // 2 - t // 2, t, dh // 2 + t // 2), 'f': (ox, y, t, dh // 2 + t // 2), 'g': (ox, y + dh // 2 - t // 2, dw, t)}
        for seg in SEG.get(ch, ''): K.fill(img, pitch, *rects[seg], color)
    return x + len(s) * (dw + gap)

def background(pitch, w, h, bright, hz, fps):
    img = bytearray((K.xrgb(255, 255, 255) if bright else K.xrgb(0, 0, 0)) * (pitch // 4 * h)); red = K.xrgb(255, 0, 0)
    K.fill(img, pitch, 0, 0, w, 3, red); K.fill(img, pitch, 0, h - 3, w, 3, red); K.fill(img, pitch, 0, 0, 3, h, red); K.fill(img, pitch, w - 3, 0, 3, h, red)
    c1 = K.xrgb(0, 0, 0) if bright else K.xrgb(255, 255, 0); c2 = K.xrgb(0, 90, 0) if bright else K.xrgb(0, 255, 0)
    x = text7(img, pitch, 40, 30, str(hz), 150, c1); text7(img, pitch, x + 20, 30 + 80, 'HZ', 70, c1)
    x = text7(img, pitch, 40, 220, str(fps), 150, c2); text7(img, pitch, x + 20, 220 + 80, 'FPS', 70, c2)
    return bytes(img)

def wait_vblank(fd, seq):
    buf = bytearray(struct.pack('IIQQ', 0, seq, 0, 0)); fcntl.ioctl(fd, K.IOWR(0x3A, 24), buf); return struct.unpack('IIqq', bytes(buf))[1]

def pad_pressed(padfd, code):
    hit = False
    try:
        while True:
            d = os.read(padfd, 24 * 16)
            for i in range(0, len(d) - 23, 24):
                _s, _u, typ, c, v = struct.unpack('qqHHi', d[i:i + 24])
                if typ == 1 and c == code and v == 1: hit = True
    except BlockingIOError: pass
    return hit

def combo(fd, conn_id, crtc, native, canv, hz, fps, padfd, seconds=6.0):
    k = hz // fps; w, h = native.hdisplay, native.vdisplay; pitch = canv[0].pitch
    bg = {False: background(pitch, w, h, False, hz, fps), True: background(pitch, w, h, True, hz, fps)}
    step = max(1, round(360 / fps)); x = 10; front = 0; late = 0; frames = 0; first = None; last_t = None
    canv[0].mm[:] = bg[False]
    assert K.drm.drmModePageFlip(fd, crtc, canv[0].fb, 1, None) == 0; ev = os.read(fd, 64); S = struct.unpack('IIQIIII', ev[:32])[5]
    t0 = time.time()
    while time.time() - t0 < seconds:
        if pad_pressed(padfd, BTN_B) and time.time() - t0 > 0.5: return None
        back = canv[1 - front]; back.mm[:] = bg[(time.time() - t0) > seconds / 2]
        bar = K.xrgb(0, 160, 0) if (time.time() - t0) > seconds / 2 else K.xrgb(0, 255, 0); row = bar * 24
        for yy in range(h - 100, h - 20): back.mm[yy * pitch + x * 4: yy * pitch + (x + 24) * 4] = row
        x += step
        if x > w - 40: x = 10
        wait_vblank(fd, S + k - 1)
        assert K.drm.drmModePageFlip(fd, crtc, back.fb, 1, None) == 0
        ev = os.read(fd, 64); _t, _l, _u, sec, usec, seq, _c = struct.unpack('IIQIIII', ev[:32]); t = sec + usec / 1e6
        if seq > S + k: late += 1
        if first is None: first = t
        last_t = t; S = seq; front = 1 - front; frames += 1
    eff = (frames - 1) / (last_t - first) if frames > 1 and last_t > first else 0
    print('  %2d Hz / %2d fps (new frame every %d refreshes): %d frames, effective %.2f fps (ideal %.2f), frames that missed their refresh: %d' % (hz, fps, k, frames, eff, hz / k, late), flush=True)
    return late

def suite(seed=None):
    seed = seed if seed is not None else int(time.time()); rnd = random.Random(seed)
    print('%s test start, shuffle seed %d' % (time.strftime('%H:%M:%S'), seed), flush=True)
    padfd = os.open(PAD, os.O_RDONLY | os.O_NONBLOCK)
    fd = os.open('/dev/dri/card1', os.O_RDWR); res = K.drm.drmModeGetResources(fd).contents; conn = None
    for i in range(res.count_connectors):
        c = K.drm.drmModeGetConnector(fd, res.connectors[i])
        if c.contents.connection == 1 and c.contents.count_modes > 0: conn = c; break
    native = K.Mode.from_buffer_copy(conn.contents.modes[0]); conn_id = conn.contents.connector_id
    crtc = K.drm.drmModeGetEncoder(fd, conn.contents.encoder_id).contents.crtc_id if conn.contents.encoder_id else res.crtcs[0]
    canv = [K.Canvas(fd, native.hdisplay, native.vdisplay), K.Canvas(fd, native.hdisplay, native.vdisplay)]
    total_late = 0; aborted = False
    try:
        for hz in HZ:
            mode = K.Mode.from_buffer_copy(native); vt = int(26000000 / native.htotal / hz + 0.5)
            mode.vtotal = vt; mode.vsync_end = vt - 16; mode.vsync_start = mode.vsync_end - 4; mode.vrefresh = hz; mode.name = ('%dHz' % hz).encode()
            canv[0].mm[:] = background(canv[0].pitch, native.hdisplay, native.vdisplay, False, hz, hz); cid = u32(conn_id)
            assert K.drm.drmModeSetCrtc(fd, crtc, canv[0].fb, 0, 0, byref(cid), 1, byref(mode)) == 0
            time.sleep(1.5)
            fps_list = divisors(hz); rnd.shuffle(fps_list); print('%d Hz: frame rates in this order: %s' % (hz, fps_list), flush=True)
            for fps in fps_list:
                r = combo(fd, conn_id, crtc, native, canv, hz, fps, padfd)
                if r is None: aborted = True; break
                total_late += r
            if aborted: break
    finally:
        cid = u32(conn_id); canv[0].mm[:] = bytes(canv[0].size)
        K.drm.drmModeSetCrtc(fd, crtc, canv[0].fb, 0, 0, byref(cid), 1, byref(native))
        os.close(fd); os.close(padfd)
    print('%s test %s; total frames that missed their refresh: %d' % (time.strftime('%H:%M:%S'), 'ABORTED (B pressed)' if aborted else 'finished', total_late), flush=True)

def run(seed=None):
    sh = lambda *a: subprocess.run(a, check=False)
    sh('systemctl', 'stop', 'wisp-steam'); sh('systemctl', 'stop', 'sway'); time.sleep(4)
    try: suite(seed)
    except Exception as e: print('test failed: %r' % (e,), flush=True)
    finally:
        sh('systemctl', 'start', 'sway'); time.sleep(3); sh('systemctl', 'start', 'wisp-steam')
        print('%s sway and Steam restarted (Steam takes about 3 minutes to show its UI)' % time.strftime('%H:%M:%S'), flush=True)

def watch(fd=None, runner=None, max_seconds=None):
    """Wait for SELECT+START held 3 s on the pad (fd/runner/max_seconds exist so the host test can feed fake events)."""
    fd = os.open(PAD, os.O_RDONLY | os.O_NONBLOCK) if fd is None else fd
    # The test runs in its own process: if it ran in this long-lived watcher, its open handle on the display device (and DRM master) would outlive it and sway could not draw to the panel afterwards.
    runner = runner or (lambda: subprocess.run([sys.executable, os.path.abspath(__file__), 'run'], check=False))
    down = set(); since = None; started = time.time()
    print('%s watching the pad: hold SELECT+START for 3 s to run the panel test' % time.strftime('%H:%M:%S'), flush=True)
    while max_seconds is None or time.time() - started < max_seconds:
        r, _, _ = select.select([fd], [], [], 0.2)
        if r:
            try:
                d = os.read(fd, 24 * 16)
                for i in range(0, len(d) - 23, 24):
                    _s, _u, typ, c, v = struct.unpack('qqHHi', d[i:i + 24])
                    if typ == 1:
                        print('%s key %s code 0x%x value %d' % (time.strftime('%H:%M:%S'), 'SELECT' if c == BTN_SELECT else 'START' if c == BTN_START else '', c, v), flush=True)
                        if c in (BTN_SELECT, BTN_START): (down.add if v else down.discard)(c)
            except BlockingIOError: pass
        if down == {BTN_SELECT, BTN_START}:
            since = since or time.time()
            if time.time() - since >= 3:
                print('%s trigger held, starting' % time.strftime('%H:%M:%S'), flush=True); runner(); down.clear(); since = None; time.sleep(2)
        else: since = None

if __name__ == '__main__':
    if sys.argv[1] == 'watch': watch()
    elif sys.argv[1] == 'run': run(int(sys.argv[2]) if len(sys.argv) > 2 else None)
