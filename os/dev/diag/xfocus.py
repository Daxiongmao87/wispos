"""Dev (never shipped), read-only, runs ON the device: who holds X input focus on the Xwayland display, and where is the pointer?
One line: focus=<window id> "<name>" class=<res_class> | pointer=X,Y over <window id> | root children mapped: <n>
Used to look at the "Steam page is drawn but ignores the pad" state after the Steam button (docs/WISPOS-NOTES.md, 2026-10-13)."""
import ctypes, os

os.environ.setdefault('DISPLAY', ':0')
x = ctypes.CDLL('libX11.so.6')
x.XOpenDisplay.restype = ctypes.c_void_p; x.XOpenDisplay.argtypes = [ctypes.c_char_p]
x.XDefaultRootWindow.restype = ctypes.c_ulong; x.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
x.XGetInputFocus.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int)]
x.XFetchName.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_char_p)]
x.XQueryPointer.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong)] + [ctypes.POINTER(ctypes.c_int)] * 4 + [ctypes.POINTER(ctypes.c_uint)]


class ClassHint(ctypes.Structure):
    _fields_ = [('res_name', ctypes.c_char_p), ('res_class', ctypes.c_char_p)]


x.XGetClassHint.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ClassHint)]
x.XQueryTree.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)), ctypes.POINTER(ctypes.c_uint)]

d = x.XOpenDisplay(None)
assert d, 'no X display'
root = x.XDefaultRootWindow(d)
fw, rev = ctypes.c_ulong(), ctypes.c_int()
x.XGetInputFocus(d, ctypes.byref(fw), ctypes.byref(rev))


def label(win):
    name = ctypes.c_char_p()
    x.XFetchName(d, win, ctypes.byref(name))
    hint = ClassHint()
    ok = x.XGetClassHint(d, win, ctypes.byref(hint))
    return '"%s" class=%s' % ((name.value or b'').decode(errors='replace'), (hint.res_class or b'').decode(errors='replace') if ok else '?')


r, c = ctypes.c_ulong(), ctypes.c_ulong()
rx, ry, wx, wy, mask = ctypes.c_int(), ctypes.c_int(), ctypes.c_int(), ctypes.c_int(), ctypes.c_uint()
x.XQueryPointer(d, root, ctypes.byref(r), ctypes.byref(c), ctypes.byref(rx), ctypes.byref(ry), ctypes.byref(wx), ctypes.byref(wy), ctypes.byref(mask))
focus = fw.value
print('focus=%#x %s | pointer=%d,%d over %#x' % (focus, label(focus) if focus > 1 else '(none/root)', rx.value, ry.value, c.value))
