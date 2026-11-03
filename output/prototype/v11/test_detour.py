"""Search must avoid obstacles and cannot bypass dry/fluid execution checks."""
import unittest
import json
import math
from pathlib import Path
from unittest.mock import patch
import numpy as np
from errors import MotionError
import detour
from engine import Engine, arm, BASE
from presets import LIFT, UP
from test_recovery_jog import finish


class SearchTests(unittest.TestCase):
    def test_blocked_straight_line_routes_around_obstacle(self):
        def valid(q): return not (-.3 < q[0] < .3 and -.6 < q[1] < .6)
        start, goal = np.array([-.9, 0]), np.array([.9, 0])
        points, stats = detour.search(start, goal, [-1,-1], [1,1], valid)
        self.assertGreater(len(points), 2)
        np.testing.assert_array_equal(points[0], start)
        np.testing.assert_array_equal(points[-1], goal)
        for a, b in zip(points, points[1:]):
            for t in np.linspace(0,1,100): self.assertTrue(valid(a+(b-a)*t))
        self.assertLessEqual(stats['nodes'], 500)

    def test_separating_wall_has_bounded_failure(self):
        with self.assertRaisesRegex(MotionError, 'DETOUR_NOT_FOUND'):
            detour.search([-.9,0], [.9,0], [-1,-1], [1,1],
                          lambda q: abs(q[0]) > .2, max_nodes=50)

    def test_search_cancellation_propagates(self):
        def cancel(): raise MotionError('PLANNING_CANCELLED')
        with self.assertRaisesRegex(MotionError, 'PLANNING_CANCELLED'):
            detour.search([0,0], [1,1], [-1,-1], [1,1], lambda _: True, guard=cancel)

    def test_edge_checker_catches_thin_obstacle_between_endpoints(self):
        # Wider than edge resolution, endpoints alone would incorrectly pass.
        with self.assertRaisesRegex(MotionError, 'DETOUR_NOT_FOUND'):
            detour.search([-.9], [.9], [-1], [1], lambda q: abs(q[0]) > .04, max_nodes=30)


class IntegrationTests(unittest.TestCase):
    def test_local_search_timeout_does_not_claim_no_route(self):
        e = Engine(realtime=False)
        action = {'kind':'move_tcp', 'name':'transfer', 'p':LIFT, 'n':UP, 'requires_dry':True}
        with patch('engine.time.perf_counter', side_effect=[0., 7., 7.]):
            with self.assertRaisesRegex(MotionError, 'DETOUR_SEARCH_TIMEOUT'):
                e._plan_dry_detour(action, e.q, e.liquid, True)
        self.assertFalse(e.queue)


    def test_real_robot_bypasses_blocked_joint_interpolation(self):
        fixture = json.loads((Path(__file__).parent/'evidence/detour-fixture.json').read_text())
        e = Engine(realtime=False)
        a, b = np.array(fixture['q_start']), np.array(fixture['q_goal'])
        with self.assertRaisesRegex(MotionError, 'COLLISION'):
            e.plan({'kind': 'move_joints', 'q_rad': b, 'duration_s': 12.}, a)
        def valid(q):
            from collision_model import clearance
            if clearance(q)[0] < 0: return False
            p, n, *_ = arm.fk(q, BASE)
            return e.liquid.geometry.connected_volume(p, n, e.liquid.height) is None
        points, stats = detour.search(a, b, arm.LOW+1e-6, arm.HIGH-1e-6, valid)
        self.assertGreater(len(points), 2)
        for start, goal in zip(points, points[1:]):
            distance = float(np.max(np.abs(goal-start)))
            duration = max(1.2, 1.875*distance/np.deg2rad(20),
                           math.sqrt(5.774*distance/np.deg2rad(40)))
            path, gap = e.plan({'kind': 'move_joints', 'q_rad': goal, 'duration_s': duration}, start)
            self.assertGreaterEqual(gap, 0)
            for sample in path:
                self.assertTrue(valid(sample))
                p, n, *_ = arm.fk(sample, BASE)
                e.liquid.advance(.02, p, n)
        self.assertEqual(e.liquid.spill, 0)
        self.assertLessEqual(e.liquid.ladle, 1e-9)

    def test_loaded_or_unverified_ladle_cannot_enter_search(self):
        e = Engine(realtime=False)
        a = {'kind':'move_tcp','name':'transfer','p':LIFT,'n':UP,'requires_dry':True}
        with self.assertRaisesRegex(MotionError, 'DOCK_DRY_INTERLOCK'):
            e._plan_dry_detour(a, e.q, e.liquid, False)
        e.liquid.ladle = 10e-6
        with self.assertRaisesRegex(MotionError, 'DOCK_DRY_INTERLOCK'):
            e._plan_dry_detour(a, e.q, e.liquid, True)

    def test_search_only_after_all_fixed_routes_fail_and_preserves_dry_checks(self):
        e = Engine(realtime=False)
        original = e.plan
        seen, fallback = [], []
        search = e._plan_dry_detour
        def force_transfer_failure(a, q):
            if a['name'] == '近空检查后退出锅口':
                seen.append(a['name'])
                raise MotionError('COLLISION')
            return original(a, q)
        def observe(*args):
            fallback.append(len(seen))
            return search(*args)
        e.plan = force_transfer_failure
        e._plan_dry_detour = observe
        e.start_preset('dock', 'search', e.incarnation)
        frame = finish(e, timeout=60)
        self.assertEqual(frame['status'], 'waiting_operator', frame['job'])
        self.assertGreaterEqual(fallback[0], 5)  # Four fixed, then first search retry.
        self.assertTrue(frame['job']['dry_verified'])
        self.assertEqual(frame['job']['predicted_spill_increment_m3'], 0)
        self.assertTrue(frame['job']['detours'])
        self.assertIn('绕行', e.code_source)
        self.assertTrue(frame['job']['detours'][-1]['validated'])

    def test_rejected_detour_does_not_move_or_drain_real_state(self):
        e = Engine(realtime=False)
        original, q, liquid = e.plan, e.q.copy(), e.liquid.snapshot()
        def force_transfer_failure(a, start):
            if a['name'] == '近空检查后退出锅口': raise MotionError('COLLISION')
            return original(a, start)
        e.plan = force_transfer_failure
        e._plan_dry_detour = lambda *args: (_ for _ in ()).throw(MotionError('DETOUR_NOT_FOUND'))
        e.start_preset('dock', 'blocked-search', e.incarnation)
        frame = finish(e, timeout=60)
        self.assertEqual(frame['status'], 'failed')
        np.testing.assert_array_equal(e.q, q)
        self.assertEqual(e.liquid.snapshot(), liquid)
        self.assertFalse(e.queue)


if __name__ == '__main__': unittest.main()
