"""Dev (never shipped), read-only, runs ON the device: for every X11 window under the root (two levels) that is mapped or carries a STEAM*/GAMESCOPE* property, print its geometry, map state and ALL its
properties with short values. Used to see what Steam marks on the windows it uses for its in-game menus (docs/WISPOS-NOTES.md, 2026-10-14).  usage: DISPLAY=:0 python3 xprops.py [all]"""
import ctypes, os, sys

os.environ.setdefault('DISPLAY', ':0')
x = ctypes.CDLL('libX11.so.6')
x.XOpenDisplay.restype = ctypes.c_void_p; x.XOpenDisplay.argtypes = [ctypes.c_char_p]
x.XDefaultRootWindow.restype = ctypes.c_ulong; x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
x.XListProperties.restype = ctypes.POINTER(ctypes.c_ulong); x.XListProperties.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_int)]
x.XGetAtomName.restype = ctypes.c_void_p; x.XGetAtomName.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
x.XFree.argtypes = [ctypes.c_void_p]
x.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)), ctypes.POINTER(ctypes.c_uint)]
x.XGetWindowProperty.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_long, ctypes.c_long, ctypes.c_int, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte))]


class Attr(ctypes.Structure):
    _fields_ = [('x', ctypes.c_int), ('y', ctypes.c_int), ('width', ctypes.c_int), ('height', ctypes.c_int), ('border_width', ctypes.c_int), ('depth', ctypes.c_int), ('visual', ctypes.c_void_p),
                ('root', ctypes.c_ulong), ('klass', ctypes.c_int), ('bit_gravity', ctypes.c_int), ('win_gravity', ctypes.c_int), ('backing_store', ctypes.c_int), ('backing_planes', ctypes.c_ulong),
                ('backing_pixel', ctypes.c_ulong), ('save_under', ctypes.c_int), ('colormap', ctypes.c_ulong), ('map_installed', ctypes.c_int), ('map_state', ctypes.c_int),
                ('all_event_masks', ctypes.c_long), ('your_event_mask', ctypes.c_long), ('do_not_propagate_mask', ctypes.c_long), ('override_redirect', ctypes.c_int), ('screen', ctypes.c_void_p)]


x.XGetWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(Attr)]
d = x.XOpenDisplay(None)
assert d, 'no X display'
show_all = 'all' in sys.argv[1:]


def atom_name(a):
    p = x.XGetAtomName(d, a)
    s = ctypes.string_at(p).decode(errors='replace') if p else '?'
    if p: x.XFree(p)
    return s


def value(w, prop):
    atype, fmt, n, after = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_ulong(), ctypes.c_ulong(); data = ctypes.POINTER(ctypes.c_ubyte)()
    x.XGetWindowProperty(d, w, prop, 0, 64, 0, 0, ctypes.byref(atype), ctypes.byref(fmt), ctypes.byref(n), ctypes.byref(after), ctypes.byref(data))
    if not data or n.value == 0: return '(empty)'
    tname = atom_name(atype.value)
    if fmt.value == 8: out = ctypes.string_at(data, n.value).decode(errors='replace').replace('\0', '|')[:90]
    elif fmt.value == 32: out = str([ctypes.cast(data, ctypes.POINTER(ctypes.c_long))[i] for i in range(min(n.value, 8))])
    else: out = '(%d-bit x %d)' % (fmt.value, n.value)
    x.XFree(data)
    return '%s %s' % (tname, out)


def props(w):
    n = ctypes.c_int(); p = x.XListProperties(d, w, ctypes.byref(n))
    names = [atom_name(p[i]) for i in range(n.value)] if p else []
    return names, p


def walk(w, depth):
    attr = Attr(); x.XGetWindowAttributes(d, w, ctypes.byref(attr))
    names, p = props(w)
    interesting = any(('STEAM' in n.upper() or 'GAMESCOPE' in n.upper()) for n in names)
    state = {0: 'unmapped', 1: 'unviewable', 2: 'VIEWABLE'}.get(attr.map_state, '?')
    if show_all or state == 'VIEWABLE' or interesting:
        print('%s%#x %s %dx%d%+d%+d depth=%d or=%d' % ('  ' * depth, w, state, attr.width, attr.height, attr.x, attr.y, attr.depth, attr.override_redirect))
        for n in names:
            if show_all or depth < 2 or 'STEAM' in n.upper() or 'GAMESCOPE' in n.upper() or n in ('WM_NAME', '_NET_WM_NAME', 'WM_CLASS', '_NET_WM_STATE', '_NET_WM_WINDOW_TYPE', 'WM_TRANSIENT_FOR'):
                atom = [i for i in range(len(names)) if names[i] == n][0]
                print('%s    %s = %s' % ('  ' * depth, n, value(w, p[atom])))
    if p: x.XFree(p)
    if depth < 2:
        r, par, ch, nch = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.POINTER(ctypes.c_ulong)(), ctypes.c_uint()
        if x.XQueryTree(d, w, ctypes.byref(r), ctypes.byref(par), ctypes.byref(ch), ctypes.byref(nch)) and ch:
            for i in range(nch.value): walk(ch[i], depth + 1)
            x.XFree(ch)


walk(x.XDefaultRootWindow(d), 0)
