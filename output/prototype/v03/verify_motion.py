"""Check continuous-branch FK/IK output and a denser collision sampling pass."""
from pathlib import Path
import json
import numpy as np
import trajectory_sim as sim
from arm_model import fk,solve

base=Path(__file__).resolve().parent
data=json.loads((base/'trajectory_results.json').read_text())
for route in data['routes']:
    fs=route['frames'];stats=route['stats']
    assert all(b['t']>a['t']for a,b in zip(fs,fs[1:]))
    assert stats['minimum_coarse_clearance_mm']>0
    assert stats['max_position_error_mm']<.2
    assert stats['max_normal_error_deg']<.2
    assert stats['minimum_joint_limit_margin_deg']>5
    assert max(stats['peak_joint_speed_deg_s'])<60
    assert max(stats['peak_joint_accel_deg_s2'])<150
    assert max(stats['peak_joint_jerk_deg_s3'])<1000
    assert np.linalg.norm(np.array(fs[0]['q'])-fs[-1]['q'])<.1

# Reject a representative low horizontal crossing, using an actually solved pose.
target=np.array([0,-.02,.12])
q,pe,ne=solve(target,np.array([0,0,1.]),sim.BASE,restarts=20)
assert pe<.0002 and ne<.002
assert sim.clearance(q)[0]<0,'Low crossing must collide with the chicken envelope'

sim.CONFIG['sample_period_s']=.025
fine=sim.run(base/'validation-25ms')
for a,b in zip(data['routes'],fine['routes']):
    assert b['stats']['minimum_coarse_clearance_mm']>0
    assert abs(a['stats']['minimum_coarse_clearance_mm']-b['stats']['minimum_coarse_clearance_mm'])<.25
print('PASS: three continuous IK routes; proposed limits; rejected low crossing; 25ms clearance recheck.')
