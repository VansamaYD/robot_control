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
import uuid
import xml.etree.ElementTree as ET

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / 'v03'))
import arm_model as arm
from trajectory_sim import clearance, BASE, CONFIG as LEGACY_CONFIG
from flow import main
from liquid import Liquid
from scene_geometry import SCENE
from projects import code_view
from errors import MotionError
from jog_control import JogMixin
from presets import HOME_Q, DOCK, recovery_candidates

DT = .02
NAMES = [entry[0] for entry in arm.JOINTS]


def vector(node, name, default='0 0 0'):
    return [float(v) for v in (node.attrib.get(name, default) if node is not None else default).split()]


def load_model():
    if (SCENE['pot']['radius_m'] * 2000 != LEGACY_CONFIG['pot_diameter_mm']
            or SCENE['pot']['depth_m'] * 1000 != LEGACY_CONFIG['pot_depth_mm']
            or SCENE['chicken']['center_m'][1] * 1000 != 50
            or SCENE['chicken']['radii_m'] != [.13, .09, .075]):
        raise MotionError('COLLISION_GEOMETRY_NOT_SUPPORTED')
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


class Robot:
    def move_joints(self, name, q_rad, duration_s):
        return {'kind': 'move_joints', 'name': name, 'q_rad': q_rad, 'duration_s': duration_s,
                'source_line': inspect.currentframe().f_back.f_lineno}

    def move_tcp(self, name, position_m, normal, duration_s):
        return {'kind': 'move_tcp', 'name': name, 'p': position_m, 'n': normal, 'duration_s': duration_s,
                'source_line': inspect.currentframe().f_back.f_lineno}

    def scoop(self, name, target_ml, duration_s):
        return {'kind': 'scoop', 'name': name, 'target_m3': target_ml * 1e-6,
                'duration_s': duration_s, 'source_line': inspect.currentframe().f_back.f_lineno}

    def wait(self, name, duration_s):
        return {'kind': 'wait', 'name': name, 'duration_s': duration_s,
                'source_line': inspect.currentframe().f_back.f_lineno}


