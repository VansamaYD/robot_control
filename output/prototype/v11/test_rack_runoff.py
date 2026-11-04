"""Physical/visual scene alignment and conservative liquid recirculation."""
import unittest
from unittest.mock import patch
from copy import deepcopy
import numpy as np
from scene_geometry import SCENE,Geometry,validate_scene
from liquid import Liquid
from projects import ProjectStore
import tempfile


class RackRunoffTests(unittest.TestCase):
    def test_chicken_and_legs_are_above_liquid_and_supported(self):
        validate_scene()
        chicken=SCENE['chicken']
        self.assertAlmostEqual(chicken['center_m'][2]-chicken['radii_m'][2],SCENE['rack']['support_height_m'])
        for part in [chicken]+chicken['legs']:
            self.assertGreater(part['center_m'][2]-part['radii_m'][2],SCENE['initial_liquid_height_m'])
            self.assertLessEqual(part['center_m'][2]+part['radii_m'][2],.145+1e-12)

    def test_rack_outside_checked_obstacle_is_rejected(self):
        rack=deepcopy(SCENE['rack'])
        rack['rods'][0]['a'][0]=.5
        with patch.dict(SCENE,{'rack':rack}):
            with self.assertRaisesRegex(ValueError,'COLLISION_GEOMETRY_NOT_SUPPORTED'):
                validate_scene()

    def test_new_volume_keeps_original_pick_submerged(self):
        l=Liquid()
        self.assertAlmostEqual(l.height,.08,places=10)
        self.assertGreater(l.initial,3.9e-3)
        self.assertLess(l.initial,4.1e-3)
        for _ in range(35):l.advance(.02,[0,-.1,.075],[0,0,1])
        self.assertGreater(l.ladle,30e-6)

    def test_body_removed_from_water_exposes_fluid_under_rack(self):
        g=Geometry()
        self.assertFalse(g.inside_chicken([0,.05,.075]))
        self.assertTrue(g.submerged([0,.05,.075],.08))
        self.assertTrue(g.inside_chicken([0,.05,.120]))

    def test_surface_and_visible_return_are_real_conserved_reservoirs(self):
        l=Liquid();l.pot-=30e-6;l.ladle=30e-6
        film_seen=return_seen=False
        for _ in range(400):
            before=l.returned
            l.advance(.02,[0,-.02,.195],[1,0,0])
            frame=l.snapshot()
            film_seen |= bool(frame['surface_packets'])
            return_seen |= any(p['kind']=='return' for p in frame['packets'])
            for packet in frame['packets']:
                if packet['kind']=='return':self.assertFalse(l.geometry.inside_chicken(packet['position_m']))
            self.assertGreaterEqual(l.returned,before)
            self.assertAlmostEqual(l.surface,sum(f['v'] for f in l.films),places=12)
            self.assertAlmostEqual(l.total(),l.initial,places=12)
        self.assertTrue(film_seen);self.assertTrue(return_seen)
        self.assertGreater(l.returned,29e-6)
        self.assertEqual(l.surface,0)
        self.assertFalse(l.packets)
        self.assertEqual(l.spill,0)
        self.assertAlmostEqual(sum(l.coverage.values()),30e-6,places=10)

    def test_old_scene_success_does_not_mark_new_scene_draft_verified(self):
        with tempfile.TemporaryDirectory() as d:
            old=ProjectStore(d,configuration='old')
            a=old.save('test',[{'kind':'wait','name':'wait','duration_s':.2}])
            old.note_result(a['revision'],'job','succeeded',configuration='old')
            new=ProjectStore(d,configuration=SCENE['revision'])
            self.assertEqual(new.get(a['revision'])['validation_status'],'previous_scene')
            new.note_result(a['revision'],'job-new','succeeded',configuration=SCENE['revision'])
            self.assertEqual(new.get(a['revision'])['validation_status'],'succeeded')


if __name__=='__main__':unittest.main()
