from pathlib import Path
import sys
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'v03'))
from arm_model import solve,fk
import trajectory_sim as sim


def human_gap(q):
    p,n,origins,axes,frames=fk(q,sim.BASE)
    T=frames[-1];bend=T[:3,3]+T[:3,:3]@np.array([0,0,-.090])
    gap=1e9
    for i,(a,b)in enumerate(zip(origins[:-1],origins[1:])):
        pts=sim.samples(a,b)
        gap=min(gap,np.linalg.norm(pts[:,:2],axis=1).min()-sim.CONFIG['arm_capsule_radius_mm'][i]/1000-.250)
    for a,b in [(origins[-1],bend),(bend,p)]:
        gap=min(gap,np.linalg.norm(sim.samples(a,b)[:,:2],axis=1).min()-.004-.250)
    gap=min(gap,np.linalg.norm(p[:2])-.025-.250)
    return float(gap)


if __name__=='__main__':
    candidates=[]
    for x in [-.240,-.200,-.160,-.120]:
        for y in [-.340,-.300,-.260]:
            for z in [.160,.195,.220]:
                p=np.array([x,y,z]);q,pe,ne=solve(p,sim.UP,sim.BASE,restarts=12)
                if pe>.0002 or ne>.002:continue
                c=sim.clearance(q)[0];hg=human_gap(q)
                if c>0 and hg>0:
                    candidates.append((round(hg*1000,2),round(c*1000,2),(p*1000).round(1).tolist(),np.rad2deg(q).round(2).tolist()))
    print(*sorted(candidates,reverse=True)[:16],sep='\n')
