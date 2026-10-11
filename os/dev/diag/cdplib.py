# Minimal Chrome DevTools Protocol client (stdlib only): list targets, sample JS call stacks with Debugger.pause, take a CPU profile.
# usage: cdp.py [port]    Read-only observation of Steam's UI browser; resumes execution after every pause.
import socket, base64, os, json, struct, sys, time, http.client, collections
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080

def targets():
    c = http.client.HTTPConnection('127.0.0.1', PORT, timeout=10); c.request('GET', '/json'); return json.loads(c.getresponse().read())

class WS:
    def __init__(self, path, timeout=12):
        self.s = socket.create_connection(('127.0.0.1', PORT), timeout=timeout); self.buf = b''
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f'GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{PORT}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                        f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n').encode())
        head = b''
        while b'\r\n\r\n' not in head:
            d = self.s.recv(4096)
            if not d: raise OSError('closed during handshake')
            head += d
        head, self.buf = head.split(b'\r\n\r\n', 1)
        if b' 101 ' not in head.split(b'\r\n')[0]: raise OSError('handshake refused: ' + head.split(b'\r\n')[0].decode())
        self.id = 0; self.events = []
    def _need(self, n):
        while len(self.buf) < n:
            d = self.s.recv(65536)
            if not d: raise OSError('closed')
            self.buf += d
        out, self.buf = self.buf[:n], self.buf[n:]; return out
    def send(self, obj):
        p = json.dumps(obj).encode(); mask = os.urandom(4); n = len(p)
        h = b'\x81' + (bytes([0x80 | n]) if n < 126 else b'\xfe' + struct.pack('>H', n) if n < 65536 else b'\xff' + struct.pack('>Q', n))
        self.s.sendall(h + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(p)))
    def recv(self):
        data = b''
        while True:
            b1, b2 = self._need(2); op = b1 & 0x0f; n = b2 & 0x7f
            if n == 126: n = struct.unpack('>H', self._need(2))[0]
            elif n == 127: n = struct.unpack('>Q', self._need(8))[0]
            payload = self._need(n)
            if op == 9: continue            # ping
            if op == 8: raise OSError('closed by peer')
            data += payload
            if b1 & 0x80: return json.loads(data)
    def call(self, method, params=None, wait=12):
        self.id += 1; i = self.id; self.send({'id': i, 'method': method, 'params': params or {}}); end = time.time() + wait
        while time.time() < end:
            m = self.recv()
            if m.get('id') == i: return m
            self.events.append(m)
        raise TimeoutError(method)
    def wait_event(self, name, wait=8):
        end = time.time() + wait
        for m in list(self.events):
            if m.get('method') == name: self.events.remove(m); return m
        while time.time() < end:
            m = self.recv()
            if m.get('method') == name: return m
            self.events.append(m)
        return None

