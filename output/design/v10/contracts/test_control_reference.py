import unittest
from control_reference import Rejected, Session, fingerprint


class ControlContractTests(unittest.TestCase):
    def setUp(self):
        self.s = Session('boot-1')
        self.request = {'program_revision': 'code-1', 'configuration': 'scene-1',
                        'parameters': {'rounds': 5}}

    def denied(self, code, function, *args, **kwargs):
        with self.assertRaises(Rejected) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)

    def start(self):
        return self.s.start('alice', 'key-1', self.request, 0)

    def test_startup_is_paused(self):
        self.assertTrue(self.s.paused)
        self.assertFalse(self.s.advance_tick())
        self.assertEqual(self.s.tick, 0)

    def test_explicit_start_starts_clock(self):
        self.start()
        self.assertFalse(self.s.paused)
        self.assertTrue(self.s.advance_tick())

    def test_owner_exclusive(self):
        self.s.acquire('alice', 0)
        self.denied('CONTROL_BUSY', self.s.acquire, 'bob', 0)

    def test_expired_manual_lease_releases_owner(self):
        old = self.s.acquire('alice', 0, 0.1)
        self.s.queue.append({'jog': 1})
        self.s.expire_manual(0.1)
        self.assertIsNone(self.s.owner)
        self.assertFalse(self.s.queue)
        self.assertGreater(self.s.epoch, old)

    def test_lease_expires_while_simulation_paused(self):
        self.s.acquire('alice', 0, 1)
        self.s.expire_manual(2)
        self.assertTrue(self.s.paused)
        self.assertIsNone(self.s.owner)

    def test_autonomous_job_survives_manual_expiry_clock(self):
        job = self.start()
        self.s.expire_manual(10000)
        self.assertEqual(self.s.active_job, job)

    def test_retry_returns_same_job(self):
        job = self.start()
        self.assertEqual(job, self.start())
        self.assertEqual(len(self.s.jobs), 1)

    def test_request_fingerprint_ignores_key_order(self):
        self.assertEqual(fingerprint({'a': 1, 'b': 2}), fingerprint({'b': 2, 'a': 1}))

    def test_changed_retry_conflicts(self):
        self.start()
        changed = dict(self.request, parameters={'rounds': 6})
        self.denied('IDEMPOTENCY_CONFLICT', self.s.start, 'alice', 'key-1', changed, 1)

    def test_invalid_start_does_not_consume_key(self):
        invalid = dict(self.request, configuration='old')
        self.denied('CONFIGURATION_CHANGED', self.s.start, 'alice', 'key-1', invalid, 0)
        self.assertEqual(self.start(), 'job-1')

    def test_other_owner_cannot_start(self):
        self.s.acquire('bob', 0)
        self.denied('CONTROL_BUSY', self.start)

    def test_second_autonomous_job_rejected(self):
        self.start()
        self.denied('CONTROL_BUSY', self.s.start, 'alice', 'key-2', self.request, 0)

    def test_cancel_revokes_late_messages(self):
        job = self.start()
        epoch = self.s.epoch
        self.s.submit('boot-1', epoch, job, 1, {'kind': 'move'})
        self.s.cancel('alice', job)
        self.assertFalse(self.s.queue)
        self.denied('STALE_CAPABILITY', self.s.submit, 'boot-1', epoch, job, 2, {})

    def test_cancel_cannot_affect_new_job(self):
        old = self.start()
        self.s.cancel('alice', old)
        new = self.s.start('alice', 'key-2', self.request, 1)
        epoch = self.s.epoch
        self.s.cancel('alice', old)
        self.assertEqual(self.s.active_job, new)
        self.assertEqual(self.s.epoch, epoch)

    def test_wrong_principal_cannot_cancel(self):
        job = self.start()
        self.denied('NOT_AUTHORIZED', self.s.cancel, 'bob', job)

    def test_old_incarnation_rejected(self):
        job = self.start()
        self.denied('STALE_CAPABILITY', self.s.submit, 'old-boot', self.s.epoch, job, 1, {})

    def test_duplicate_action_sequence_rejected(self):
        job = self.start()
        self.s.submit('boot-1', self.s.epoch, job, 1, {})
        self.denied('ACTION_SEQUENCE_CONFLICT', self.s.submit,
                    'boot-1', self.s.epoch, job, 1, {})

    def test_queue_bounded(self):
        job = self.start()
        self.s.submit('boot-1', self.s.epoch, job, 1, {})
        self.denied('QUEUE_FULL', self.s.submit, 'boot-1', self.s.epoch, job, 2, {})

    def test_pause_stops_ticks_and_new_actions(self):
        job = self.start()
        self.s.pause()
        self.assertFalse(self.s.advance_tick())
        self.denied('NOT_RUNNING', self.s.submit, 'boot-1', self.s.epoch, job, 1, {})

    def test_single_tick_stays_paused(self):
        self.s.advance_tick(single_step=True)
        self.assertEqual(self.s.tick, 1)
        self.assertTrue(self.s.paused)

    def test_single_tick_rejected_while_running(self):
        self.start()
        self.denied('NOT_PAUSED', self.s.advance_tick, single_step=True)

    def test_active_job_cannot_edit_configuration(self):
        self.start()
        self.s.pause()
        self.denied('EDIT_REQUIRES_IDLE_PAUSE', self.s.edit_configuration, 'scene-1', 'scene-2')

    def test_edit_requires_expected_revision(self):
        self.denied('CONFIGURATION_CHANGED', self.s.edit_configuration, 'old', 'scene-2')

    def test_edit_invalidates_old_plan_context(self):
        self.s.edit_configuration('scene-1', 'scene-2')
        self.denied('CONFIGURATION_CHANGED', self.start)

    def test_retry_of_cancelled_job_does_not_restart(self):
        job = self.start()
        self.s.cancel('alice', job)
        self.assertEqual(self.start(), job)
        self.assertIsNone(self.s.active_job)

    def test_nonfinite_input_rejected_before_job_created(self):
        request = dict(self.request, parameters={'volume': float('nan')})
        self.denied('INVALID_ARGUMENT', self.s.start, 'alice', 'bad', request, 0)
        self.assertFalse(self.s.jobs)

    def test_state_changes_publish_new_revision_without_tick(self):
        before = self.s.snapshot_ref()
        self.s.edit_configuration('scene-1', 'scene-2')
        self.assertNotEqual(before, self.s.snapshot_ref())
        self.assertEqual(self.s.tick, 0)


if __name__ == '__main__':
    unittest.main()
