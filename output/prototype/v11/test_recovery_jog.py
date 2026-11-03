import json
import math
from pathlib import Path
import time
import unittest
import numpy as np
from engine import Engine, MotionError, arm, BASE
from liquid import Liquid
from presets import HOME_Q, DOCK


def finish(engine, timeout=45):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        if engine.job and engine.job['status'] in ('succeeded', 'failed', 'cancelled', 'waiting_operator'):
            return engine.snapshot()
        if engine.queue and not engine.paused:
            engine.advance()
        else:
            time.sleep(.002)
    raise AssertionError('Timed out: ' + str(engine.snapshot()['job']))


class LiquidRegressionTests(unittest.TestCase):
    def test_submerged_tilt_does_not_create_airborne_spill(self):
        liquid = Liquid(); liquid.pot -= 30e-6; liquid.ladle = 30e-6
        for _ in range(300):
            liquid.advance(.02, [0, -.1, .075], [.707106781, 0, .707106781])
        self.assertEqual(liquid.spill, 0)
        self.assertFalse(liquid.packets)
        self.assertAlmostEqual(liquid.total(), liquid.initial, places=12)

    def test_small_volume_does_not_pour_at_shallow_tilt(self):
        liquid = Liquid(); liquid.pot -= 2e-6; liquid.ladle = 2e-6
        for _ in range(100):
            liquid.advance(.02, [0, -.1, .195], [math.sin(.4), 0, math.cos(.4)])
        self.assertAlmostEqual(liquid.ladle, 2e-6, places=12)
        self.assertFalse(liquid.packets)

    def test_reported_pose_not_rejected_by_cylindrical_bottom_bug(self):
        fixture = json.loads((Path(__file__).parent/'evidence/reported-liquid-state.json').read_text())
        liquid = Liquid()
        details = liquid.scoop_diagnostics(fixture['tcp_m'], fixture['normal_world'], 30e-6)
        self.assertTrue(details['ready'], details)

    def test_excessive_tilt_is_explained_before_job_start(self):
        engine = Engine(realtime=False)
        engine.q[3] += math.radians(25)
        with self.assertRaisesRegex(MotionError, 'SCOOP_PRECONDITION_FAILED') as caught:
            engine.start('tilted')
        self.assertEqual(caught.exception.details['reason'], 'LADLE_TILTED')
        self.assertGreater(caught.exception.details['pot_m3'], .002)
        self.assertIsNone(engine.job)


