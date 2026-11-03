"""Tests of the spike's real state/IK path, not production acceptance."""
import threading
import time
import unittest
import numpy as np
from engine import Engine, Robot, MotionError, arm, BASE, DT


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.initial = Engine(realtime=False)
        cls.action = Robot().move_tcp('lift', [0, -.1, .195], [0, 0, 1], 12.2)
        cls.path, cls.gap = cls.initial.plan(cls.action, cls.initial.q)

    def test_canonical_urdf_and_final_fk_agree(self):
        end, normal, *_ = arm.fk(self.path[-1], BASE)
        np.testing.assert_allclose(end, self.action['p'], atol=.0002)
        np.testing.assert_allclose(normal, [0, 0, 1], atol=.002)
        self.assertEqual(len(self.path), 610)

    def test_all_samples_obey_limits_and_coarse_screen(self):
        samples = np.array([self.initial.q] + self.path)
        self.assertTrue(np.all(samples >= arm.LOW) and np.all(samples <= arm.HIGH))
        self.assertGreaterEqual(self.gap, 0)
        self.assertLess(np.max(np.abs(np.diff(samples, axis=0) / DT)), np.deg2rad(60))

    def test_unreachable_request_does_not_mutate_state(self):
        engine = Engine(realtime=False)
        initial = engine.snapshot()
        with self.assertRaisesRegex(MotionError, 'NO_IK'):
            engine.plan(Robot().move_tcp('bad', [10, 0, 0], [0, 0, 1], 2), engine.q)
        self.assertEqual(engine.snapshot(), initial)

    def test_nonfinite_and_zero_normal_rejected(self):
        for target, normal in [([float('nan'), 0, .1], [0, 0, 1]), ([0, 0, .1], [0, 0, 0])]:
            with self.assertRaisesRegex(MotionError, 'INVALID_ARGUMENT'):
                self.initial.plan(Robot().move_tcp('bad', target, normal, 2), self.initial.q)

    def test_screenshot_input_is_immutable_copy(self):
        engine = Engine(realtime=False)
        frame = engine.snapshot()
        frame['q_rad'][0] = 999
        self.assertNotEqual(engine.snapshot()['q_rad'][0], 999)

    def test_render_frames_do_not_advance_simulation(self):
        engine = Engine(realtime=False)
        engine.paused = False
        engine.action = {'name': 'test', 'complete': False}
        engine.queue.extend(self.path[:10])
        for _ in range(3):
            engine.advance()
        original = engine.snapshot()
        for _ in range(100):
            engine.snapshot()
        self.assertEqual(engine.snapshot(), original)

    def test_cancel_invalidates_plan_finishing_late(self):
        engine = Engine(realtime=False)
        started, release = threading.Event(), threading.Event()
        def delayed_plan(action, q):
            started.set()
            release.wait(3)
            return self.path[:3], self.gap
        engine.plan = delayed_plan
        job = engine.start('race')
        self.assertTrue(started.wait(2))
        engine.control('cancel')
        frozen = engine.snapshot()
        release.set()
        time.sleep(.1)
        self.assertEqual(engine.snapshot(), frozen)
        self.assertFalse(engine.queue)
        self.assertEqual(engine.start('race'), job)

    def test_pause_and_single_step_do_not_skip_path_samples(self):
        engine = Engine(realtime=False)
        engine.action = {'name': 'test', 'complete': False}
        engine.queue.extend(self.path[:3])
        self.assertFalse(engine.advance())
        engine.control('step_tick')
        self.assertEqual(engine.tick, 1)
        np.testing.assert_array_equal(engine.q, self.path[0])
        self.assertTrue(engine.paused)

    def test_empty_scoop_fails_job_without_success_event(self):
        engine = Engine(realtime=False)
        engine.liquid.initial = engine.liquid.pot = 0.
        engine.start('empty-pot')
        deadline = time.monotonic() + 2
        while not engine.queue and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(engine.queue)
        while engine.queue:
            engine.advance()
        self.assertEqual(engine.snapshot()['job']['status'], 'failed')
        self.assertEqual(engine.snapshot()['job']['error'], 'LIQUID_UNAVAILABLE')
        self.assertFalse(any(event['event'] == 'action_completed' for event in engine.events))

    def test_fixed_tick_result_independent_of_observer_frequency(self):
        slow, fast = Engine(realtime=False), Engine(realtime=False)
        for engine in [slow, fast]:
            engine.paused = False
            engine.action = {'name': 'test', 'complete': False}
            engine.queue.extend(self.path[:20])
        for index in range(20):
            slow.advance()
            if index % 5 == 0:
                slow.snapshot()
            fast.advance()
            for _ in range(10):
                fast.snapshot()
        np.testing.assert_array_equal(slow.q, fast.q)
        self.assertEqual(slow.tick, fast.tick)


if __name__ == '__main__':
    unittest.main()
