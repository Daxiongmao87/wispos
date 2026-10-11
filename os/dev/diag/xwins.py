# List X11 windows on the Xwayland display with their map state, geometry and name (read-only).
import ctypes, ctypes.util, os
os.environ.setdefault('DISPLAY', ':0.0')
x = ctypes.CDLL('libX11.so.6')
class Attr(ctypes.Structure):
    _fields_ = [('x', ctypes.c_int), ('y', ctypes.c_int), ('width', ctypes.c_int), ('height', ctypes.c_int),
        ('border_width', ctypes.c_int), ('depth', ctypes.c_int), ('visual', ctypes.c_void_p), ('root', ctypes.c_ulong),
        ('klass', ctypes.c_int), ('bit_gravity', ctypes.c_int), ('win_gravity', ctypes.c_int), ('backing_store', ctypes.c_int),
        ('backing_planes', ctypes.c_ulong), ('backing_pixel', ctypes.c_ulong), ('save_under', ctypes.c_int),
        ('colormap', ctypes.c_ulong), ('map_installed', ctypes.c_int), ('map_state', ctypes.c_int),
        ('all_event_masks', ctypes.c_long), ('your_event_mask', ctypes.c_long), ('do_not_propagate_mask', ctypes.c_long),
        ('override_redirect', ctypes.c_int), ('screen', ctypes.c_void_p)]
x.XOpenDisplay.restype = ctypes.c_void_p; x.XOpenDisplay.argtypes = [ctypes.c_char_p]
x.XDefaultRootWindow.restype = ctypes.c_ulong; x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
x.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
                         ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)), ctypes.POINTER(ctypes.c_uint)]
x.XGetWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Attr)]
x.XFetchName.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_char_p)]
d = x.XOpenDisplay(None)
if not d: raise SystemExit('cannot open display')
root = x.XDefaultRootWindow(d)
STATE = {0: 'unmapped', 1: 'unviewable', 2: 'VIEWABLE'}
def walk(w, depth=0):
    r = ctypes.c_ulong(); p = ctypes.c_ulong(); kids = ctypes.POINTER(ctypes.c_ulong)(); n = ctypes.c_uint()
    if not x.XQueryTree(d, w, ctypes.byref(r), ctypes.byref(p), ctypes.byref(kids), ctypes.byref(n)): return
    for i in range(n.value):
        c = kids[i]; a = Attr()
        if not x.XGetWindowAttributes(d, c, ctypes.byref(a)): continue
        nm = ctypes.c_char_p()
        x.XFetchName(d, c, ctypes.byref(nm))
        print('  ' * depth + f'0x{c:x} {STATE.get(a.map_state, a.map_state)} {a.width}x{a.height}+{a.x}+{a.y} or={a.override_redirect} name={nm.value.decode(errors="replace") if nm.value else None!r}')
        if depth < 2: walk(c, depth + 1)
walk(root)
