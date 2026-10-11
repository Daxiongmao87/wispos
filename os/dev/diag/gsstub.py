# Stand-in for the parts of gamescope that Steam's quick-access menu talks to (dev first; python3 stdlib only, a minimal Wayland server).
# Steam connects to the Wayland socket named by $GAMESCOPE_WAYLAND_DISPLAY and binds `gamescope_control` (protocol/gamescope-control.xml, version 7) and
# `gamescope_action_binding_manager` (gamescope-action-binding.xml). This server advertises both, tells Steam the refresh rates the slider may offer
# (event active_display_info.valid_refresh_rates), logs every request (Steam's own Guide/QAM chords are handled by Steam; no hotkeys here), and can apply set_app_target_refresh_cycle(fps, flags) as a panel mode change.
#   gsstub.py [--name gamescope-wisp-0] [--log FILE] [--apply] [--rates 31-60]
# Observation first: without --apply it only logs. Message wire format: object id u32, (size << 16 | opcode) u32, arguments padded to 4 bytes.
import array, os, selectors, socket, struct, subprocess, sys, time

GLOBALS = [(1, 'gamescope_control', 7), (2, 'gamescope_action_binding_manager', 1)]
FEATURES = [(2, 1, 0), (4, 1, 0)]          # display_info, refresh_cycle_only_change_refresh_rate (feature, version, flags); then `done` (0)
PANEL = dict(h_total=813, pixel_clock_mhz='26.00', h='720 767 771 813', v_active=480)


def choose_refresh(fps, valid):
    """Refresh rate for an app target of `fps`: the lowest valid rate that is a whole multiple of fps (lower refresh costs less power); fps 0 means native (the highest rate)."""
    if not valid: return None
    if fps <= 0: return max(valid)
    for hz in sorted(valid):
        if hz % fps == 0: return hz
    return min(valid, key=lambda hz: (abs(hz - fps), hz))


def modeline_args(hz, h_total=813):
    vt = int(26000000 / h_total / hz + 0.5); vse = vt - 16; vss = vse - 4
    return ['swaymsg', '--', 'output', 'DSI-1', 'modeline', '26.00', '720', '767', '771', '813', '480', str(vss), str(vse), str(vt), '-hsync', '-vsync']


def pad(n): return (n + 3) & ~3
def p_str(s): b = s.encode() + b'\0'; return struct.pack('I', len(b)) + b + b'\0' * (pad(len(b)) - len(b))
def p_arr(vals): b = b''.join(struct.pack('I', v) for v in vals); return struct.pack('I', len(b)) + b + b'\0' * (pad(len(b)) - len(b))
def msg(obj, opcode, payload=b''): return struct.pack('II', obj, ((8 + len(payload)) << 16) | opcode) + payload


class Reader:
    def __init__(self, data): self.d, self.i = data, 0
    def u32(self): v = struct.unpack_from('I', self.d, self.i)[0]; self.i += 4; return v
    def string(self): n = self.u32(); s = self.d[self.i:self.i + max(0, n - 1)].decode('utf-8', 'replace'); self.i += pad(n); return s
    def array(self): n = self.u32(); a = self.d[self.i:self.i + n]; self.i += pad(n); return a


