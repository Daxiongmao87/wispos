# Sample X11 top-level window geometry/map state at 10 Hz for N seconds (ctypes/libX11, read-only) and report how often each window changes.
import ctypes, os, sys, time, collections
os.environ.setdefault('DISPLAY', ':0.0')
x = ctypes.CDLL('libX11.so.6')
class Attr(ctypes.Structure):
    _fields_ = [('x', ctypes.c_int), ('y', ctypes.c_int), ('width', ctypes.c_int), ('height', ctypes.c_int), ('border_width', ctypes.c_int), ('depth', ctypes.c_int),
        ('visual', ctypes.c_void_p), ('root', ctypes.c_ulong), ('klass', ctypes.c_int), ('bit_gravity', ctypes.c_int), ('win_gravity', ctypes.c_int), ('backing_store', ctypes.c_int),
        ('backing_planes', ctypes.c_ulong), ('backing_pixel', ctypes.c_ulong), ('save_under', ctypes.c_int), ('colormap', ctypes.c_ulong), ('map_installed', ctypes.c_int),
        ('map_state', ctypes.c_int), ('all_event_masks', ctypes.c_long), ('your_event_mask', ctypes.c_long), ('do_not_propagate_mask', ctypes.c_long),
        ('override_redirect', ctypes.c_int), ('screen', ctypes.c_void_p)]
x.XOpenDisplay.restype = ctypes.c_void_p; x.XOpenDisplay.argtypes = [ctypes.c_char_p]
x.XDefaultRootWindow.restype = ctypes.c_ulong; x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
x.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)), ctypes.POINTER(ctypes.c_uint)]
x.XGetWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Attr)]
d = x.XOpenDisplay(None); root = x.XDefaultRootWindow(d)
secs = float(sys.argv[1]) if len(sys.argv) > 1 else 8
hist = collections.defaultdict(list); end = time.time() + secs
while time.time() < end:
    r = ctypes.c_ulong(); p = ctypes.c_ulong(); kids = ctypes.POINTER(ctypes.c_ulong)(); n = ctypes.c_uint()
    if x.XQueryTree(d, root, ctypes.byref(r), ctypes.byref(p), ctypes.byref(kids), ctypes.byref(n)):
        for i in range(n.value):
            a = Attr()
            if x.XGetWindowAttributes(d, kids[i], ctypes.byref(a)) and a.map_state == 2 and a.width > 100:
                st = (a.width, a.height, a.x, a.y); h = hist[kids[i]]
                if not h or h[-1] != st: h.append(st)
    time.sleep(0.1)
print(f'sampled {secs:.0f} s at 10 Hz; viewable windows >100px:')
for w, h in hist.items(): print(f'   0x{w:x}: {len(h)} distinct state change(s); sequence (first 8): {h[:8]}')
if not hist: print('   (none)')
