#!/usr/bin/env python3
"""Serve the files of a release directory (os/make-release.py output) over plain HTTP, with Range requests, for testing the on-device update path against a build that is not published.

  os/dev/serve_release.py DIR [--port 8765] [--bind 0.0.0.0]

It serves only the files of DIR, read-only; the device still checks the manifest's signature and every file's hash, so what travels over plain http cannot change what is installed. Stop it with
Ctrl-C. The Range support is what makes Pause/Resume and a Wi-Fi drop cost nothing on the device (python's http.server has none)."""
import argparse, http.server, os, re, sys
from pathlib import Path


class Handler(http.server.BaseHTTPRequestHandler):
    root = Path('.')

    def log_message(self, fmt, *a): sys.stderr.write('%s %s\n' % (self.address_string(), fmt % a))

    def do_GET(self):
        name = self.path.split('?', 1)[0].lstrip('/')
        p = (self.root / name)
        if '/' in name or p.is_symlink() or not p.is_file(): self.send_error(404); return           # only regular files of the directory itself: no sub-directories, no links out of it
        size = p.stat().st_size; start, status = 0, 200
        m = re.fullmatch(r'bytes=(\d+)-', self.headers.get('Range') or '')                           # the only form a resume sends; any other Range is ignored (a full 200 is always allowed)
        if m:
            start, status = int(m.group(1)), 206
            if start >= size: self.send_response(416); self.send_header('Content-Range', 'bytes */%d' % size); self.send_header('Content-Length', '0'); self.end_headers(); return
        self.send_response(status); self.send_header('Content-Length', str(size - start)); self.send_header('Accept-Ranges', 'bytes')
        if status == 206: self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, size - 1, size))
        self.end_headers()
        try:
            with open(p, 'rb') as f:
                f.seek(start)
                while chunk := f.read(1 << 20): self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError): pass


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0]); ap.add_argument('dir'); ap.add_argument('--port', type=int, default=8765); ap.add_argument('--bind', default='0.0.0.0')
    a = ap.parse_args(argv)
    Handler.root = Path(a.dir).resolve()
    if not Handler.root.is_dir(): sys.exit('not a directory: %s' % a.dir)
    srv = http.server.ThreadingHTTPServer((a.bind, a.port), Handler); srv.daemon_threads = True
    print('serving %s on http://%s:%d/ (Ctrl-C to stop)' % (Handler.root, a.bind, a.port), flush=True)
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
