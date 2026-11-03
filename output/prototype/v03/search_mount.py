"""Screen candidate base mountings against coarse key-pose collisions."""
import numpy as np
import trajectory_sim as sim
from arm_model import solve

ranked=[]
for y in [-.34,-.36,-.38,-.40,-.42]:
    for z in [.00,.02,.04,.06,.08]:
        sim.BASE[:]=[0,y,z,np.pi/2]
        points=[([0,-.100,.075],0),([0,-.100,.195],0)]
        points += [([x,-.020,.195],a)for x in [-.04,0,.04]for a in [0,np.pi/4]]
        gap=1e6;error=0;q=None;reason=''
        for p,a in points:
            q,pe,ne=solve(np.array(p),sim.normal(a),sim.BASE,seed=q,restarts=6)
            error=max(error,pe*1000+np.rad2deg(ne))
            c,reason,*_=sim.clearance(q);gap=min(gap,c*1000)
        ranked.append(dict(y=y,z=z,error=round(error,2),clearance=round(gap,2)))
print(*sorted(ranked,key=lambda x:(x['error']>.2,-x['clearance'],x['error']))[:12],sep='\n')