class Engine(JogMixin):
    def __init__(self, realtime=True, default_program=None):
        self.model = load_model()
        self.lock = threading.RLock()
        self.changed = threading.Condition(self.lock)
        self.incarnation = f'boot-{time.monotonic_ns()}'
        self.config = SCENE['revision']
        self.default_program = deepcopy(default_program)
        self.code_source = default_program['code'] if default_program else (ROOT / 'flow.py').read_text()
        self.code_hash = hashlib.sha256(self.code_source.encode()).hexdigest()
        self.q = HOME_Q.copy()
        self.target_q = self.q.copy()
        self.velocity = np.zeros(5)
        self.tick = 0
        self.action_tick = 0
        self.liquid = Liquid()
        self.rev = 0
        self.epoch = 0
        self.paused = True
        self.speed = 1
        self.status = 'ready'
        self.job = None
        self.jobs = {}
        self.action = None
        self.queue = deque()
        self.events = []
        self.frames = {}
        self.history = deque()
        self.next_job = 0
        self.requests = {}
        self.manual_requests = {}
        self.jog_requests = {}
        self.preset_requests = {}
        self.jog = None
        self.shutdown = False
        self.publish()
        if realtime:
            threading.Thread(target=self._clock, daemon=True).start()

    def publish(self):
        self.rev += 1
        p, n, _, _, links = arm.fk(self.q, BASE)
        frame = {'session_id': 'local-v11', 'incarnation': self.incarnation,
                 'state_revision': self.rev, 'configuration_revision': self.config,
                 'tick': self.tick, 'action_tick': self.action_tick, 'dt_s': DT, 'simulation_time_s': round(self.tick * DT, 9),
                 'source': 'simulation', 'quality': 'ideal_kinematics',
                 'q_rad': self.q.tolist(), 'target_q_rad': self.target_q.tolist(),
                 'velocity_rad_s': self.velocity.tolist(), 'joint_names': NAMES,
                 'joint_limits_rad': np.column_stack([arm.LOW, arm.HIGH]).tolist(),
                 'tcp_m': p.tolist(), 'normal_world': n.tolist(),
                 'link_matrices': [matrix.tolist() for matrix in links],
                 'paused': self.paused, 'status': self.status,
                 'job': deepcopy(self.job), 'action': deepcopy(self.action),
                 'speed': self.speed, 'code_hash': self.code_hash,
                 'electrical': {'current_a': None, 'temperature_c': None, 'quality': 'unavailable'},
                 'liquid': self.liquid.snapshot()}
        frame['scoop_readiness'] = self.liquid.scoop_diagnostics(p, n, 30e-6)
        frame['jog'] = {'active': self.jog is not None, 'speed_deg_s': 4, 'lease_timeout_ms': 350}
        frame['parking'] = {'target_m': DOCK, 'virtual_parked': bool(np.linalg.norm(p-DOCK) < .001),
                            'support_contact_validated': False}
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
        self.events.append({'seq': len(self.events) + 1, 'tick': self.tick, 'incarnation': self.incarnation,
                            'event': event_name, **details})

    def get_job(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise MotionError('JOB_NOT_FOUND')
            return deepcopy(self.jobs[job_id])

    def _clock(self):
        previous = time.monotonic()
        accumulator = 0.
        while not self.shutdown:
            time.sleep(.003)
            now = time.monotonic()
            with self.lock:
                self.expire_jog(now)
                if not self.paused and (self.queue or (self.job and self.job['status'] in ('planning', 'running'))):
                    accumulator += min(now - previous, .1) * (1 if self.jog else self.speed)
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
            if not self.queue and not (self.job and self.job['status'] in ('planning', 'running')):
                return False
            moving = bool(self.queue)
            previous_q = self.q.copy()
            jogging = self.jog is not None
            if jogging:
                if not self.advance_jog(DT):
                    return False
            elif moving:
                self.q = self.queue.popleft()
                self.action_tick += 1
            self.velocity = (self.q - previous_q) / DT
            self.tick += 1
            p, n, *_ = arm.fk(self.q, BASE)
            target = self.action.get('target_m3') if moving and self.action and self.action.get('kind') == 'scoop' else None
            self.liquid.advance(DT, p, n, target)
            if moving and not self.queue:
                self.action['complete'] = True
                if self.action.get('kind') == 'scoop' and self.liquid.ladle < self.action['target_m3'] - 1e-9:
                    self.epoch += 1
                    self.status = 'failed'
                    self.job.update(status='failed', error='LIQUID_UNAVAILABLE',
                                    error_details=self.liquid.scoop_diagnostics(p, n, self.action['target_m3']))
                    self.paused = True
                    self.event('job_failed', error='LIQUID_UNAVAILABLE')
                    self.event('action_failed', name=self.action['name'], error='LIQUID_UNAVAILABLE')
                else:
                    self.event('action_completed', name=self.action['name'])
            self.publish()
            self.changed.notify_all()
            return True

    def control(self, command, job_id=None, principal='ui'):
        with self.changed:
            if job_id is not None and (not self.job or self.job['job_id'] != job_id):
                previous = self.get_job(job_id)
                if previous['principal'] != principal and principal != 'ui':
                    raise MotionError('NOT_AUTHORIZED')
                if command == 'cancel' and previous['status'] in ('cancelled', 'failed', 'succeeded'):
                    return self.snapshot()
                raise MotionError('JOB_NOT_ACTIVE')
            if self.job and self.job['principal'] != principal and principal != 'ui':
                raise MotionError('NOT_AUTHORIZED')
            if self.jog and command in ('pause', 'cancel'):
                self._finish_jog('operator_' + command)
                return self.snapshot()
            if command == 'pause':
                self.paused = True
                self.velocity[:] = 0
                if self.job and self.job.get('preplanning'):
                    self.job['start_after_planning'] = False
            elif command == 'resume':
                if not self.job or self.job['status'] not in ('running', 'planning'):
                    raise MotionError('NO_ACTIVE_JOB')
                self.paused = False
                if self.job.get('preplanning'):
                    self.job['start_after_planning'] = True
                    self.paused = True
            elif command == 'step_tick':
                if self.job and self.job.get('preplanning'):
                    raise MotionError('PREFLIGHT_BUSY')
                if not self.paused:
                    raise MotionError('NOT_PAUSED')
                self.advance(single=True)
            elif command == 'cancel':
                self.epoch += 1
                self.queue.clear()
                if self.job and self.job['status'] in ('planning', 'running', 'waiting_operator'):
                    self.job['status'] = 'cancelled'
                    self.job['preplanning'] = False
                self.status = 'cancelled'
                self.paused = True
                self.target_q = self.q.copy()
                self.velocity[:] = 0
                self.event('cancelled')
            else:
                raise MotionError('UNKNOWN_COMMAND')
            self.publish()
            self.changed.notify_all()
            return self.snapshot()

    def manual(self, joint_name, delta_deg, key, expected_revision):
        with self.changed:
            request = json.dumps([joint_name, delta_deg, expected_revision], allow_nan=False)
            if key in self.manual_requests:
                previous, job_id = self.manual_requests[key]
                if request != previous:
                    raise MotionError('IDEMPOTENCY_CONFLICT')
                return job_id
            if joint_name not in NAMES or isinstance(delta_deg, bool) or not isinstance(delta_deg, (int, float)) or not math.isfinite(delta_deg) or not 0 < abs(delta_deg) <= 5:
                raise MotionError('INVALID_ARGUMENT')
            if expected_revision != self.rev:
                raise MotionError('START_STATE_CHANGED')
            goal = self.q.copy()
            goal[NAMES.index(joint_name)] += math.radians(delta_deg)
            actions = [{'kind': 'move_joints', 'name': f'手动 {joint_name} {delta_deg:+g}°', 'q_rad': goal.tolist(), 'duration_s': 1.}]
            program = {'revision': 'manual', 'code': code_view(actions), 'actions': actions}
            job_id = self.start('manual:' + key, program=program, principal='ui', origin='manual')
            self.manual_requests[key] = (request, job_id)
            return job_id

    def start(self, key, expected_configuration=None, program=None, principal='ui', origin='ui'):
        with self.changed:
            program = deepcopy(program or self.default_program)
            source = program['code'] if program else (ROOT / 'flow.py').read_text()
            source_hash = hashlib.sha256(source.encode()).hexdigest()
            request = {'configuration': expected_configuration or self.config, 'code_hash': source_hash,
                       'program_revision': program['revision'] if program else 'trusted-builtin'}
            fingerprint = json.dumps(request, sort_keys=True)
            scope = (principal, key)
            if scope in self.requests:
                old_fingerprint, job_id = self.requests[scope]
                if fingerprint != old_fingerprint:
                    raise MotionError('IDEMPOTENCY_CONFLICT')
                return job_id
            if request['configuration'] != self.config:
                raise MotionError('CONFIGURATION_CHANGED')
            if self.job and self.job['status'] in ('planning', 'running', 'waiting_operator'):
                raise MotionError('CONTROL_BUSY')
            first = program['actions'][0] if program and program.get('actions') else next(main(Robot())) if not program else None
            if first and first['kind'] == 'scoop':
                p, n, *_ = arm.fk(self.q, BASE)
                readiness = self.liquid.scoop_diagnostics(p, n, first['target_m3'])
                if not readiness['ready']:
                    raise MotionError('SCOOP_PRECONDITION_FAILED', readiness)
            self.epoch += 1
            epoch = self.epoch
            self.next_job += 1
            job_id = f'run-{uuid.uuid4().hex}'
            self.code_source, self.code_hash = source, source_hash
            self.job = {'job_id': job_id, 'status': 'planning', 'code_hash': self.code_hash,
                        'principal': principal, 'origin': origin,
                        'program_revision': request['program_revision'], 'start_action_tick': self.action_tick}
            self.jobs[job_id] = self.job
            self.status = 'planning'
            self.paused = False
            if program and program.get('preset'):
                self.job.update(preplanning=True, start_after_planning=True, preset=program['preset'])
                self.paused = True
            self.queue.clear()
            self.action = None
            self.requests[scope] = (fingerprint, job_id)
            self.event('job_created', job_id=job_id)
            self.publish()
            threading.Thread(target=self._run, args=(epoch, program), daemon=True).start()
            return job_id

    def reset_simulation(self, incarnation, confirmation):
        with self.changed:
            if incarnation != self.incarnation or confirmation != 'RESET_SIMULATION':
                raise MotionError('RESET_CONFIRMATION_REQUIRED')
            if self.jog:
                self._finish_jog('simulation_reset')
            if self.job and self.job['status'] in ('planning', 'running', 'waiting_operator'):
                self.job.update(status='cancelled', end_reason='simulation_reset')
            self.epoch += 1
            self.queue.clear()
            self.incarnation = 'boot-' + uuid.uuid4().hex
            self.q = HOME_Q.copy()
            self.target_q = self.q.copy()
            self.velocity[:] = 0
            self.liquid = Liquid()
            self.tick = self.action_tick = self.rev = 0
            self.frames.clear(); self.history.clear()
            self.job = self.action = None
            self.status = 'ready'
            self.paused = True
            self.code_source = self.default_program['code'] if self.default_program else (ROOT/'flow.py').read_text()
            self.code_hash = hashlib.sha256(self.code_source.encode()).hexdigest()
            self.event('simulation_reset')
            return self.publish()

    def start_preset(self, mode, key, incarnation):
        with self.changed:
            fingerprint = json.dumps([mode, incarnation])
            if key in self.preset_requests:
                old, job_id = self.preset_requests[key]
                if old != fingerprint:
                    raise MotionError('IDEMPOTENCY_CONFLICT')
                return job_id
            if mode not in ('home', 'dock') or incarnation != self.incarnation:
                raise MotionError('INVALID_PRESET_CONTEXT')
            if self.job and self.job['status'] == 'waiting_operator' and mode == 'home':
                self.job.update(status='succeeded', end_reason='operator_handoff')
                self.epoch += 1
            p, n, *_ = arm.fk(self.q, BASE)
            candidates = recovery_candidates(mode, p.tolist(), n.tolist(), self.liquid.ladle)
            program = {'revision': 'preset-' + mode, 'code': '# Checked system preset: ' + mode,
                       'actions': [], 'preset': mode, 'candidates': candidates}
            job_id = self.start('preset:'+key, program=program, origin='recovery')
            self.preset_requests[key] = (fingerprint, job_id)
            return job_id

    def plan(self, action, start):
        if np.asarray(start).shape != (5,) or not np.isfinite(start).all() or np.any(start < arm.LOW) or np.any(start > arm.HIGH):
            raise MotionError('INVALID_START_STATE')
        if clearance(start)[0] < 0:
            raise MotionError('INITIAL_COLLISION')
        if action.get('kind') in ('wait', 'scoop'):
            return [start.copy() for _ in range(math.ceil(action['duration_s'] / DT))], clearance(start)[0]
        if action.get('kind') == 'move_joints':
            goal = np.asarray(action['q_rad'], dtype=float)
            if goal.shape != (5,) or not np.isfinite(goal).all() or np.any(goal < arm.LOW) or np.any(goal > arm.HIGH):
                raise MotionError('LIMIT_VIOLATION')
            count = math.ceil(action['duration_s'] / DT)
            path, gap = [], float('inf')
            for index in range(1, count + 1):
                u = index / count
                q = start + (goal-start)*(10*u**3-15*u**4+6*u**5)
                gap = min(gap, clearance(q)[0])
                if gap < 0:
                    raise MotionError('COLLISION')
                path.append(q)
            samples = np.array([start] + path)
            velocity = np.diff(samples, axis=0) / DT
            if np.max(np.abs(velocity)) > np.deg2rad(60) or np.max(np.abs(np.diff(velocity, axis=0)/DT), initial=0) > np.deg2rad(150):
                raise MotionError('LIMIT_VIOLATION')
            return path, gap
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

    def _run(self, epoch, program=None):
        try:
            prepared = None
            if program and program.get('preset'):
                with self.changed:
                    initial_q = self.q.copy()
                failures = []
                for candidate in program['candidates']:
                    try:
                        paths, q = [], initial_q.copy()
                        for item in candidate:
                            with self.changed:
                                if epoch != self.epoch:
                                    return
                            path, gap = self.plan(item, q)
                            q = path[-1]
                            paths.append((path, gap))
                        prepared = paths
                        program['actions'] = candidate
                        break
                    except MotionError as error:
                        failures.append(str(error))
                if prepared is None:
                    raise MotionError('RECOVERY_PATH_UNAVAILABLE', {'candidates': failures})
                with self.changed:
                    if epoch != self.epoch:
                        return
                    if not np.allclose(initial_q, self.q, atol=1e-12):
                        raise MotionError('START_STATE_CHANGED')
                    self.code_source = code_view(program['actions'])
                    self.code_hash = hashlib.sha256(self.code_source.encode()).hexdigest()
                    self.job.update(code_hash=self.code_hash, preplanning=False)
                    self.paused = not self.job['start_after_planning']
            actions = [{**a, 'source_line': index + 3} for index, a in enumerate(program['actions'])] if program else main(Robot())
            for index, action in enumerate(actions):
                with self.changed:
                    if epoch != self.epoch:
                        return
                    start = self.q.copy()
                    self.status = 'planning'
                    self.job['status'] = 'planning'
                    self.action = {**action, 'complete': False}
                    self.publish()
                path, gap = prepared[index] if prepared is not None else self.plan(action, start)
                with self.changed:
                    if epoch != self.epoch:
                        return
                    if not np.allclose(start, self.q, atol=1e-12):
                        raise MotionError('START_STATE_CHANGED')
                    self.queue.extend(path)
                    self.target_q = path[-1].copy()
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
                    if program and program.get('preset') == 'dock':
                        p, n, *_ = arm.fk(self.q, BASE)
                        if np.linalg.norm(p-DOCK) > .001 or n[2] < .99 or self.liquid.ladle > .5e-6:
                            raise MotionError('PARK_CONDITIONS_NOT_MET')
                        self.status = self.job['status'] = 'waiting_operator'
                    else:
                        self.status = self.job['status'] = 'succeeded'
                    self.paused = True
                    self.velocity[:] = 0
                    self.epoch += 1
                    self.event('waiting_operator' if self.status == 'waiting_operator' else 'job_succeeded')
                    self.publish()
        except Exception as error:
            with self.changed:
                if epoch == self.epoch:
                    self.queue.clear()
                    self.status = 'failed'
                    self.job['status'] = 'failed'
                    self.job['preplanning'] = False
                    self.job['error'] = str(error)
                    self.job['error_details'] = getattr(error, 'details', None)
                    self.paused = True
                    self.velocity[:] = 0
                    self.target_q = self.q.copy()
                    self.epoch += 1
                    self.event('job_failed', error=str(error))
                    self.publish()
                    traceback.print_exc()
