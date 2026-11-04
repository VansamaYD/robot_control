"""Run all three refills through the authoritative engine and liquid model."""
import unittest
from engine import Engine
from projects import code_view
from recipe_library import three_zone_actions
from test_recovery_jog import finish


class ThreeZoneTests(unittest.TestCase):
    def test_three_independent_pours_hit_each_zone_without_spill(self):
        actions = three_zone_actions()
        program = {'revision':'three-zone-regression','actions':actions,'code':code_view(actions)}
        engine = Engine(realtime=False)
        engine.start('three-zone', program=program)
        frame = finish(engine, timeout=80)
        self.assertEqual(frame['status'],'succeeded', frame['job'])
        coverage = frame['liquid']['coverage']
        self.assertEqual(coverage['covered_zones'],3)
        self.assertTrue(all(v > 25e-6 for v in coverage['zone_hit_m3'].values()))
        self.assertLess(max(coverage['zone_hit_m3'].values())-min(coverage['zone_hit_m3'].values()),1e-6)
        self.assertEqual(frame['liquid']['spill_m3'],0)
        self.assertLess(abs(frame['liquid']['balance_error_m3']),1e-12)
        self.assertEqual(sum(a['kind']=='scoop' for a in actions),3)
        self.assertEqual(len(actions),24)


if __name__=='__main__':unittest.main()
