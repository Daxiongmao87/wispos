#!/usr/bin/env python3
"""Developer-machine listener for dev builds: receives the 'hello' a dev build's wisp-dev-announce sends once it has an address.

    python3 os/dev/listen.py [--port 47653] [--state DIR]

Binds 0.0.0.0 (this is a headless server) but only accepts connections from private (RFC 1918) or loopback addresses, so
nothing on the internet can write to it. Each hello is appended to DIR/devices.jsonl, and DIR/devices.json keeps the latest
per device (keyed by MAC), with a last-seen time. Read it to find where to SSH:  ssh -i ~/.ssh/wispos-dev root@<ip>
GET /devices returns the same JSON.
"""
import argparse, ipaddress, json, sys, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

MAX_BODY = 4096


# Loopback, RFC 1918 and IPv6 unique-local only. ipaddress's is_private is wider (documentation, link-local and
# reserved ranges), which is not what "a device on my own network" means.
LOCAL_NETS = tuple(ipaddress.ip_network(n) for n in ('127.0.0.0/8', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '::1/128', 'fc00::/7'))


def allowed(addr):
    ip = ipaddress.ip_address(addr)
    if getattr(ip, 'ipv4_mapped', None): ip = ip.ipv4_mapped
    return any(ip in net for net in LOCAL_NETS)


class State:
    def __init__(self, directory):
        self.dir = Path(directory); self.dir.mkdir(parents=True, exist_ok=True)

    def record(self, info, source):
        entry = {k: str(v)[:200] for k, v in info.items() if k in ('hostname', 'dev', 'ip', 'mac', 'kernel', 'state', 'failed', 'ssh_user')}
        entry['uptime'] = int(info.get('uptime', 0)) if str(info.get('uptime', '0')).isdigit() else 0
        entry['seen_from'] = source; entry['last_seen'] = int(time.time())
        with open(self.dir / 'devices.jsonl', 'a') as f:
            f.write(json.dumps(entry) + '\n')
        path = self.dir / 'devices.json'
        devices = json.loads(path.read_text()) if path.exists() else {}
        key = entry.get('mac') or entry.get('ip', 'unknown')
        new = key not in devices or devices[key].get('ip') != entry.get('ip')
        devices[key] = entry
        path.write_text(json.dumps(devices, indent=1, sort_keys=True))
        return entry, new


def make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _deny(self, code=403):
            self.send_response(code); self.end_headers()

        def do_POST(self):
            if not allowed(self.client_address[0]): return self._deny()
            if self.path != '/hello': return self._deny(404)
            n = int(self.headers.get('Content-Length', 0))
            if n <= 0 or n > MAX_BODY: return self._deny(413)
            try:
                info = json.loads(self.rfile.read(n))
                if not isinstance(info, dict): raise ValueError
            except ValueError:
                return self._deny(400)
            entry, new = state.record(info, self.client_address[0])
            if new:
                print(f"[{time.strftime('%H:%M:%S')}] device {entry.get('hostname')} at {entry.get('ip')} ({entry.get('mac')}), state {entry.get('state')}: "
                      f"ssh -i ~/.ssh/wispos-dev root@{entry.get('ip')}", flush=True)
            self.send_response(204); self.end_headers()

        def do_GET(self):
            if not allowed(self.client_address[0]): return self._deny()
            if self.path != '/devices': return self._deny(404)
            path = state.dir / 'devices.json'
            body = path.read_bytes() if path.exists() else b'{}'
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--port', type=int, default=47653)
    ap.add_argument('--state', default=str(Path.home() / '.wispos-dev'))
    args = ap.parse_args(argv)
    server = ThreadingHTTPServer(('0.0.0.0', args.port), make_handler(State(args.state)))
    print(f'listening on 0.0.0.0:{args.port}, state in {args.state}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    sys.exit(main())
