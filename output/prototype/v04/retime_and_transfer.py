from pathlib import Path
import sys,json,math
import numpy as np
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'v03'))
from arm_model import solve,LOW,HIGH
import trajectory_sim as sim
import protection_and_dock as protection
from search_dock import human_gap


def slowed_duration(t,scale):
    return math.ceil(t*max(1,scale)*1.10*10)/10 if scale>1.001 else t


def transfer_specs():
    S=sim.LIFT;D=np.array([0,-.1,.16]);G=protection.GATE;U=protection.ABOVE;K=protection.DOCK
    return [
      ('降低至锅内倒回点',2.,S,D,0,0),
      ('倒回锅内',3.,D,D,0,np.pi/4),
      ('等待残汁滴流',1.,D,D,np.pi/4,np.pi/4),
      ('恢复水平',2.5,D,D,np.pi/4,0),
      ('回到高位中转',2.,D,S,0,0),
      ('退出锅口',3.5,S,G,0,0),
      ('移动至泊位上方',3.5,G,U,0,0),
      ('低速落座',2.,U,K,0,0),
    ]


def sample_specs(specs):
    fs=[];elapsed=0;phases=[]
    q,pe,ne=solve(specs[0][2],sim.normal(specs[0][4]),sim.BASE,restarts=20)
    for name,T,p0,p1,a0,a1 in specs:
        phases.append(dict(name=name,start=round(elapsed,3),duration=T));count=round(T/.05)
        for j in range(count+1):
            if fs and j==0:continue
            v=sim.smooth(j/count);p=p0+(p1-p0)*v;normal=sim.normal(a0+(a1-a0)*v)
            q,pe,ne=solve(p,normal,sim.BASE,seed=q,restarts=0)
            assert pe<.0002 and ne<.002
            gap,which,actual,n,o,Ts,bend=sim.clearance(q)
            assert gap>0
            fs.append(dict(t=round(elapsed+j*.05,3),phase=name,q=np.rad2deg(q).round(5).tolist(),
              p=(actual*1000).round(3).tolist(),n=n.round(6).tolist(),joints=(o*1000).round(3).tolist(),
              bend=(bend*1000).round(3).tolist(),R=Ts[-1][:3,:3].round(6).tolist(),
              clearance_mm=gap*1000,human_zone_gap_mm=human_gap(q)*1000,nearest=which))
        elapsed+=T
    qs=np.deg2rad(np.array([f['q']for f in fs]))
    return dict(phases=phases,frames=fs,stats=dict(duration_s=round(elapsed,3),
        minimum_coarse_extra_clearance_mm=min(f['clearance_mm']for f in fs),
        minimum_joint_limit_margin_deg=float(np.rad2deg(np.minimum(qs-LOW,HIGH-qs).min())),
        conditional_stopping_screen=protection.stopping_screen(fs)))


if __name__=='__main__':
    previous=json.loads((ROOT/'protection_results.json').read_text())
    phase_bounds={}
    for s in previous['work_route_screens']:
        for phase,factor in s['phase_scale_bounds'].items():phase_bounds[phase]=max(phase_bounds.get(phase,1),factor)
    original=sim.segments
    def slow_work(target):
        return [(name,slowed_duration(t,phase_bounds.get(name,1)),p0,p1,a0,a1,loaded)
                for name,t,p0,p1,a0,a1,loaded in original(target)]
    sim.segments=slow_work
    work=sim.run(ROOT/'work-retimed')
    work_screens=[dict(route=r['id'],**protection.stopping_screen(r['frames']))for r in work['routes']]
    assert all(s['scale']<=1.05 for s in work_screens)

    raw=sample_specs(transfer_specs())
    bounds=raw['stats']['conditional_stopping_screen']['phase_scale_bounds']
    slow_specs=[(name,slowed_duration(t,bounds.get(name,1)),p0,p1,a0,a1)
                for name,t,p0,p1,a0,a1 in transfer_specs()]
    transfer=sample_specs(slow_specs)
    assert transfer['stats']['conditional_stopping_screen']['scale']<=1.05
    # After manual re-confirmation only: leave support upward, then retrace the
    # already screened empty exit route. No automatic restart is implemented.
    reverse=[('重新接管并抬离支撑',slow_specs[-1][1],protection.DOCK,protection.ABOVE,0,0),
             ('返回锅外中转',slow_specs[-2][1],protection.ABOVE,protection.GATE,0,0),
             ('返回高位待命',slow_specs[-3][1],protection.GATE,sim.LIFT,0,0)]
    reentry=sample_specs(reverse)
    assert reentry['stats']['conditional_stopping_screen']['scale']<=1.05
    result=dict(assumptions=protection.ASSUMPTIONS,retimed_work_screens=work_screens,
      retimed_work_duration_s=work['routes'][0]['stats']['duration_s'],transfer=transfer,reentry=reentry,
      conditional_only=True,
      missing='Hardware stop measurements, fixture contact/latch validation, full meshes, people approach and thermal/fluid tests')
    (ROOT/'transfer_results.json').write_text(json.dumps(result,ensure_ascii=False,separators=(',',':')))
    print(json.dumps(dict(work_duration_s=result['retimed_work_duration_s'],work_screens=work_screens,
      transfer_stats=transfer['stats'],transfer_phases=transfer['phases'],reentry_stats=reentry['stats']),ensure_ascii=False,indent=2))
