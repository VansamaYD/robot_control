"""Bounded, pinned-snapshot renderer; worker cannot change authoritative state."""
from copy import deepcopy
import json
import os
import secrets
import signal
import subprocess
import threading
import time
from engine import ROOT, MotionError


class Renderer:
    def __init__(self, engine, port):
        self.engine, self.port = engine, port
        self.slots = threading.BoundedSemaphore(2)
        self.lock = threading.Lock()
        self.tickets = {}

    def read(self, ticket):
        with self.lock:
            entry = self.tickets.get(ticket)
            if not entry or entry['expires'] < time.monotonic():
                raise MotionError('RENDER_TICKET_EXPIRED')
            return deepcopy(entry['state'])

    def render(self, revision, width=1280, height=720, view='default', incarnation=None):
        if incarnation != self.engine.incarnation:
            raise MotionError('SESSION_CHANGED')
        if type(revision) is not int or type(width) is not int or type(height) is not int or not 320 <= width <= 1920 or not 240 <= height <= 1080 or view not in ('default', 'top', 'side', 'rack'):
            raise MotionError('INVALID_ARGUMENT')
        if not self.slots.acquire(blocking=False):
            raise MotionError('RENDER_BUSY')
        ticket = secrets.token_urlsafe(24)
        try:
            frame = self.engine.snapshot(revision)
            with self.lock:
                self.tickets[ticket] = {'state': frame, 'expires': time.monotonic() + 40}
            url = f'http://127.0.0.1:{self.port}/?capture=1&render_ticket={ticket}&view={view}'
            process = subprocess.Popen(['node', str(ROOT / 'render_frame.mjs'), url, str(width), str(height)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
            try:
                stdout, stderr = process.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGTERM)
                process.communicate(timeout=5)
                raise MotionError('RENDER_TIMEOUT') from None
            if process.returncode:
                raise MotionError('RENDERER_UNAVAILABLE')
            result = json.loads(stdout)
            if result['metadata']['state_revision'] != frame['state_revision'] or result['metadata']['incarnation'] != frame['incarnation']:
                raise MotionError('RENDER_STATE_MISMATCH')
            return result
        except OSError:
            raise MotionError('RENDERER_UNAVAILABLE') from None
        finally:
            with self.lock:
                self.tickets.pop(ticket, None)
            self.slots.release()
