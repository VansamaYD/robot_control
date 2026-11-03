"""Numerical correctness and recovery invariants for staged local planning."""
import unittest
from unittest.mock import patch
import numpy as np
from engine import Engine, MotionError, arm, BASE, DT
import ik_solver
from presets import HOME_Q, recovery_candidates
from test_recovery_jog import finish


class SolverTests(unittest.TestCase):
    def test_analytic_jacobian_matches_central_difference(self):
        rng = np.random.default_rng(32)
        for q in rng.uniform(arm.LOW+.05, arm.HIGH-.05, (20, 5)):
            _, jac = ik_solver.feature_jacobian(q, BASE)
            eps = 1e-6
            numeric = np.column_stack([(arm.feature(q+eps*np.eye(5)[j], BASE)-
                                        arm.feature(q-eps*np.eye(5)[j], BASE))/(2*eps) for j in range(5)])
            np.testing.assert_allclose(jac, numeric, atol=2e-8, rtol=2e-6)

    def test_reachable_nearby_targets_within_execution_tolerances(self):
        rng = np.random.default_rng(51)
        for q in rng.uniform(arm.LOW+.1, arm.HIGH-.1, (30, 5)):
            p, n, *_ = arm.fk(q, BASE)
            result, pe, ne, _ = ik_solver.solve(p, n, BASE, q+rng.normal(0, .01, 5))
            self.assertLessEqual(pe, .0002)
            self.assertLessEqual(ne, .002)
            self.assertTrue(np.all(result >= arm.LOW) and np.all(result <= arm.HIGH))

    def test_alternate_branch_cannot_bypass_joint_step_limit(self):
        p, n, *_ = arm.fk(HOME_Q, BASE)
        q, pe, ne, details = ik_solver.solve(p, n, BASE, HOME_Q+.1, max_jump=1e-5)
        # Any numerically good distant answer is still rejected by the caller.
        self.assertGreater(details['joint_step_rad'], 1e-5)
        self.assertEqual(details['attempts'], 5)

    def test_guard_interrupts_inside_solver(self):
        count = 0
        def guard():
            nonlocal count
            count += 1
            if count == 3: raise MotionError('PLANNING_CANCELLED')
        with self.assertRaisesRegex(MotionError, 'PLANNING_CANCELLED'):
            ik_solver.solve([2, 2, 2], [0, 0, 1], BASE, HOME_Q, guard=guard)
        self.assertEqual(count, 3)


class PlanningTests(unittest.TestCase):
    def test_timeout_before_execution_preserves_pose_and_liquid(self):
        engine = Engine(realtime=False)
        q, liquid = engine.q.copy(), engine.liquid.snapshot()
        engine.planning_timeout_s = 0
        engine.start_preset('dock', 'timeout', engine.incarnation)
        result = finish(engine)
        self.assertEqual(result['job']['error'], 'PLANNING_TIMEOUT')
        np.testing.assert_array_equal(engine.q, q)
        self.assertEqual(engine.liquid.snapshot(), liquid)
        self.assertFalse(engine.queue)

    def test_failure_records_every_candidate_stage_and_start_state(self):
        engine = Engine(realtime=False)
        engine.plan = lambda a, q: (_ for _ in ()).throw(MotionError('NO_IK', {'sample': 12}))
        engine.start_preset('dock', 'diagnostics', engine.incarnation)
        job = finish(engine)['job']
        self.assertEqual(job['error'], 'RECOVERY_PATH_UNAVAILABLE')
        self.assertEqual(len(job['error_details']['failures']), 4)
        self.assertEqual(job['error_details']['failures'][0]['details']['sample'], 12)
        self.assertIn('name', job['error_details']['failures'][0])
        self.assertEqual(job['planning_start']['q_rad'], HOME_Q.tolist())

    def test_coupled_alternative_still_obeys_collision_gate(self):
        engine = Engine(realtime=False)
        p, n, *_ = arm.fk(engine.q, BASE)
        routes = recovery_candidates('dock', p, n, 0, engine.q)
        action = routes[2][0]
        with patch('engine.clearance', return_value=(-.001, 'bowl')):
            with self.assertRaisesRegex(MotionError, 'INITIAL_COLLISION'):
                engine.plan(action, engine.q)

    def test_coupled_route_runs_full_drain_and_operator_handoff(self):
        engine = Engine(realtime=False)
        original = engine.plan
        def force_alternative(action, q):
            if action['name'] in ('原位置抬离液面（保持勺姿）', '原位置抬升'):
                raise MotionError('NO_IK')
            return original(action, q)
        engine.plan = force_alternative
        engine.start_preset('dock', 'coupled', engine.incarnation)
        frame = finish(engine)
        self.assertEqual(frame['status'], 'waiting_operator', frame['job'])
        self.assertEqual(frame['job']['planning_progress']['candidate'], 3)
        self.assertTrue(frame['job']['dry_verified'])
        self.assertLessEqual(frame['liquid']['ladle_m3'], 1e-9)
        self.assertEqual(frame['liquid']['spill_m3'], 0)
        self.assertGreater(frame['job']['planning_stats']['ik_points'], 0)

    def test_stale_epoch_interrupts_without_waiting_for_segment_end(self):
        engine = Engine(realtime=False)
        engine.planning_context.epoch = engine.epoch-1
        with self.assertRaisesRegex(MotionError, 'PLANNING_CANCELLED'):
            engine.plan({'kind': 'wait', 'duration_s': 3}, engine.q)

    def test_fallback_is_used_only_after_primary_failure(self):
        p, n, *_ = arm.fk(HOME_Q, BASE)
        _, _, _, details = ik_solver.solve(p, n, BASE, HOME_Q)
        self.assertEqual(details['attempts'], 1)


if __name__ == '__main__':
    unittest.main()
