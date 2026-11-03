import time
import unittest
import numpy as np
from engine import Engine, MotionError, arm
from projects import code_view, validate


def program(duration=.2):
    actions = [{'kind': 'wait', 'name': 'wait', 'duration_s': duration}]
    return {'revision': 'a'*64, 'actions': actions, 'code': code_view(actions)}


class ControlFeaturesTests(unittest.TestCase):
    def wait_path(self, engine):
        deadline = time.monotonic()+3
        while not engine.queue and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(engine.queue)

    def test_joint_goal_and_velocity_fields_have_model_order(self):
        state = Engine(realtime=False).snapshot()
        self.assertEqual(len(state['target_q_rad']), 5)
        self.assertEqual(len(state['velocity_rad_s']), 5)
        self.assertEqual(len(state['joint_limits_rad']), 5)
        np.testing.assert_array_equal(state['target_q_rad'], state['q_rad'])

    def test_manual_finite_move_and_retry(self):
        engine = Engine(realtime=False)
        revision, initial = engine.rev, engine.q.copy()
        job = engine.manual('shoulder_pan', 1, 'manual-test', revision)
        self.assertEqual(engine.manual('shoulder_pan', 1, 'manual-test', revision), job)
        self.wait_path(engine)
        while engine.queue:
            engine.advance()
        self.assertAlmostEqual(engine.q[0], initial[0]+np.deg2rad(1), places=10)
        self.assertEqual(engine.jobs[job]['origin'], 'manual')

    def test_manual_stale_start_rejected(self):
        engine = Engine(realtime=False)
        with self.assertRaisesRegex(MotionError, 'START_STATE_CHANGED'):
            engine.manual('shoulder_pan', 1, 'stale', engine.rev-1)

    def test_joint_goal_limit_rejected_without_changing_pose(self):
        engine = Engine(realtime=False)
        q = engine.q.copy()
        goal = q.copy(); goal[0] = arm.HIGH[0]+1
        with self.assertRaisesRegex(MotionError, 'LIMIT_VIOLATION'):
            engine.plan({'kind': 'move_joints', 'q_rad': goal.tolist(), 'duration_s': 1}, q)
        np.testing.assert_array_equal(q, engine.q)

    def test_cross_owner_cancel_denied(self):
        engine = Engine(realtime=False)
        job = engine.start('key', program=program(), principal='mcp:a', origin='mcp')
        with self.assertRaisesRegex(MotionError, 'NOT_AUTHORIZED'):
            engine.control('cancel', job, principal='mcp:b')
        engine.control('cancel', job, principal='mcp:a')

    def test_cancel_old_job_cannot_cancel_new_one(self):
        engine = Engine(realtime=False)
        old = engine.start('old', program=program(), principal='mcp:a', origin='mcp')
        engine.control('cancel', old, principal='mcp:a')
        new = engine.start('new', program=program(), principal='mcp:b', origin='mcp')
        epoch = engine.epoch
        engine.control('cancel', old, principal='mcp:a')
        self.assertEqual(engine.epoch, epoch)
        self.assertEqual(engine.job['job_id'], new)
        self.assertEqual(engine.get_job(old)['status'], 'cancelled')
        engine.control('cancel', new, principal='mcp:b')

    def test_idempotency_is_namespaced_by_principal(self):
        engine = Engine(realtime=False)
        first = engine.start('same', program=program(), principal='mcp:a')
        engine.control('cancel', first, principal='mcp:a')
        second = engine.start('same', program=program(), principal='mcp:b')
        self.assertNotEqual(first, second)
        self.assertEqual(engine.start('same', program=program(), principal='mcp:a'), first)
        engine.control('cancel', second, principal='mcp:b')

    def test_invalid_joint_vector_schema_rejected(self):
        with self.assertRaisesRegex(ValueError, 'INVALID_JOINT_VECTOR'):
            validate([{'kind': 'move_joints', 'name': 'bad', 'duration_s': 1, 'q_rad': [0]*6}])

    def test_new_service_jobs_do_not_reuse_old_ids(self):
        first, second = Engine(realtime=False), Engine(realtime=False)
        old = first.start('key', program=program())
        new = second.start('key', program=program())
        self.assertNotEqual(old, new)
        epoch = second.epoch
        with self.assertRaisesRegex(MotionError, 'JOB_NOT_FOUND'):
            second.control('cancel', old)
        self.assertEqual(second.epoch, epoch)
        first.control('cancel', old)
        second.control('cancel', new)

    def test_wait_does_not_bypass_invalid_start_limits(self):
        engine = Engine(realtime=False)
        start = engine.q.copy(); start[0] = arm.HIGH[0]+1
        with self.assertRaisesRegex(MotionError, 'INVALID_START_STATE'):
            engine.plan({'kind': 'wait', 'duration_s': .2}, start)


if __name__ == '__main__':
    unittest.main()