class Client:
    def __init__(self, srv, sock): self.srv, self.sock, self.buf, self.objs, self.fds = srv, sock, b'', {1: ('wl_display', 1)}, []

    def send(self, data): self.sock.sendall(data)

    def feed(self):
        data, anc, _flags, _addr = self.sock.recvmsg(65536, socket.CMSG_LEN(16 * 4))
        for level, typ, cdata in anc:
            if level == socket.SOL_SOCKET and typ == socket.SCM_RIGHTS:
                fds = array.array('i'); fds.frombytes(cdata[:len(cdata) - (len(cdata) % 4)]); self.fds.extend(fds)
        if not data: return False
        self.buf += data
        while len(self.buf) >= 8:
            obj, w = struct.unpack_from('II', self.buf); size, op = w >> 16, w & 0xFFFF
            if size < 8 or len(self.buf) < size: break
            body, self.buf = self.buf[8:size], self.buf[size:]
            self.dispatch(obj, op, body)
        return True

    def dispatch(self, obj, op, body):
        iface, ver = self.objs.get(obj, ('?', 0)); r = Reader(body); log = self.srv.log
        try:
            if iface == 'wl_display':
                if op == 0: cb = r.u32(); self.send(msg(cb, 0, struct.pack('I', 0)) + msg(1, 1, struct.pack('I', cb)))          # sync -> callback.done, delete_id
                elif op == 1:                                                                                                       # get_registry
                    reg = r.u32(); self.objs[reg] = ('wl_registry', 1)
                    for name, i, v in GLOBALS: self.send(msg(reg, 0, struct.pack('I', name) + p_str(i) + struct.pack('I', v)))
                else: log('wl_display request %d' % op)
            elif iface == 'wl_registry' and op == 0:
                name = r.u32(); i = r.string(); v = r.u32(); nid = r.u32(); self.objs[nid] = (i, v)
                log('bind %s v%d as object %d' % (i, v, nid))
                if i == 'gamescope_control': self.announce(nid, v)
            elif iface == 'gamescope_control': self.control(obj, op, r, ver)
            elif iface == 'gamescope_action_binding_manager':
                if op == 1: cb = r.u32(); self.objs[cb] = ('gamescope_action_binding', 1); log('action binding manager: create_action_binding -> object %d' % cb)
                elif op == 0: log('action binding manager destroyed')
                else: log('action binding manager request %d' % op)
            elif iface == 'gamescope_action_binding':
                names = {0: 'destroy', 1: 'set_description', 2: 'add_keyboard_trigger', 3: 'clear_triggers', 4: 'arm', 5: 'disarm'}
                if op == 1: log('action binding %d: description %r' % (obj, r.string()))
                elif op == 2: a = r.array(); log('action binding %d: keyboard trigger keysyms %s' % (obj, [hex(x) for x in struct.unpack('%dI' % (len(a) // 4), a)]))
                elif op == 4: log('action binding %d: arm flags %d' % (obj, r.u32()))
                else: log('action binding %d: %s' % (obj, names.get(op, 'request %d' % op)))
            else: log('unknown object %d (%s) opcode %d, %d bytes: %s' % (obj, iface, op, len(body), body[:32].hex()))
        except Exception as e: log('error decoding object %d opcode %d: %r' % (obj, op, e))

    def announce(self, nid, v):
        for f, fv, fl in FEATURES: self.send(msg(nid, 0, struct.pack('III', f, fv, fl)))
        self.send(msg(nid, 0, struct.pack('III', 0, 0, 0)))                                  # feature_support: done
        if v >= 2:
            self.send(msg(nid, 1, p_str('DSI-1') + p_str('Anbernic') + p_str('RG34XXSP') + struct.pack('I', 1) + p_arr(self.srv.rates)))
            self.srv.log('sent feature list and active_display_info (valid_refresh_rates %d..%d Hz, %d values)' % (self.srv.rates[0], self.srv.rates[-1], len(self.srv.rates)))

    def control(self, obj, op, r, ver):
        log = self.srv.log
        if op == 0: log('gamescope_control destroyed')
        elif op == 1:
            fps = r.u32(); flags = r.u32(); hz = choose_refresh(fps, self.srv.rates)
            log('set_app_target_refresh_cycle fps=%d flags=0x%x (internal=%d allow_switching=%d only_refresh=%d) -> chosen refresh %s Hz' % (fps, flags, flags & 1, bool(flags & 2), bool(flags & 4), hz))
            self.srv.on_refresh_cycle(fps, flags, hz)
        elif op == 2: log('take_screenshot path=%r type=%d flags=%d' % (r.string(), r.u32(), r.u32()))
        elif op == 3: log('display_sleep type=%d flags=%d' % (r.u32(), r.u32()))
        elif op == 6: log('request_app_performance_stats app_id=%d' % r.u32())
        elif op == 7: log('set_keyboard_layout %r %r' % (r.string(), r.string()))
        else: log('gamescope_control request %d' % op)


class Server:
    def __init__(self, path, logfile=None, apply=False, rates=range(31, 61)):
        self.path, self.apply, self.rates, self.logfile = path, apply, list(rates), logfile
        self.events = []
        if os.path.exists(path): os.unlink(path)
        self.lsock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); self.lsock.bind(path); self.lsock.listen(8); self.lsock.setblocking(False)
        self.sel = selectors.DefaultSelector(); self.sel.register(self.lsock, selectors.EVENT_READ, None); self.clients = {}

    def log(self, text):
        line = '%s %s' % (time.strftime('%H:%M:%S'), text); self.events.append(text)
        if self.logfile: print(line, file=self.logfile, flush=True)

    def on_refresh_cycle(self, fps, flags, hz):
        if self.apply and hz: subprocess.run(modeline_args(hz), check=False); self.log('applied panel mode %d Hz' % hz)

    def serve(self, seconds=None):
        end = None if seconds is None else time.time() + seconds
        while end is None or time.time() < end:
            for key, _ in self.sel.select(0.2):
                if key.data is None:
                    c, _ = self.lsock.accept(); self.clients[c] = Client(self, c); self.sel.register(c, selectors.EVENT_READ, self.clients[c]); self.log('client connected')
                else:
                    try: alive = key.data.feed()
                    except OSError: alive = False
                    if not alive: self.sel.unregister(key.fileobj); key.fileobj.close(); self.log('client disconnected')

    def close(self): self.lsock.close(); os.path.exists(self.path) and os.unlink(self.path)


def main(argv):
    opts = {'--name': 'gamescope-wisp-0', '--log': '/storage/wispos/logs/gsstub.log', '--rates': '31-60'}; apply = False; i = 0
    while i < len(argv):
        if argv[i] == '--apply': apply = True; i += 1
        else: opts[argv[i]] = argv[i + 1]; i += 2
    lo, hi = [int(x) for x in opts['--rates'].split('-')]
    path = os.path.join(os.environ.get('XDG_RUNTIME_DIR', '/var/run/0-runtime-dir'), opts['--name'])
    srv = Server(path, open(opts['--log'], 'a'), apply=apply, rates=range(lo, hi + 1))
    srv.log('listening on %s, rates %d..%d, apply=%s' % (path, lo, hi, apply)); srv.serve()


if __name__ == '__main__': main(sys.argv[1:])
