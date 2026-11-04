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
from trajectory_sim import BASE
from collision_model import clearance
from flow import main
from liquid import Liquid
from scene_geometry import SCENE, validate_scene
from projects import code_view
from errors import MotionError
import ik_solver
import detour
from jog_control import JogMixin
from presets import HOME_Q, DOCK, DRY_LIMIT_M3, recovery_candidates

DT = .02
NAMES = [entry[0] for entry in arm.JOINTS]


def vector(node, name, default='0 0 0'):
    return [float(v) for v in (node.attrib.get(name, default) if node is not None else default).split()]


def load_model():
    try:
        validate_scene()
    except ValueError as error:
        raise MotionError(str(error)) from error
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
        self.planning_context = threading.local()
        self.planning_timeout_s = 30.
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
        frame['world_phase'] = 'paused' if self.paused else 'motion' if self.queue or self.jog else 'fluid_only'
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
                if not self.paused:
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
            if self.job and self.job.get('preplanning'):
                return False
            moving = bool(self.queue)
            if moving and self.action.get('requires_dry'):
                candidate_p, candidate_n, *_ = arm.fk(self.queue[0], BASE)
                if (not self.job.get('dry_verified') or self.liquid.ladle > DRY_LIMIT_M3 or
                        self.liquid.geometry.connected_volume(candidate_p, candidate_n, self.liquid.height) is not None):
                    self._fail_action('DOCK_DRY_INTERLOCK', {'ladle_m3': self.liquid.ladle})
                    return False
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
                if self.action.get('kind') == 'verify_dry':
                    air = sum(packet['v'] for packet in self.liquid.packets)
                    if self.liquid.ladle > DRY_LIMIT_M3 or air > DRY_LIMIT_M3:
                        self._fail_action('DRAIN_INCOMPLETE', {'ladle_m3': self.liquid.ladle, 'air_m3': air})
                        return False
                    self.job['dry_verified'] = True
                    self.job['dry_check'] = {'tick': self.tick, 'tcp_m': p.tolist(), 'ladle_m3': self.liquid.ladle, 'air_m3': air}
                    self.event('drain_verified', **self.job['dry_check'])
                    self.event('action_completed', name=self.action['name'])
                elif self.action.get('kind') == 'scoop' and self.liquid.ladle < self.action['target_m3'] - 1e-9:
                    self._fail_action('LIQUID_UNAVAILABLE', self.liquid.scoop_diagnostics(p, n, self.action['target_m3']))
                    return False
                else:
                    self.event('action_completed', name=self.action['name'])
            self.publish()
            self.changed.notify_all()
            return True

    def _fail_action(self, code, details=None):
        self.epoch += 1
        self.queue.clear()
        self.status = 'failed'
        self.job.update(status='failed', error=code, error_details=details, preplanning=False)
        self.paused = True
        self.velocity[:] = 0
        self.target_q = self.q.copy()
        self.event('action_failed', name=self.action['name'] if self.action else None, error=code)
        self.publish()
        self.changed.notify_all()

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
            if command in ('pause_world', 'resume_world'):
                if principal != 'ui' or self.job and self.job['status'] in ('planning', 'running', 'waiting_operator'):
                    raise MotionError('WORLD_CONTROL_NOT_AVAILABLE')
                self.paused = command == 'pause_world'
                self.velocity[:] = 0
            elif command == 'pause':
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

    def manual(self, joint_name, delta_deg, key, expected_revision, incarnation=None):
        with self.changed:
            request = json.dumps([joint_name, delta_deg, expected_revision, incarnation], allow_nan=False)
            if key in self.manual_requests:
                previous, job_id = self.manual_requests[key]
                if request != previous:
                    raise MotionError('IDEMPOTENCY_CONFLICT')
                return job_id
            if joint_name not in NAMES or isinstance(delta_deg, bool) or not isinstance(delta_deg, (int, float)) or not math.isfinite(delta_deg) or not 0 < abs(delta_deg) <= 5:
                raise MotionError('INVALID_ARGUMENT')
            if incarnation is not None and incarnation != self.incarnation:
                raise MotionError('SESSION_CHANGED')
            frame = self.frames.get(expected_revision) if type(expected_revision) is int else None
            if not frame or frame['incarnation'] != self.incarnation or frame['configuration_revision'] != self.config or not np.allclose(frame['q_rad'], self.q, atol=1e-10, rtol=0):
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
                self.job.update(preplanning=True, start_after_planning=True, preset=program['preset'], dry_verified=False)
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
            candidates = recovery_candidates(mode, p.tolist(), n.tolist(), self.liquid.ladle, self.q)
            program = {'revision': 'preset-' + mode, 'code': '# Checked system preset: ' + mode,
                       'actions': [], 'preset': mode, 'candidates': candidates}
            job_id = self.start('preset:'+key, program=program, origin='recovery')
            self.preset_requests[key] = (fingerprint, job_id)
            return job_id

    def _planning_checkpoint(self):
        context = self.planning_context
        if getattr(context, 'epoch', self.epoch) != self.epoch:
            raise MotionError('PLANNING_CANCELLED')
        if time.perf_counter() > getattr(context, 'deadline', float('inf')):
            raise MotionError('PLANNING_TIMEOUT', {'budget_s': self.planning_timeout_s})

    def plan(self, action, start):
        self._planning_checkpoint()
        if np.asarray(start).shape != (5,) or not np.isfinite(start).all() or np.any(start < arm.LOW) or np.any(start > arm.HIGH):
            raise MotionError('INVALID_START_STATE')
        if clearance(start)[0] < 0:
            raise MotionError('INITIAL_COLLISION')
        if action.get('kind') in ('wait', 'scoop', 'verify_dry'):
            return [start.copy() for _ in range(math.ceil(action['duration_s'] / DT))], clearance(start)[0]
        if action.get('kind') == 'move_joints':
            goal = np.asarray(action['q_rad'], dtype=float)
            if goal.shape != (5,) or not np.isfinite(goal).all() or np.any(goal < arm.LOW) or np.any(goal > arm.HIGH):
                raise MotionError('LIMIT_VIOLATION')
            count = math.ceil(action['duration_s'] / DT)
            path, gap = [], float('inf')
            for index in range(1, count + 1):
                self._planning_checkpoint()
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
        fallback_points = 0
        for index in range(1, count + 1):
            self._planning_checkpoint()
            u = index / count
            u = 10*u**3 - 15*u**4 + 6*u**5
            p = p0 + (target - p0) * u
            n = n0 + (normal - n0) * u
            if np.linalg.norm(n) < 1e-6:
                raise MotionError('UNSUPPORTED_NORMAL_PATH')
            n /= np.linalg.norm(n)
            previous_q = q.copy()
            q, pe, ne, diagnostics = ik_solver.solve(p, n, BASE, previous_q,
                guard=self._planning_checkpoint, fallback=fallback_points < 8,
                max_jump=np.deg2rad(60)*DT)
            if diagnostics['attempts'] > 1:
                fallback_points += 1
            stats = getattr(self.planning_context, 'stats', None)
            if stats is not None:
                stats['ik_points'] += 1
                stats['fallback_points'] += int(diagnostics['attempts'] > 1)
                stats['ik_iterations'] += diagnostics['iterations']
            failure = {**diagnostics, 'sample': index, 'samples': count,
                       'progress': index/count, 'target_m': p.tolist(), 'normal': n.tolist()}
            if pe > .0002 or ne > .002:
                raise MotionError('NO_IK', failure)
            if diagnostics['joint_step_rad'] > np.deg2rad(60)*DT+1e-12:
                raise MotionError('JOINT_SPEED_LIMIT', {**failure,
                    'joint_name': NAMES[int(np.argmax(np.abs(q-previous_q)))],
                    'speed_deg_s': float(np.rad2deg(diagnostics['joint_step_rad']/DT)),
                    'limit_deg_s': 60., 'duration_s': duration})
            gap, obstacle, *_ = clearance(q)
            minimum_gap = min(gap, minimum_gap)
            if gap < 0:
                raise MotionError('COLLISION', {'sample': index, 'samples': count,
                    'clearance_m': gap, 'part': obstacle})
            result.append(q.copy())
        samples = np.array([start] + result)
        velocity = np.diff(samples, axis=0) / DT
        accel = np.diff(velocity, axis=0) / DT
        if np.max(np.abs(velocity)) > np.deg2rad(60) or np.max(np.abs(accel), initial=0) > np.deg2rad(150):
            raise MotionError('LIMIT_VIOLATION')
        return result, minimum_gap

    def _plan_dry_detour(self, action, start, liquid, dry_verified):
        """Only system docking after the dry checkpoint may change path shape."""
        if (not dry_verified or not action.get('requires_dry') or
                liquid.ladle > DRY_LIMIT_M3 or sum(p['v'] for p in liquid.packets) > DRY_LIMIT_M3):
            raise MotionError('DOCK_DRY_INTERLOCK')
        deadline = time.perf_counter()+6.
        def guard():
            self._planning_checkpoint()
            if time.perf_counter() > deadline:
                raise MotionError('DETOUR_SEARCH_TIMEOUT', {'budget_s': 6.})
        def valid(q):
            guard()
            if clearance(q)[0] < 0:
                return False
            p, n, *_ = arm.fk(q, BASE)
            return liquid.geometry.connected_volume(p, n, liquid.height) is None
        def progress(stats):
            with self.changed:
                self._planning_checkpoint()
                self.job['detour_progress'] = stats
                self.publish()
        target, normal = np.asarray(action['p']), np.asarray(action['n'])
        normal = normal/np.linalg.norm(normal)
        # Exact endpoint still respects the original position and bowl direction.
        rng = np.random.default_rng(1701)
        seeds = [start, HOME_Q] + list(rng.uniform(arm.LOW+.02, arm.HIGH-.02, (4, 5)))
        goals = []
        for seed in seeds:
            guard()
            q, pe, ne, _ = ik_solver.solve(target, normal, BASE, seed, guard=guard, fallback=False)
            if pe <= .0002 and ne <= .002 and valid(q):
                if not any(np.linalg.norm(q-g) < .01 for g in goals): goals.append(q)
        if not goals:
            raise MotionError('DETOUR_GOAL_UNREACHABLE')
        last_error = None
        for goal in sorted(goals, key=lambda q: np.linalg.norm(q-start)):
            try:
                points, stats = detour.search(start, goal, arm.LOW+1e-6, arm.HIGH-1e-6,
                    valid, guard=guard, progress=progress)
                break
            except MotionError as error:
                if str(error) != 'DETOUR_NOT_FOUND': raise
                last_error = error
        else:
            raise last_error
        result = []
        q = start.copy()
        for index, target_q in enumerate(points[1:]):
            guard()
            distance = float(np.max(np.abs(target_q-q)))
            # Quintic interpolation: peak coefficients 1.875 and 5.774.
            duration = max(1.2, 1.875*distance/np.deg2rad(20),
                           math.sqrt(5.774*distance/np.deg2rad(40)))
            item = {'kind': 'move_joints', 'name': '绕行%d：%s' % (index+1, action['name']),
                    'q_rad': target_q.tolist(), 'duration_s': math.ceil(duration/DT)*DT,
                    'requires_dry': True}
            path, gap = self.plan(item, q)
            result.append((item, path, gap))
            q = path[-1]
        with self.changed:
            self._planning_checkpoint()
            self.job.setdefault('detours', []).append({**stats, 'action': action['name'],
                'joint_waypoints': [q.tolist() for q in points], 'validated': False,
                'duration_s': sum(item['duration_s'] for item, _, _ in result)})
            self.publish()
        return result

    def _run(self, epoch, program=None):
        try:
            self.planning_context.epoch = epoch
            self.planning_context.stats = {'ik_points': 0, 'fallback_points': 0, 'ik_iterations': 0}
            self.planning_context.deadline = time.perf_counter()+self.planning_timeout_s
            prepared = None
            if program and program.get('preset'):
                planning_started = time.perf_counter()
                with self.changed:
                    initial_q = self.q.copy()
                    initial_liquid = deepcopy(self.liquid)
                    initial_config = self.config
                    initial_incarnation = self.incarnation
                    self.job['planning_start'] = {'q_rad': initial_q.tolist(),
                        'liquid': initial_liquid.snapshot(), 'configuration': initial_config,
                        'incarnation': initial_incarnation}
                failures, failure_details = [], []
                # Append search retries only for fixed routes blocked AFTER drainage.
                attempts = [(i, False, candidate) for i, candidate in enumerate(program['candidates'])]
                for candidate_index, allow_search, candidate in attempts:
                    try:
                        paths, expanded, q = [], [], initial_q.copy()
                        detour_start = len(self.job.get('detours', []))
                        predicted = deepcopy(initial_liquid)
                        dry_verified = False
                        for segment_index, item in enumerate(candidate):
                            with self.changed:
                                if epoch != self.epoch:
                                    return
                                self.job['planning_progress'] = {'candidate': candidate_index+1, 'segment': segment_index+1,
                                    'segments': len(candidate), 'name': item['name'], 'stage': 'detour' if allow_search else 'fixed',
                                    'elapsed_s': time.perf_counter()-planning_started}
                                self.publish()
                            try:
                                path, gap = self.plan(item, q)
                                segments = [(item, path, gap)]
                            except MotionError as error:
                                if (allow_search and program['preset'] == 'dock' and dry_verified
                                        and item.get('requires_dry') and item['kind'] == 'move_tcp'
                                        and str(error) in ('NO_IK', 'COLLISION')):
                                    segments = self._plan_dry_detour(item, q, predicted, dry_verified)
                                else:
                                    raise
                            for execution_item, path, gap in segments:
                                for sample in path:
                                    self._planning_checkpoint()
                                    p, n, *_ = arm.fk(sample, BASE)
                                    if execution_item.get('requires_dry') and (not dry_verified or predicted.ladle > DRY_LIMIT_M3 or
                                            predicted.geometry.connected_volume(p, n, predicted.height) is not None):
                                        raise MotionError('DOCK_DRY_INTERLOCK')
                                    predicted.advance(DT, p, n)
                                    if predicted.spill-initial_liquid.spill > DRY_LIMIT_M3:
                                        raise MotionError('PREDICTED_SPILL')
                                if execution_item['kind'] == 'verify_dry':
                                    if predicted.ladle > DRY_LIMIT_M3 or sum(p['v'] for p in predicted.packets) > DRY_LIMIT_M3:
                                        raise MotionError('DRAIN_INCOMPLETE')
                                    dry_verified = True
                                q = path[-1]
                                paths.append((path, gap))
                                expanded.append(execution_item)
                        prepared = paths
                        with self.changed:
                            self._planning_checkpoint()
                            for record in self.job.get('detours', [])[detour_start:]:
                                record['validated'] = True
                        program['actions'] = expanded
                        break
                    except MotionError as error:
                        if str(error) in ('PLANNING_CANCELLED', 'PLANNING_TIMEOUT'):
                            raise
                        if (not allow_search and program['preset'] == 'dock' and dry_verified
                                and item.get('requires_dry') and item['kind'] == 'move_tcp'
                                and str(error) in ('NO_IK', 'COLLISION')):
                            attempts.append((candidate_index, True, candidate))
                        failures.append(str(error))
                        failure_details.append({'candidate': candidate_index+1, 'segment': segment_index+1,
                            'name': item['name'], 'stage': 'detour' if allow_search else 'fixed',
                            'code': str(error), 'details': error.details})
                        with self.changed:
                            if epoch != self.epoch:
                                return
                            self.job['planning_failures'] = deepcopy(failure_details)
                if prepared is None:
                    raise MotionError('RECOVERY_PATH_UNAVAILABLE', {'candidates': failures, 'failures': failure_details})
                with self.changed:
                    if epoch != self.epoch:
                        return
                    if (not np.allclose(initial_q, self.q, atol=1e-12) or self.config != initial_config
                            or self.incarnation != initial_incarnation
                            or self.liquid.snapshot() != initial_liquid.snapshot()):
                        raise MotionError('START_STATE_CHANGED')
                    self.code_source = code_view(program['actions'])
                    self.code_hash = hashlib.sha256(self.code_source.encode()).hexdigest()
                    self.job.update(code_hash=self.code_hash, preplanning=False,
                                    planning_wall_s=time.perf_counter()-planning_started,
                                    planned_duration_s=sum(len(path)*DT for path, gap in prepared),
                                    predicted_spill_increment_m3=predicted.spill-initial_liquid.spill,
                                    planned_samples=sum(len(path) for path, gap in prepared),
                                    planning_stats=dict(self.planning_context.stats))
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
                if prepared is None:
                    self.planning_context.deadline = time.perf_counter()+self.planning_timeout_s
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
                        if np.linalg.norm(p-DOCK) > .001 or n[2] < .99 or not self.job.get('dry_verified') or self.liquid.ladle > DRY_LIMIT_M3:
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
