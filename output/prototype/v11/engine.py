"""Minimal trusted-code -> checked motion -> authoritative snapshots spike.

Not a user Python sandbox, dynamics engine, liquid solver or production controller.
"""
from collections import deque
from copy import deepcopy
from pathlib import Path
import hashlib
import inspect
import json
import math
import sys
import threading
import time
import traceback
import xml.etree.ElementTree as ET

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / 'v03'))
import arm_model as arm
from trajectory_sim import clearance, BASE
from flow import main

DT = .02
NAMES = [entry[0] for entry in arm.JOINTS]


def vector(node, name, default='0 0 0'):
    return [float(v) for v in (node.attrib.get(name, default) if node is not None else default).split()]


def load_model():
    tree = ET.parse(ROOT / 'assets/so101_new_calib.urdf').getroot()
    joints = []
    for joint in tree.findall('joint'):
        origin = joint.find('origin')
        joints.append({'name': joint.attrib['name'], 'type': joint.attrib['type'],
                       'parent': joint.find('parent').attrib['link'],
                       'child': joint.find('child').attrib['link'],
                       'xyz': vector(origin, 'xyz'), 'rpy': vector(origin, 'rpy'),
                       'axis': vector(joint.find('axis'), 'xyz'),
                       'limit': {k: float(v) for k, v in joint.find('limit').attrib.items()}
                       if joint.find('limit') is not None else None})
    links = []
    for link in tree.findall('link'):
        visuals = []
        if link.attrib['name'] != 'moving_jaw_so101_v1_link':
            for visual in link.findall('visual'):
                mesh = visual.find('geometry/mesh')
                # Custom ladle replaces the gripper actuator, not the wrist.
                if link.attrib['name'] == 'gripper_link' and 'sts3215' in mesh.attrib['filename']:
                    continue
                visuals.append({'mesh': mesh.attrib['filename'], 'xyz': vector(visual.find('origin'), 'xyz'),
                                'rpy': vector(visual.find('origin'), 'rpy'),
                                'scale': vector(mesh, 'scale', '1 1 1')})
        links.append({'name': link.attrib['name'], 'visuals': visuals})
    # Reject any divergence between the old solver and pinned canonical URDF.
    by_name = {joint['name']: joint for joint in joints}
    for index, name in enumerate(NAMES):
        j = by_name[name]
        assert np.allclose(j['axis'], [0, 0, 1], atol=1e-14)
        transform = np.eye(4)
        transform[:3, :3] = arm.rpy(j['rpy'])
        transform[:3, 3] = j['xyz']
        assert np.allclose(transform, arm.ORIGINS[index], atol=1e-12), name
        assert np.allclose([j['limit']['lower'], j['limit']['upper']],
                           [arm.LOW[index], arm.HIGH[index]], atol=1e-12), name
    return {'joints': joints, 'links': links, 'joint_order': NAMES, 'base': BASE.tolist(),
            'tool_offset_m': arm.TOOL.tolist(), 'tool_normal_axis': [1, 0, 0],
            'omitted': ['moving jaw', 'original gripper actuator'],
            'scope': 'official meshes, custom assumed ladle; no mesh collision'}


class MotionError(Exception):
    pass


class Robot:
    def move_tcp(self, name, position_m, normal, duration_s):
        return {'name': name, 'p': position_m, 'n': normal, 'duration_s': duration_s,
                'source_line': inspect.currentframe().f_back.f_lineno}


