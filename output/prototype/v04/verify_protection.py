from pathlib import Path
import json,math
import numpy as np
import protection_and_dock as p
from access_logic import Inputs,human_access_permitted

# Known zero-initial-acceleration S-curve solutions independently derived.
a={**p.ASSUMPTIONS,'adverse_accel_mm_s2':0}
assert abs(p.stop_budget(20,a)['required_clearance_mm']-(2+20*math.sqrt(.02)+10))<1e-9
assert abs(p.stop_budget(80,a)['required_clearance_mm']-42)<1e-9
values=[p.stop_budget(v)['required_clearance_mm']for v in range(101)]
assert all(b>a for a,b in zip(values,values[1:]))
assert abs(p.stop_budget(p.allowed_speed(20))['required_clearance_mm']-20)<1e-6
assert not human_access_permitted(Inputs())
d=json.loads((Path(__file__).resolve().parent/'transfer_results.json').read_text())
for r in [d['transfer'],d['reentry']]:
 assert r['stats']['minimum_coarse_extra_clearance_mm']>0
 assert r['stats']['minimum_joint_limit_margin_deg']>5
 assert r['stats']['conditional_stopping_screen']['scale']<=1.05
 assert all(b['t']>a['t']for a,b in zip(r['frames'],r['frames'][1:]))
assert np.linalg.norm(np.array(d['transfer']['frames'][-1]['p'])-d['reentry']['frames'][0]['p'])<.1
assert all(s['scale']<=1.05 for s in d['retimed_work_screens'])
print('PASS: stopping math, monotonicity, inverse speed budget, exit/reentry continuity and conditional screens.')
print('Conditional allowed speed at 15mm clearance:',round(p.allowed_speed(15),2),'mm/s')
