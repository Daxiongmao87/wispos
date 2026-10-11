"""Dev: synthetic keyboard/mouse into the X display via XTEST.  xin.py key Return | click X Y | hclick X Y [HOLD_S=0.4] | move X Y | drag X1 Y1 X2 Y2"""
import ctypes, sys, time
x = ctypes.CDLL('libX11.so.6'); t = ctypes.CDLL('libXtst.so.6')
x.XOpenDisplay.restype = ctypes.c_void_p; x.XOpenDisplay.argtypes = [ctypes.c_char_p]
x.XKeysymToKeycode.restype = ctypes.c_ubyte; x.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
t.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
t.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
t.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
x.XFlush.argtypes = [ctypes.c_void_p]
d = x.XOpenDisplay(None); assert d, 'no display'
K = {'Return': 0xff0d, 'space': 0x20, 'Escape': 0xff1b, 'Tab': 0xff09, 'Left': 0xff51, 'Right': 0xff53, 'Up': 0xff52, 'Down': 0xff54}
def move(a, b):
    t.XTestFakeMotionEvent(d, 0, a, b, 0); x.XFlush(d); time.sleep(0.15)
def btn(down): t.XTestFakeButtonEvent(d, 1, down, 0); x.XFlush(d)
a = sys.argv[1:]
if a[0] == 'key':
    for n in a[1:]:
        kc = x.XKeysymToKeycode(d, K[n]); t.XTestFakeKeyEvent(d, kc, 1, 0); x.XFlush(d); time.sleep(0.1); t.XTestFakeKeyEvent(d, kc, 0, 0); x.XFlush(d); time.sleep(0.3)
elif a[0] == 'move': move(int(a[1]), int(a[2]))
elif a[0] == 'click': move(int(a[1]), int(a[2])); btn(1); time.sleep(0.12); btn(0); time.sleep(0.3)
elif a[0] == 'hclick': move(int(a[1]), int(a[2])); time.sleep(0.3); btn(1); time.sleep(float(a[3]) if len(a) > 3 else 0.4); btn(0); time.sleep(0.3)   # hold the button longer: a frame under load can outlast a 0.12 s click
elif a[0] == 'drag': move(int(a[1]), int(a[2])); btn(1); time.sleep(0.2); move(int(a[3]), int(a[4])); btn(0)
print('ok', a)
