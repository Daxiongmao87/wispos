"""Host tests for the gamescope stand-in: a fake Steam speaks the Wayland wire protocol to it."""
import os, socket, struct, sys, tempfile, threading, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dev', 'diag'))
import gsstub as G


def read_msgs(sock, want):
    """Read messages until `want(obj, op, body)` is true; return all read."""
    out, buf = [], b''
    while True:
        while len(buf) >= 8:
            obj, w = struct.unpack_from('II', buf); size, op = w >> 16, w & 0xFFFF
            if len(buf) < size: break
            body, buf = buf[8:size], buf[size:]; out.append((obj, op, body))
            if want(obj, op, body): return out
        buf += sock.recv(4096)


class StubTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(); self.path = os.path.join(self.dir, 'gs-test')
        self.srv = G.Server(self.path); self.t = threading.Thread(target=self.srv.serve, kwargs=dict(seconds=6)); self.t.start()
        self.c = socket.socket(socket.AF_UNIX); self.c.connect(self.path); self.c.settimeout(4)

    def tearDown(self):
        self.c.close(); self.t.join(); self.srv.close()

    def bind(self, iface, ver, nid):
        self.c.sendall(G.msg(1, 1, struct.pack('I', 2)))                                   # get_registry -> object 2
        globs = read_msgs(self.c, lambda o, op, b: o == 2 and b'action_binding_manager' in b)
        names = [G.Reader(b[4:]).string() for o, op, b in globs if o == 2]
        self.assertEqual(names, ['gamescope_control', 'gamescope_action_binding_manager'])
        self.c.sendall(G.msg(2, 0, struct.pack('I', 1 if iface == 'gamescope_control' else 2) + G.p_str(iface) + struct.pack('II', ver, nid)))

    def test_control_announces_features_and_valid_refresh_rates(self):
        self.bind('gamescope_control', 7, 3)
        evs = read_msgs(self.c, lambda o, op, b: o == 3 and op == 1)
        feats = [struct.unpack('III', b) for o, op, b in evs if o == 3 and op == 0]
        self.assertEqual(feats[-1], (0, 0, 0))                                             # `done` ends the feature list
        r = G.Reader(evs[-1][2]); self.assertEqual((r.string(), r.string(), r.string(), r.u32()), ('DSI-1', 'Anbernic', 'RG34XXSP', 1))
        a = r.array(); rates = list(struct.unpack('%dI' % (len(a) // 4), a)); self.assertEqual(rates, list(range(31, 61)))

    def test_target_refresh_cycle_request_is_decoded_and_mapped_to_a_refresh_rate(self):
        self.bind('gamescope_control', 7, 3)
        read_msgs(self.c, lambda o, op, b: o == 3 and op == 1)
        self.c.sendall(G.msg(3, 1, struct.pack('II', 40, 3))); self.c.sendall(G.msg(1, 0, struct.pack('I', 9)))   # then a sync to know the server has processed it
        read_msgs(self.c, lambda o, op, b: o == 9 and op == 0)
        self.assertTrue(any('fps=40 flags=0x3' in e and 'chosen refresh 40 Hz' in e for e in self.srv.events), self.srv.events)

    def test_action_binding_requests_are_logged(self):
        self.bind('gamescope_action_binding_manager', 1, 4)
        self.c.sendall(G.msg(4, 1, struct.pack('I', 5)) + G.msg(5, 1, G.p_str('Quick access menu')) + G.msg(5, 2, G.p_arr([0xFF14, 0x20])) + G.msg(5, 4, struct.pack('I', 1)) + G.msg(1, 0, struct.pack('I', 9)))
        read_msgs(self.c, lambda o, op, b: o == 9 and op == 0)
        text = '\n'.join(self.srv.events)
        for needle in ("description 'Quick access menu'", "keysyms ['0xff14', '0x20']", 'arm flags 1'): self.assertIn(needle, text)


class ChooseRefreshTest(unittest.TestCase):
    def test_lowest_valid_whole_multiple_of_the_target_frame_rate(self):
        valid = list(range(31, 61))
        for fps, hz in [(60, 60), (40, 40), (30, 60), (25, 50), (20, 40), (15, 45), (12, 36), (10, 40), (0, 60), (31, 31), (45, 45), (48, 48)]:
            self.assertEqual(G.choose_refresh(fps, valid), hz, (fps, hz))
        self.assertIsNone(G.choose_refresh(30, []))

    def test_modeline_uses_the_panels_own_pixel_clock_and_horizontal_timing(self):
        a = G.modeline_args(40); self.assertEqual(a[:6], ['swaymsg', '--', 'output', 'DSI-1', 'modeline', '26.00']); self.assertEqual(a[6:10], ['720', '767', '771', '813'])
        self.assertEqual(a[10:14], ['480', '780', '784', '800']); self.assertEqual(a[-2:], ['-hsync', '-vsync'])


if __name__ == '__main__': unittest.main()
