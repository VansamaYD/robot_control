"""Executable design model only: no robot, persistence, IPC or sandbox.

Single-threaded callers only. Production must add transactions and authentication.
"""
from dataclasses import dataclass, field
import hashlib
import json
import math


class Rejected(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def fingerprint(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError):
        raise Rejected('INVALID_ARGUMENT') from None
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class Session:
    incarnation: str
    configuration: str = 'scene-1'
    paused: bool = True
    tick: int = 0
    state_revision: int = 0
    epoch: int = 0
    owner: str | None = None
    lease_deadline: float | None = None
    active_job: str | None = None
    jobs: dict = field(default_factory=dict)
    idempotency: dict = field(default_factory=dict)
    queue: list = field(default_factory=list)

    def _publish(self):
        self.state_revision += 1

    def acquire(self, principal, now, ttl=1.0):
        if not principal or not math.isfinite(ttl) or ttl <= 0 or not math.isfinite(now):
            raise Rejected('INVALID_ARGUMENT')
        self.expire_manual(now)
        if self.owner is not None:
            raise Rejected('CONTROL_BUSY')
        self.epoch += 1
        self.owner = principal
        self.lease_deadline = now + ttl
        self._publish()
        return self.epoch

    def expire_manual(self, now):
        if self.lease_deadline is not None and now >= self.lease_deadline:
            self.epoch += 1
            self.owner = None
            self.lease_deadline = None
            self.queue.clear()
            self._publish()

    def start(self, principal, key, request, now):
        if not principal or not key:
            raise Rejected('INVALID_ARGUMENT')
        digest = fingerprint(request)
        scope = (principal, self.incarnation, key)
        existing = self.idempotency.get(scope)
        if existing:
            old_digest, job_id = existing
            if digest != old_digest:
                raise Rejected('IDEMPOTENCY_CONFLICT')
            return job_id
        self.expire_manual(now)
        if request.get('configuration') != self.configuration:
            raise Rejected('CONFIGURATION_CHANGED')
        if not request.get('program_revision'):
            raise Rejected('INVALID_ARGUMENT')
        if self.active_job is not None or self.owner not in (None, principal):
            raise Rejected('CONTROL_BUSY')
        # Production: transaction for owner, job, and idempotency binding.
        self.epoch += 1
        self.owner = principal
        self.lease_deadline = None  # service-managed autonomous job
        job_id = f'job-{len(self.jobs) + 1}'
        self.jobs[job_id] = {'principal': principal, 'status': 'running', 'seq': 0,
                             'epoch': self.epoch, 'configuration': self.configuration}
        self.active_job = job_id
        self.idempotency[scope] = (digest, job_id)
        self.paused = False
        self._publish()
        return job_id

    def submit(self, incarnation, epoch, job_id, seq, action):
        job = self.jobs.get(job_id)
        if (incarnation != self.incarnation or epoch != self.epoch
                or job_id != self.active_job or not job
                or job['epoch'] != epoch):
            raise Rejected('STALE_CAPABILITY')
        if job['status'] != 'running' or self.paused:
            raise Rejected('NOT_RUNNING')
        if seq != job['seq'] + 1:
            raise Rejected('ACTION_SEQUENCE_CONFLICT')
        if self.queue:
            raise Rejected('QUEUE_FULL')
        fingerprint(action)  # only finite JSON, not actual action/path validation
        self.queue.append(action)
        job['seq'] = seq

    def pause(self):
        self.paused = True
        if self.active_job:
            self.jobs[self.active_job]['status'] = 'paused'
        self._publish()

    def advance_tick(self, single_step=False):
        if self.paused and not single_step:
            return False
        if single_step and not self.paused:
            raise Rejected('NOT_PAUSED')
        self.tick += 1
        self._publish()
        return True

    def cancel(self, principal, job_id):
        job = self.jobs.get(job_id)
        if job is None or job['principal'] != principal:
            raise Rejected('NOT_AUTHORIZED')
        if job['status'] == 'cancelled':
            return  # repeated cancellation cannot revoke a later job
        if self.active_job != job_id:
            raise Rejected('JOB_TERMINAL')
        self.epoch += 1
        job['status'] = 'cancelled'
        self.active_job = None
        self.owner = None
        self.queue.clear()
        self._publish()

    def edit_configuration(self, expected, replacement):
        if not self.paused or self.active_job is not None:
            raise Rejected('EDIT_REQUIRES_IDLE_PAUSE')
        if expected != self.configuration:
            raise Rejected('CONFIGURATION_CHANGED')
        if not replacement or replacement == expected:
            raise Rejected('INVALID_ARGUMENT')
        self.configuration = replacement
        self._publish()

    def snapshot_ref(self):
        return self.incarnation, self.state_revision