class JogTests(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(realtime=False)

    def start(self):
        return self.engine.start_jog('shoulder_pan', 1, 'hold', self.engine.incarnation)

    def test_hold_advances_without_building_a_queue(self):
        result = self.start(); initial = self.engine.q.copy()
        for _ in range(15): self.engine.advance()
        self.assertGreater(self.engine.q[0], initial[0])
        self.assertFalse(self.engine.queue)
        self.assertLessEqual(abs(self.engine.velocity[0]), math.radians(4)+1e-12)
        self.engine.stop_jog(result['lease_token'])

    def test_release_freezes_pose_and_invalidates_heartbeat(self):
        result = self.start(); self.engine.advance()
        self.engine.stop_jog(result['lease_token'])
        q = self.engine.q.copy()
        for _ in range(10): self.engine.advance()
        np.testing.assert_array_equal(q, self.engine.q)
        with self.assertRaisesRegex(MotionError, 'STALE_JOG_LEASE'):
            self.engine.heartbeat_jog(result['lease_token'])

    def test_expiry_uses_wall_time_even_while_paused(self):
        self.start(); self.engine.paused = True
        self.engine.expire_jog(self.engine.jog['deadline']+.01)
        self.assertIsNone(self.engine.jog)
        self.assertEqual(self.engine.job['end_reason'], 'LEASE_EXPIRED')

    def test_old_stop_does_not_stop_new_lease(self):
        old = self.start(); self.engine.stop_jog(old['lease_token'])
        new = self.engine.start_jog('shoulder_pan', -1, 'new', self.engine.incarnation)
        with self.assertRaisesRegex(MotionError, 'STALE_JOG_LEASE'):
            self.engine.stop_jog(old['lease_token'])
        self.assertEqual(self.engine.jog['lease'], new['lease_token'])
        self.engine.stop_jog(new['lease_token'])

    def test_expired_lease_cannot_be_renewed_late(self):
        result = self.start(); self.engine.jog['deadline'] = time.monotonic()-.01
        with self.assertRaisesRegex(MotionError, 'STALE_JOG_LEASE'):
            self.engine.heartbeat_jog(result['lease_token'])
        self.assertIsNone(self.engine.jog)


class RecoveryTests(unittest.TestCase):
    def test_failed_complete_route_does_not_move_or_drain(self):
        engine = Engine(realtime=False)
        initial_q, initial_liquid = engine.q.copy(), engine.liquid.snapshot()
        def reject(action, start): raise MotionError('COLLISION')
        engine.plan = reject
        engine.start_preset('home', 'blocked-home', engine.incarnation)
        frame = finish(engine)
        self.assertEqual(frame['status'], 'failed')
        self.assertFalse(frame['job']['preplanning'])
        np.testing.assert_array_equal(engine.q, initial_q)
        self.assertEqual(engine.liquid.snapshot(), initial_liquid)

    def test_reset_is_explicit_and_invalidates_pending_jog(self):
        engine = Engine(realtime=False)
        old_boot = engine.incarnation
        result = engine.start_jog('shoulder_pan', 1, 'j', old_boot)
        engine.advance()
        with self.assertRaisesRegex(MotionError, 'RESET_CONFIRMATION_REQUIRED'):
            engine.reset_simulation(old_boot, 'no')
        frame = engine.reset_simulation(old_boot, 'RESET_SIMULATION')
        self.assertNotEqual(frame['incarnation'], old_boot)
        self.assertEqual(frame['tick'], 0)
        self.assertEqual(frame['liquid']['ladle_m3'], 0)
        np.testing.assert_array_equal(frame['q_rad'], HOME_Q)
        with self.assertRaisesRegex(MotionError, 'STALE_JOG_LEASE'):
            engine.heartbeat_jog(result['lease_token'])

    def test_reported_pose_returns_home_without_resetting_liquid(self):
        engine = Engine(realtime=False)
        fixture = json.loads((Path(__file__).parent/'evidence/reported-liquid-state.json').read_text())
        engine.q = np.array(fixture['q_rad']); engine.target_q = engine.q.copy()
        engine.liquid.pot -= 7e-6; engine.liquid.spill = 7e-6
        total, boot = engine.liquid.total(), engine.incarnation
        engine.start_preset('home', 'home', boot)
        frame = finish(engine)
        self.assertEqual(frame['status'], 'succeeded', frame['job'])
        np.testing.assert_allclose(frame['q_rad'], HOME_Q, atol=1e-12)
        self.assertEqual(engine.incarnation, boot)
        self.assertAlmostEqual(engine.liquid.spill, 7e-6, places=12)
        self.assertAlmostEqual(engine.liquid.total(), total, places=12)

    def test_dock_and_return_are_checked_and_require_operator_handoff(self):
        engine = Engine(realtime=False)
        engine.start_preset('dock', 'dock', engine.incarnation)
        frame = finish(engine)
        self.assertEqual(frame['status'], 'waiting_operator', frame['job'])
        self.assertTrue(frame['parking']['virtual_parked'])
        np.testing.assert_allclose(frame['tcp_m'], DOCK, atol=.001)
        self.assertFalse(frame['parking']['support_contact_validated'])
        with self.assertRaisesRegex(MotionError, 'CONTROL_BUSY'):
            engine.start('cannot-restart')
        engine.start_preset('home', 'return', engine.incarnation)
        frame = finish(engine)
        self.assertEqual(frame['status'], 'succeeded', frame['job'])
        self.assertTrue(frame['scoop_readiness']['ready'])


if __name__ == '__main__':
    unittest.main()
