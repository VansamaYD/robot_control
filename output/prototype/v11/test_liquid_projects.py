from copy import deepcopy
from pathlib import Path
import json
import math
import tempfile
import unittest
import numpy as np
from liquid import Liquid
from scene_geometry import Geometry, SCENE
from projects import ProjectStore, validate


class GeometryAndLiquidTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.geometry = Geometry()

    def liquid(self):
        return Liquid(self.geometry)

    def test_volume_height_inverse_monotonic(self):
        self.assertTrue(np.all(np.diff(self.geometry.volumes) >= 0))
        for height in [.02, .05, .08, .11]:
            self.assertAlmostEqual(self.geometry.height_at(self.geometry.volume_at(height)), height, places=10)

    def test_displacement_is_bounded_by_pot(self):
        for height in [.03, .08, .12]:
            empty_pot = math.pi * height**2 * (self.geometry.curvature - height/3)
            self.assertGreater(self.geometry.volume_at(height), 0)
            self.assertLessEqual(self.geometry.volume_at(height), empty_pot + 1e-9)

    def test_scoop_changes_level_and_conserves(self):
        liquid = self.liquid()
        initial_height = liquid.height
        for _ in range(35):
            liquid.advance(.02, [0, -.1, .075], [0, 0, 1], 30e-6)
        self.assertAlmostEqual(liquid.ladle, 30e-6, places=12)
        self.assertLess(liquid.height, initial_height)
        self.assertAlmostEqual(liquid.total(), liquid.initial, places=12)

    def test_empty_pot_cannot_fill_ladle(self):
        liquid = self.liquid()
        liquid.initial = liquid.pot = 0
        for _ in range(35):
            liquid.advance(.02, [0, -.1, .075], [0, 0, 1], 30e-6)
        self.assertEqual(liquid.ladle, 0)

    def test_above_surface_or_blocked_cannot_scoop(self):
        for point in [[0, -.1, .3], [0, .05, .075], [1, 0, .075]]:
            liquid = self.liquid()
            liquid.advance(.02, point, [0, 0, 1], 30e-6)
            self.assertEqual(liquid.ladle, 0)

    def test_tilted_ladle_cannot_scoop(self):
        liquid = self.liquid()
        liquid.advance(.02, [0, -.1, .075], [1, 0, 0], 30e-6)
        self.assertEqual(liquid.ladle, 0)

    def test_pour_hits_chicken_and_returns(self):
        liquid = self.liquid()
        liquid.pot -= 30e-6
        liquid.ladle = 30e-6
        max_surface = 0
        for _ in range(300):
            liquid.advance(.02, [0, -.02, .195], [1, 0, 0])
            max_surface = max(max_surface, liquid.surface)
        self.assertEqual(liquid.ladle, 0)
        self.assertGreater(max_surface, 0)
        self.assertLess(liquid.spill, 1e-12)
        for _ in range(1000):
            liquid.advance(.02, [0, -.1, .195], [0, 0, 1])
        self.assertLess(liquid.surface, max_surface * .001)
        self.assertAlmostEqual(liquid.total(), liquid.initial, places=12)

    def test_pour_outside_pot_is_spill(self):
        liquid = self.liquid()
        liquid.pot -= 10e-6
        liquid.ladle = 10e-6
        for _ in range(200):
            liquid.advance(.02, [.5, 0, .2], [1, 0, 0])
        self.assertAlmostEqual(liquid.spill, 10e-6, places=12)
        self.assertAlmostEqual(liquid.total(), liquid.initial, places=12)

    def test_many_scoop_pour_cycles_keep_ledger(self):
        liquid = self.liquid()
        for _ in range(100):
            for __ in range(30):
                liquid.advance(.02, [0, -.1, .075], [0, 0, 1], 30e-6)
            for __ in range(120):
                liquid.advance(.02, [0, -.02, .195], [1, 0, 0])
        self.assertLess(abs(liquid.total()-liquid.initial), 1e-10)
        self.assertLessEqual(liquid.ladle, liquid.capacity)

    def test_segment_hit_detects_passing_through_chicken(self):
        hit = self.geometry.segment_chicken([0, .05, .3], [0, .05, -.1])
        self.assertIsNotNone(hit)
        self.assertGreater(hit, 0)
        self.assertLess(hit, 1)

    def test_bad_timestep_rejected(self):
        for dt in [0, -1, float('nan')]:
            with self.assertRaises(ValueError):
                self.liquid().advance(dt, [0, -.1, .075], [0, 0, 1])


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ProjectStore(self.temp.name)
        self.actions = [{'kind': 'wait', 'name': '等待', 'duration_s': .2}]

    def tearDown(self):
        self.temp.cleanup()

    def test_save_reload_and_dedup(self):
        first = self.store.save('test', self.actions)
        self.assertEqual(first, ProjectStore(self.temp.name).get(first['revision']))
        self.assertEqual(first['revision'], self.store.save('test', self.actions)['revision'])
        self.assertEqual(len(self.store.list()), 1)

    def test_edit_creates_new_version_and_preserves_old(self):
        first = self.store.save('test', self.actions)
        actions = deepcopy(self.actions)
        actions[0]['duration_s'] = .4
        second = self.store.save('test', actions)
        self.assertNotEqual(first['revision'], second['revision'])
        self.assertEqual(self.store.get(first['revision'])['actions'][0]['duration_s'], .2)

    def test_imported_code_is_regenerated_not_executed(self):
        record = self.store.save('test', self.actions)
        target = Path(self.temp.name) / (record['revision'] + '.json')
        record['code'] = 'raise RuntimeError("untrusted")'
        target.write_text(json.dumps(record))
        self.assertNotIn('untrusted', self.store.get(record['revision'])['code'])

    def test_tampered_actions_detected(self):
        record = self.store.save('test', self.actions)
        target = Path(self.temp.name) / (record['revision'] + '.json')
        record['actions'][0]['duration_s'] = 10
        target.write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, 'REVISION_CORRUPT'):
            self.store.get(record['revision'])

    def test_revision_path_escape_rejected(self):
        with self.assertRaisesRegex(ValueError, 'INVALID_REVISION'):
            self.store.get('../anything')

    def test_unknown_actions_and_fields_rejected(self):
        for action in [{'kind': 'exec', 'name': 'bad', 'duration_s': 1},
                       {'kind': 'wait', 'name': 'bad', 'duration_s': 1, 'code': 'print(1)'}]:
            with self.assertRaises(ValueError):
                validate([action])

    def test_nonfinite_or_bool_duration_rejected(self):
        for value in [float('nan'), float('inf'), True, -1, 0]:
            with self.assertRaises(ValueError):
                validate([{'kind': 'wait', 'name': 'bad', 'duration_s': value}])

    def test_excess_scoop_volume_rejected(self):
        with self.assertRaisesRegex(ValueError, 'INVALID_SCOOP_VOLUME'):
            validate([{'kind': 'scoop', 'name': 'bad', 'duration_s': 1, 'target_m3': 1}])

    def test_empty_or_oversized_recipe_rejected(self):
        for actions in [[], self.actions * 101]:
            with self.assertRaises(ValueError):
                validate(actions)


if __name__ == '__main__':
    unittest.main()
