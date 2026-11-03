"""Loopback-only chain-validation server. Runs only the bundled trusted flow."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
import argparse
import json
import secrets

from engine import Engine, ROOT, MotionError


def serve(port):
    engine = Engine()
    token = secrets.token_urlsafe(32)

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(ROOT), **kwargs)

        def log_message(self, *_):
            pass

        def reply(self, data, status=200):
            raw = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def valid_host(self):
            return self.headers.get('Host') in (f'127.0.0.1:{port}', f'localhost:{port}')

        def do_GET(self):
            if not self.valid_host():
                return self.reply({'error': 'INVALID_HOST'}, 403)
            path = urlparse(self.path)
            try:
                if path.path == '/api/bootstrap':
                    return self.reply({'token': token, 'code': engine.code_source,
                                       'model': engine.model, 'dt_s': .02})
                if path.path == '/api/state':
                    revision = parse_qs(path.query).get('revision', [None])[0]
                    return self.reply(engine.snapshot(int(revision) if revision is not None else None))
                if path.path == '/api/events':
                    with engine.lock:
                        return self.reply(engine.events.copy())
                if path.path.startswith('/api/'):
                    return self.reply({'error': 'NOT_FOUND'}, 404)
                # Serve only the app entry and pinned static dependency assets.
                if path.path not in ('/', '/index.html', '/app.js', '/style.css') and not path.path.startswith('/assets/'):
                    return self.reply({'error': 'NOT_FOUND'}, 404)
                if path.path.startswith('/assets/') and not (ROOT / unquote(path.path).lstrip('/')).resolve().is_relative_to((ROOT / 'assets').resolve()):
                    return self.reply({'error': 'INVALID_PATH'}, 403)
                return super().do_GET()
            except (ValueError, MotionError) as error:
                return self.reply({'error': str(error)}, 400)

        def do_POST(self):
            if not self.valid_host() or self.headers.get('X-Session-Token') != token:
                return self.reply({'error': 'NOT_AUTHORIZED'}, 403)
            origin = self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{port}', f'http://localhost:{port}'):
                return self.reply({'error': 'INVALID_ORIGIN'}, 403)
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size < 16384:
                    raise MotionError('INVALID_ARGUMENT')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise MotionError('INVALID_ARGUMENT')
                if self.path == '/api/run':
                    key = data.get('idempotency_key')
                    if not isinstance(key, str) or not 0 < len(key) <= 200:
                        raise MotionError('INVALID_ARGUMENT')
                    return self.reply({'job_id': engine.start(key, data.get('configuration_revision'))})
                if self.path == '/api/control':
                    return self.reply(engine.control(data.get('command')))
                if self.path == '/api/speed':
                    speed = data.get('speed')
                    if speed not in (1, 4, 8):
                        raise MotionError('INVALID_ARGUMENT')
                    with engine.lock:
                        engine.speed = speed
                        engine.publish()
                    return self.reply({'speed': speed})
                return self.reply({'error': 'NOT_FOUND'}, 404)
            except (ValueError, TypeError, MotionError) as error:
                return self.reply({'error': str(error)}, 409)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print(f'Chain prototype: http://127.0.0.1:{port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    serve(parser.parse_args().port)
