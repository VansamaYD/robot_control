import math
import time
import unittest
import numpy as np
from collision_model import clearance
from trajectory_sim import clearance as legacy_clearance
from engine import Engine, MotionError, arm
from liquid import Liquid
from presets import HOME_Q, PICK, recovery_candidates
from projects import code_view


class NaturalLiquidTests(unittest.TestCase):
    def test_immersed_open_mouth_fills_without_scoop(self):
        liquid = Liquid()
        for _ in range(30): liquid.advance(.02, PICK, [0, 0, 1])
        self.assertAlmostEqual(liquid.ladle, liquid.capacity, places=12)
        self.assertAlmostEqual(liquid.total(), liquid.initial, places=12)

    def test_body_underwater_but_rim_dry_does_not_fill_through_metal(self):
        liquid = Liquid()
        for _ in range(30): liquid.advance(.02, [0, -.1, .09], [0, 0, 1])
        self.assertEqual(liquid.ladle, 0)

    def test_partial_rim_immersion_naturally_fills_tilted_cavity(self):
        liquid = Liquid(); normal = [math.sqrt(.5), 0, math.sqrt(.5)]
        for _ in range(50): liquid.advance(.02, [0, -.1, .09], normal)
        self.assertGreater(liquid.ladle, 0)
        self.assertLess(liquid.ladle, liquid.capacity)
        self.assertEqual(liquid.spill, 0)

    def test_scoop_target_is_not_a_pump_or_a_volume_cap(self):
        liquid = Liquid()
        for _ in range(30): liquid.advance(.02, PICK, [0, 0, 1], 5e-6)
        self.assertGreater(liquid.ladle, 30e-6)

    def test_world_can_advance_liquid_while_robot_has_no_job(self):
        engine = Engine(realtime=False)
        q = engine.q.copy()
        engine.control('resume_world')
        for _ in range(30): engine.advance()
        self.assertGreater(engine.liquid.ladle, 30e-6)
        np.testing.assert_array_equal(engine.q, q)
        engine.control('pause_world'); before = engine.liquid.snapshot()
        for _ in range(10): engine.advance()
        self.assertEqual(engine.liquid.snapshot(), before)

    def test_liquid_only_frame_changes_do_not_invalidate_manual_pose(self):
        engine = Engine(realtime=False)
        revision = engine.rev
        engine.control('resume_world')
        for _ in range(10): engine.advance()
        job = engine.manual('shoulder_pan', 1, 'liquid-only', revision, engine.incarnation)
        self.assertTrue(job)
        engine.control('cancel', job)


class DockingSafetyTests(unittest.TestCase):
    def test_empty_at_request_still_has_unconditional_drain_and_checkpoint(self):
        for candidate in recovery_candidates('dock', PICK, [0, 0, 1], 0., HOME_Q):
            kinds = [action['kind'] for action in candidate]
            checkpoint = kinds.index('verify_dry')
            self.assertTrue(any(action['name'] == '锅内倾倒至85°' for action in candidate[:checkpoint]))
            self.assertTrue(all(action.get('requires_dry') for action in candidate[checkpoint+1:]))
            self.assertFalse(any(action['kind'] == 'move_joints' for action in candidate))
            self.assertFalse(any(action.get('p') == PICK for action in candidate))

    def test_refilled_ladle_cannot_execute_exit_segment(self):
        engine = Engine(realtime=False)
        actions = [{'kind': 'wait', 'name': 'exit guard', 'duration_s': .1}]
        job = engine.start('guard', program={'revision': 'test', 'actions': actions, 'code': code_view(actions)})
        deadline = time.monotonic()+2
        while not engine.queue and time.monotonic()<deadline: time.sleep(.002)
        engine.action['requires_dry'] = True; engine.job['dry_verified'] = True
        engine.liquid.pot -= 30e-6; engine.liquid.ladle = 30e-6
        before = engine.q.copy()
        self.assertFalse(engine.advance())
        self.assertEqual(engine.jobs[job]['error'], 'DOCK_DRY_INTERLOCK')
        np.testing.assert_array_equal(before, engine.q)

    def test_already_home_does_not_generate_ten_second_zero_motion(self):
        candidate = recovery_candidates('home', PICK, [0, 0, 1], 0, HOME_Q)[0]
        self.assertEqual(len(candidate), 1)
        self.assertEqual(candidate[0]['kind'], 'wait')
        self.assertLess(candidate[0]['duration_s'], 1)

    def test_batched_collision_matches_legacy_sampled_envelopes(self):
        random = np.random.default_rng(215)
        for q in [HOME_Q] + list(random.uniform(arm.LOW, arm.HIGH, size=(20, 5))):
            old, new = legacy_clearance(q), clearance(q)
            self.assertAlmostEqual(old[0], new[0], delta=1e-9)
            np.testing.assert_allclose(old[2], new[2], atol=1e-12)


if __name__ == '__main__': unittest.main()
