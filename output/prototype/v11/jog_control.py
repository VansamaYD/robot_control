"""Finite-lifetime, single-axis velocity jog. No browser-fed motion queue."""
import json
import math
import secrets
import time
import uuid
import numpy as np
from errors import MotionError
from arm_model import LOW, HIGH
from collision_model import clearance

LEASE_S = .35
SPEED = math.radians(4)
ACCEL = math.radians(20)


class JogMixin:
    def start_jog(self, joint_name, direction, key, incarnation):
        with self.changed:
            request = json.dumps([joint_name, direction, incarnation])
            if key in self.jog_requests:
                old, result = self.jog_requests[key]
                if request != old:
                    raise MotionError('IDEMPOTENCY_CONFLICT')
                return dict(result)
            if incarnation != self.incarnation:
                raise MotionError('SESSION_CHANGED')
            names = self.model['joint_order']
            if joint_name not in names or type(direction) is not int or direction not in (-1, 1):
                raise MotionError('INVALID_ARGUMENT')
            if self.job and self.job['status'] in ('planning', 'running', 'waiting_operator'):
                raise MotionError('CONTROL_BUSY')
            if clearance(self.q)[0] < 0 or np.any(self.q < LOW) or np.any(self.q > HIGH):
                raise MotionError('INVALID_START_STATE')
            self.epoch += 1
            job_id = 'run-' + uuid.uuid4().hex
            self.job = {'job_id': job_id, 'status': 'running', 'origin': 'jog', 'principal': 'ui',
                        'program_revision': 'jog', 'joint_name': joint_name, 'direction': direction}
            self.jobs[job_id] = self.job
            self.jog = {'lease': secrets.token_urlsafe(24), 'deadline': time.monotonic()+LEASE_S,
                        'index': names.index(joint_name), 'direction': direction, 'speed': 0.}
            self.queue.clear()
            self.action = {'kind': 'jog', 'name': f'长按点动 {joint_name}', 'complete': False}
            self.paused = False
            self.status = 'running'
            result = {'job_id': job_id, 'lease_token': self.jog['lease'], 'heartbeat_interval_ms': 100,
                      'lease_timeout_ms': 350, 'speed_deg_s': 4}
            self.jog_requests[key] = (request, dict(result))
            self.event('jog_started', joint_name=joint_name)
            self.publish()
            return result

    def _finish_jog(self, reason, status='cancelled'):
        if self.jog is None:
            return
        self.epoch += 1
        self.jog = None
        self.job.update(status=status, end_reason=reason)
        if status == 'failed':
            self.job['error'] = reason
        self.action['complete'] = True
        self.velocity[:] = 0
        self.target_q = self.q.copy()
        self.status = status
        self.paused = True
        self.event('jog_stopped', reason=reason)
        self.publish()
        self.changed.notify_all()

    def expire_jog(self, now=None):
        with self.changed:
            if self.jog and (time.monotonic() if now is None else now) >= self.jog['deadline']:
                self._finish_jog('LEASE_EXPIRED')

    def heartbeat_jog(self, lease):
        with self.changed:
            self.expire_jog()
            if not self.jog or not secrets.compare_digest(str(lease), self.jog['lease']):
                raise MotionError('STALE_JOG_LEASE')
            self.jog['deadline'] = time.monotonic()+LEASE_S
            return {'active': True, 'lease_timeout_ms': 350}

    def stop_jog(self, lease, reason='released'):
        with self.changed:
            if not self.jog or not secrets.compare_digest(str(lease), self.jog['lease']):
                raise MotionError('STALE_JOG_LEASE')
            if reason not in ('released', 'blur', 'hidden', 'pointer_cancel', 'pagehide'):
                raise MotionError('INVALID_ARGUMENT')
            self._finish_jog(reason, 'succeeded' if reason == 'released' else 'cancelled')
            return self.snapshot()

    def advance_jog(self, dt):
        self.expire_jog()
        if not self.jog:
            return False
        self.jog['speed'] = min(SPEED, self.jog['speed'] + ACCEL*dt)
        goal = self.q.copy()
        goal[self.jog['index']] += self.jog['direction']*self.jog['speed']*dt
        error = 'LIMIT_VIOLATION' if np.any(goal < LOW) or np.any(goal > HIGH) else 'COLLISION' if clearance(goal)[0] < 0 else None
        if error:
            self._finish_jog(error, 'failed')
            return False
        self.q = goal
        self.target_q = goal.copy()
        self.action_tick += 1
        return True