class Engine:
    def __init__(self, realtime=True):
        self.model = load_model()
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.incarnation = f'boot-{time.monotonic_ns()}'
        self.config = 'fixed-S_BASE-v11'
        self.code_source = (ROOT / 'flow.py').read_text()
        self.code_hash = hashlib.sha256(self.code_source.encode()).hexdigest()
        self.q = np.deg2rad([-.0386, 22.7339, 65.0816, -87.8151, 2.7893])
        self.tick = 0
        self.rev = 0
        self.epoch = 0
        self.paused = True
        self.speed = 1
        self.status = 'ready'
        self.job = None
        self.action = None
        self.queue = deque()
        self.events = []
        self.frames = {}
        self.history = deque()
        self.next_job = 0
        self.requests = {}
        self.shutdown = False
        self.publish()
        if realtime:
            threading.Thread(target=self._clock, daemon=True).start()

    def publish(self):
        self.rev += 1
        p, n, _, _, links = arm.fk(self.q, BASE)
        frame = {'session_id': 'local-v11', 'incarnation': self.incarnation,
                 'state_revision': self.rev, 'configuration_revision': self.config,
                 'tick': self.tick, 'dt_s': DT, 'simulation_time_s': round(self.tick * DT, 9),
                 'source': 'simulation', 'quality': 'ideal_kinematics',
                 'q_rad': self.q.tolist(), 'joint_names': NAMES,
                 'tcp_m': p.tolist(), 'normal_world': n.tolist(),
                 'link_matrices': [matrix.tolist() for matrix in links],
                 'paused': self.paused, 'status': self.status,
                 'job': deepcopy(self.job), 'action': deepcopy(self.action),
                 'speed': self.speed, 'code_hash': self.code_hash,
                 'electrical': {'current_a': None, 'temperature_c': None, 'quality': 'unavailable'},
                 'liquid': {'mode': 'static_visual_only', 'height_m': .08}}
        self.frames[self.rev] = frame
        self.history.append(self.rev)
        while len(self.history) > 6000:
            del self.frames[self.history.popleft()]
        return frame

    def snapshot(self, revision=None):
        with self.lock:
            frame = self.frames.get(self.rev if revision is None else revision)
            if frame is None:
                raise MotionError('SNAPSHOT_EXPIRED')
            return deepcopy(frame)

    def event(self, event_name, **details):
        self.events.append({'seq': len(self.events) + 1, 'tick': self.tick,
                            'event': event_name, **details})

    def _clock(self):
        previous = time.monotonic()
        accumulator = 0.
        while not self.shutdown:
            time.sleep(.003)
            now = time.monotonic()
            with self.lock:
                if not self.paused and self.queue:
                    accumulator += min(now - previous, .1) * self.speed
                    while accumulator >= DT:
                        self.advance()
                        accumulator -= DT
                else:
                    accumulator = 0.
            previous = now

    def advance(self, single=False):
        with self.changed:
            if self.paused and not single:
                return False
            if not self.queue:
                return False
            sample = self.queue.popleft()
            self.q = sample
            self.tick += 1
            if not self.queue:
                self.action['complete'] = True
                self.event('action_completed', name=self.action['name'])
            self.publish()
            self.changed.notify_all()
            return True

    def control(self, command):
        with self.changed:
            if command == 'pause':
                self.paused = True
            elif command == 'resume':
                if not self.job or self.job['status'] not in ('running', 'planning'):
                    raise MotionError('NO_ACTIVE_JOB')
                self.paused = False
            elif command == 'step_tick':
                if not self.paused:
                    raise MotionError('NOT_PAUSED')
                self.advance(single=True)
            elif command == 'cancel':
                self.epoch += 1
                self.queue.clear()
                if self.job and self.job['status'] in ('planning', 'running'):
                    self.job['status'] = 'cancelled'
                self.status = 'cancelled'
                self.paused = True
                self.event('cancelled')
            else:
                raise MotionError('UNKNOWN_COMMAND')
            self.publish()
            self.changed.notify_all()
            return self.snapshot()

    def start(self, key, expected_configuration=None):
        with self.changed:
            request = {'configuration': expected_configuration or self.config, 'code_hash': self.code_hash}
            fingerprint = json.dumps(request, sort_keys=True)
            if key in self.requests:
                old_fingerprint, job_id = self.requests[key]
                if fingerprint != old_fingerprint:
                    raise MotionError('IDEMPOTENCY_CONFLICT')
                return job_id
            if request['configuration'] != self.config:
                raise MotionError('CONFIGURATION_CHANGED')
            if self.job and self.job['status'] in ('planning', 'running'):
                raise MotionError('CONTROL_BUSY')
            self.epoch += 1
            epoch = self.epoch
            self.next_job += 1
            job_id = f'run-{self.next_job}'
            self.job = {'job_id': job_id, 'status': 'planning', 'code_hash': self.code_hash}
            self.status = 'planning'
            self.paused = False
            self.queue.clear()
            self.action = None
            self.requests[key] = (fingerprint, job_id)
            self.event('job_created', job_id=job_id)
            self.publish()
            threading.Thread(target=self._run, args=(epoch,), daemon=True).start()
            return job_id

    def plan(self, action, start):
        p0, n0, *_ = arm.fk(start, BASE)
        target = np.asarray(action['p'], dtype=float)
        normal = np.asarray(action['n'], dtype=float)
        duration = action['duration_s']
        if (target.shape != (3,) or normal.shape != (3,) or not np.isfinite(target).all()
                or not np.isfinite(normal).all() or not math.isfinite(duration)
                or duration <= 0 or duration > 120 or np.linalg.norm(normal) < 1e-8):
            raise MotionError('INVALID_ARGUMENT')
        if np.linalg.norm(target - BASE[:3]) > 1:
            raise MotionError('NO_IK')
        normal = normal / np.linalg.norm(normal)
        count = math.ceil(duration / DT)
        result = []
        q = start.copy()
        minimum_gap = float('inf')
        for index in range(1, count + 1):
            u = index / count
            u = 10*u**3 - 15*u**4 + 6*u**5
            p = p0 + (target - p0) * u
            n = n0 + (normal - n0) * u
            if np.linalg.norm(n) < 1e-6:
                raise MotionError('UNSUPPORTED_NORMAL_PATH')
            n /= np.linalg.norm(n)
            q, pe, ne = arm.solve(p, n, BASE, seed=q, restarts=0)
            if pe > .0002 or ne > .002:
                raise MotionError('NO_IK')
            gap = clearance(q)[0]
            minimum_gap = min(gap, minimum_gap)
            if gap < 0:
                raise MotionError('COLLISION')
            result.append(q.copy())
        samples = np.array([start] + result)
        velocity = np.diff(samples, axis=0) / DT
        accel = np.diff(velocity, axis=0) / DT
        if np.max(np.abs(velocity)) > np.deg2rad(60) or np.max(np.abs(accel), initial=0) > np.deg2rad(150):
            raise MotionError('LIMIT_VIOLATION')
        return result, minimum_gap

    def _run(self, epoch):
        try:
            for action in main(Robot()):
                with self.changed:
                    if epoch != self.epoch:
                        return
                    start = self.q.copy()
                    self.status = 'planning'
                    self.job['status'] = 'planning'
                    self.action = {**action, 'complete': False}
                    self.publish()
                path, gap = self.plan(action, start)
                with self.changed:
                    if epoch != self.epoch:
                        return
                    if not np.allclose(start, self.q, atol=1e-12):
                        raise MotionError('START_STATE_CHANGED')
                    self.queue.extend(path)
                    self.status = 'running'
                    self.job['status'] = 'running'
                    self.action['coarse_clearance_m'] = gap
                    self.event('action_started', name=action['name'], line=action['source_line'],
                               clearance_m=gap)
                    self.publish()
                    while self.queue and epoch == self.epoch:
                        self.changed.wait(.1)
                    if epoch != self.epoch:
                        return
            with self.changed:
                if epoch == self.epoch:
                    self.status = 'succeeded'
                    self.job['status'] = 'succeeded'
                    self.paused = True
                    self.epoch += 1
                    self.event('job_succeeded')
                    self.publish()
        except Exception as error:
            with self.changed:
                if epoch == self.epoch:
                    self.queue.clear()
                    self.status = 'failed'
                    self.job['status'] = 'failed'
                    self.job['error'] = str(error)
                    self.paused = True
                    self.epoch += 1
                    self.event('job_failed', error=str(error))
                    self.publish()
                    traceback.print_exc()
