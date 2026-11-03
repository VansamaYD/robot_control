"""Loopback-only chain-validation server. Runs only the bundled trusted flow."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
import argparse
import json
import secrets
import threading
from render_service import Renderer

from engine import Engine, ROOT, MotionError, Robot, main
from projects import ProjectStore
from scene_geometry import SCENE


def serve(port):
    store = ProjectStore(ROOT.parents[2] / 'data/projects')
    default = store.save('默认浇汁流程', [{k: v for k, v in action.items() if k != 'source_line'} for action in main(Robot())])
    engine = Engine(default_program=default)
    token = secrets.token_urlsafe(32)
    clients = {token: {'principal': 'ui', 'origin': 'ui'}}
    clients_lock = threading.Lock()
    renderer = Renderer(engine, port)

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
                    render_ticket = parse_qs(path.query).get('render_ticket', [None])[0]
                    if render_ticket is not None:
                        renderer.read(render_ticket)
                    return self.reply({**({'token': token} if render_ticket is None else {}), 'code': engine.code_source,
                                       'model': engine.model, 'scene': SCENE, 'default_program': default,
                                       'projects': store.list(), 'dt_s': .02})
                if path.path == '/api/capabilities':
                    return self.reply({'version': '0.15.0', 'mode': 'simulation', 'mcp_stdio': True,
                                       'render_snapshot': True, 'manual_joint_steps': True,
                                       'leased_jog': True, 'recovery_presets': True,
                                       'natural_liquid_exchange': True, 'dock_dry_interlock': True,
                                       'arbitrary_python': False, 'hardware': False, 'vision': False})
                if path.path == '/api/projects':
                    return self.reply(store.list())
                if path.path.startswith('/api/projects/'):
                    return self.reply(store.get(path.path.rsplit('/', 1)[-1]))
                if path.path == '/api/state':
                    render_ticket = parse_qs(path.query).get('render_ticket', [None])[0]
                    if render_ticket is not None:
                        return self.reply(renderer.read(render_ticket))
                    expected_boot = parse_qs(path.query).get('incarnation', [engine.incarnation])[0]
                    if expected_boot != engine.incarnation:
                        raise MotionError('SESSION_CHANGED')
                    revision = parse_qs(path.query).get('revision', [None])[0]
                    return self.reply(engine.snapshot(int(revision) if revision is not None else None))
                if path.path.startswith('/api/jobs/'):
                    return self.reply(engine.get_job(path.path.rsplit('/', 1)[-1]))
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
            with clients_lock:
                client = clients.get(self.headers.get('X-Session-Token'))
            if not self.valid_host() or client is None:
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
                if self.path == '/api/clients':
                    if client['principal'] != 'ui':
                        raise MotionError('NOT_AUTHORIZED')
                    with clients_lock:
                        if len(clients) >= 32:
                            raise MotionError('CLIENT_LIMIT')
                        capability = secrets.token_urlsafe(32)
                        identity = {'principal': 'mcp:' + secrets.token_hex(12), 'origin': 'mcp'}
                        clients[capability] = identity
                    return self.reply({'token': capability, **identity})
                if self.path == '/api/run':
                    key = data.get('idempotency_key')
                    if not isinstance(key, str) or not 0 < len(key) <= 200:
                        raise MotionError('INVALID_ARGUMENT')
                    program = store.get(data['program_revision']) if 'program_revision' in data else default
                    return self.reply({'job_id': engine.start(key, data.get('configuration_revision'), program, **client)})
                if self.path == '/api/manual':
                    if client['principal'] != 'ui':
                        raise MotionError('NOT_AUTHORIZED')
                    key = data.get('idempotency_key')
                    if not isinstance(key, str) or not 0 < len(key) <= 200:
                        raise MotionError('INVALID_ARGUMENT')
                    return self.reply({'job_id': engine.manual(data.get('joint_name'), data.get('delta_deg'), key, data.get('expected_state_revision'), data.get('incarnation'))})
                if self.path in ('/api/jog/start', '/api/jog/heartbeat', '/api/jog/stop', '/api/preset', '/api/reset'):
                    if client['principal'] != 'ui':
                        raise MotionError('NOT_AUTHORIZED')
                    if self.path in ('/api/jog/start', '/api/preset'):
                        key = data.get('idempotency_key')
                        if not isinstance(key, str) or not 0 < len(key) <= 200:
                            raise MotionError('INVALID_ARGUMENT')
                    if self.path == '/api/jog/start':
                        return self.reply(engine.start_jog(data.get('joint_name'), data.get('direction'), key, data.get('incarnation')))
                    if self.path == '/api/jog/heartbeat':
                        return self.reply(engine.heartbeat_jog(data.get('lease_token')))
                    if self.path == '/api/jog/stop':
                        return self.reply(engine.stop_jog(data.get('lease_token'), data.get('reason', 'released')))
                    if self.path == '/api/preset':
                        return self.reply({'job_id': engine.start_preset(data.get('mode'), key, data.get('incarnation'))})
                    return self.reply(engine.reset_simulation(data.get('incarnation'), data.get('confirmation')))
                if self.path == '/api/images':
                    return self.reply(renderer.render(data.get('state_revision'), data.get('width', 1280), data.get('height', 720), data.get('view', 'default'), data.get('incarnation')))
                if self.path == '/api/validate':
                    program = store.get(data.get('program_revision'))
                    frame = engine.snapshot()
                    import numpy as np
                    q = np.array(frame['q_rad'])
                    samples, gap = 0, float('inf')
                    for action in program['actions']:
                        path_samples, clearance_m = engine.plan(action, q)
                        q = path_samples[-1]
                        samples += len(path_samples)
                        gap = min(gap, clearance_m)
                    return self.reply({'valid': True, 'level': 'kinematic_path', 'liquid_validation': False,
                                       'program_revision': program['revision'], 'start_state_revision': frame['state_revision'],
                                       'configuration_revision': frame['configuration_revision'],
                                       'samples': samples, 'minimum_coarse_clearance_m': gap,
                                       'note': 'Motion will be checked again on execution; liquid availability is checked at runtime.'})
                if self.path == '/api/projects':
                    return self.reply(store.save(data.get('name'), data.get('actions')))
                if self.path == '/api/control':
                    if client['principal'] != 'ui' and not isinstance(data.get('job_id'), str):
                        raise MotionError('JOB_ID_REQUIRED')
                    return self.reply(engine.control(data.get('command'), data.get('job_id'), client['principal']))
                if self.path == '/api/speed':
                    speed = data.get('speed')
                    if type(speed) is not int or speed not in (1, 4, 8) or client['principal'] != 'ui':
                        raise MotionError('INVALID_ARGUMENT')
                    with engine.lock:
                        engine.speed = speed
                        engine.publish()
                    return self.reply({'speed': speed})
                return self.reply({'error': 'NOT_FOUND'}, 404)
            except (ValueError, TypeError, MotionError) as error:
                return self.reply({'error': str(error), 'details': getattr(error, 'details', None)}, 409)

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print(f'Chain prototype: http://127.0.0.1:{port}', flush=True)
    server.serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    serve(parser.parse_args().port)
