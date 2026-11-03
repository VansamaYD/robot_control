"""Conditional stopping budgets and external docking kinematics.

Assumed stopping parameters are NOT measured actuator capability.
No personnel-access permit is issued by this offline program.
"""
from pathlib import Path
import sys,json,math
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'v03'))
from arm_model import solve,fk,LOW,HIGH
import trajectory_sim as sim
from search_dock import human_gap

ROOT=Path(__file__).resolve().parent
ASSUMPTIONS=dict(reaction_s=.100,decel_mm_s2=200,jerk_mm_s3=1000,
                 adverse_accel_mm_s2=50,
                 position_uncertainty_mm=5,reserve_mm=5,
                 note='Illustrative conditional limits, not measured or safety-rated')
DOCK=np.array([-.240,-.300,.195])
ABOVE=DOCK+np.array([0,0,.025])
# Leave the pot over the near-left quadrant; initially stay high and empty.
GATE=np.array([-.140,-.220,.220])


def stop_budget(v,assumptions=ASSUMPTIONS):
    tr=assumptions['reaction_s'];a=assumptions['decel_mm_s2'];j=assumptions['jerk_mm_s3']
    a0=assumptions.get('adverse_accel_mm_s2',0.)
    reaction=v*tr+.5*a0*tr*tr
    vb=v+a0*tr
    peak=math.sqrt(j*vb+a0*a0/2)
    hold=0.
    if peak>a:
        peak=a;hold=max(0.,(vb+(a0*a0-2*a*a)/(2*j))/a)
    pieces=[((a0+peak)/j,-j),(hold,0.),(peak/j,j)]
    x=0.;speed=vb;accel=a0
    for t,jerk in pieces:
        x+=speed*t+.5*accel*t*t+jerk*t**3/6
        speed+=accel*t+.5*jerk*t*t;accel+=jerk*t
    assert abs(speed)<1e-7 and abs(accel)<1e-7
    braking=sum(t for t,_ in pieces)
    return dict(speed_mm_s=v,braking_time_s=braking,total_time_s=tr+braking,
                reaction_distance_mm=reaction,braking_distance_mm=x,
                required_clearance_mm=reaction+x+assumptions['position_uncertainty_mm']+assumptions['reserve_mm'])


def allowed_speed(clearance):
    if clearance<=stop_budget(0)['required_clearance_mm']:return 0.
    lo,hi=0.,1000.
    for _ in range(50):
        m=(lo+hi)/2
        if stop_budget(m)['required_clearance_mm']<=clearance:lo=m
        else:hi=m
    return lo


def groups(q):
    p,n,o,axes,frames=fk(q,sim.BASE)
    R=frames[-1][:3,:3];bend=o[-1]+R@np.array([0,0,-.090])
    entries=[]
    for i in range(4):
        pts=o[i]+(o[i+1]-o[i])*np.linspace(0,1,31)[:,None]
        entries.append((f'arm_{i+1}',pts,sim.CONFIG['arm_capsule_radius_mm'][i],frames[i][:3,:3]))
    for name,a,b in [('stem_upper',o[-1],bend),('stem_drop',bend,p)]:
        entries.append((name,a+(b-a)*np.linspace(0,1,31)[:,None],4.,R))
    bowl=[]
    for theta in np.linspace(0,np.pi/2,13):
        for phi in np.linspace(0,2*np.pi,32,endpoint=False):
            bowl.append(p+R@np.array([-.025*np.cos(theta),.025*np.sin(theta)*np.cos(phi),.025*np.sin(theta)*np.sin(phi)]))
    entries.append(('bowl',np.array(bowl),0.,R))
    return entries


def stopping_screen(frames,dt=.05):
    collected=[groups(np.deg2rad(f['q']))for f in frames]
    worst=dict(scale=1.);phases={};speeds=[];accelerations=[]
    for g in range(7):
        name=collected[0][g][0];pts=np.array([x[g][1]for x in collected]);radius=collected[0][g][2]
        vel=np.linalg.norm(np.gradient(pts,dt,axis=0,edge_order=2),axis=2).max(axis=1)*1000
        rotations=[x[g][3]for x in collected]
        # Bound angular velocity contribution to surfaces off the centreline.
        omega=[];omega_vec=[]
        for i in range(len(rotations)):
            a,b=max(0,i-1),min(len(rotations)-1,i+1)
            delta=rotations[b]@rotations[a].T
            angle=np.arccos(np.clip((np.trace(delta)-1)/2,-1,1))
            omega.append(angle/((b-a)*dt))
            vee=np.array([delta[2,1]-delta[1,2],delta[0,2]-delta[2,0],delta[1,0]-delta[0,1]])
            factor=angle/(2*np.sin(angle))if angle>1e-7 else .5
            omega_vec.append(vee*factor/((b-a)*dt))
        speed=vel+radius*np.array(omega);speeds.append(speed)
        acc=np.linalg.norm(np.gradient(np.gradient(pts,dt,axis=0,edge_order=2),dt,axis=0,edge_order=2),axis=2).max(axis=1)*1000
        alpha=np.linalg.norm(np.gradient(omega_vec,dt,axis=0,edge_order=2),axis=1)
        acc+=radius*(alpha+np.array(omega)**2);accelerations.append(acc)
        for i,entry in enumerate(collected):
            p=entry[g][1]
            clear=min(sim.pot_distance(p).min(),sim.chicken_distance(p).min())*1000-radius
            vmax=allowed_speed(clear)
            scale=max(float(speed[i]/vmax)if vmax else(float('inf')if speed[i]>.01 else 1.),
                      math.sqrt(float(acc[i])/ASSUMPTIONS['adverse_accel_mm_s2']))
            phase=frames[i]['phase'];phases[phase]=max(phases.get(phase,1.),scale)
            if scale>worst['scale']:
                worst=dict(scale=scale,part=name,t=frames[i]['t'],phase=frames[i]['phase'],
                           speed_bound_mm_s=float(speed[i]),surface_clearance_mm=float(clear),
                           conditional_allowed_speed_mm_s=vmax)
    # Relative speed bounds for non-adjacent self-collision pairs.
    pairs=[(i,j)for i in range(4)for j in range(i+2,4)]+[(i,j)for i in range(3)for j in [4,5,6]]
    for i,j in pairs:
        for k,entries in enumerate(collected):
            ai,aj=entries[i],entries[j]
            clear=float(np.linalg.norm(ai[1][:,None,:]-aj[1][None,:,:],axis=2).min()*1000-ai[2]-aj[2])
            vmax=allowed_speed(clear);speed=float(speeds[i][k]+speeds[j][k]);acc=float(accelerations[i][k]+accelerations[j][k])
            scale=max(speed/vmax if vmax else(float('inf')if speed>.01 else 1.),math.sqrt(acc/ASSUMPTIONS['adverse_accel_mm_s2']))
            phase=frames[k]['phase'];phases[phase]=max(phases.get(phase,1.),scale)
            if scale>worst['scale']:
                worst=dict(scale=scale,part=f'self_{ai[0]}_{aj[0]}',t=frames[k]['t'],phase=phase,
                  speed_bound_mm_s=speed,surface_clearance_mm=clear,conditional_allowed_speed_mm_s=vmax)
    worst['phase_scale_bounds']=phases
    return worst


def docking_segments():
    return [
      ('空勺退出锅口',3.5,sim.LIFT,GATE),
      ('移动至泊位上方',3.5,GATE,ABOVE),
      ('低速落座',2.0,ABOVE,DOCK),
    ]


def make_route():
    fs=[];elapsed=0;phases=[]
    q,pe,ne=solve(sim.LIFT,sim.UP,sim.BASE,restarts=20)
    for phase,T,p0,p1 in docking_segments():
        phases.append(dict(name=phase,start=elapsed,duration=T))
        count=round(T/.05)
        for j in range(count+1):
            if fs and j==0:continue
            p=p0+(p1-p0)*sim.smooth(j/count)
            qq,pe,ne=solve(p,sim.UP,sim.BASE,seed=q,restarts=0)
            if pe>.0002 or ne>.002:raise RuntimeError((phase,p,pe,ne))
            q=qq;c,which,actual,n,o,Ts,bend=sim.clearance(q)
            fs.append(dict(t=round(elapsed+j*.05,3),phase=phase,q=np.rad2deg(q).round(5).tolist(),
                           p=(actual*1000).round(3).tolist(),n=n.round(6).tolist(),
                           joints=(o*1000).round(3).tolist(),bend=(bend*1000).round(3).tolist(),
                           R=Ts[-1][:3,:3].round(6).tolist(),clearance_mm=c*1000,
                           human_zone_gap_mm=human_gap(q)*1000,nearest=which))
        elapsed+=T
    return fs,phases


def support_load(q):
    p,n,o,axes,Ts=fk(q,sim.BASE)
    data=[(.100006,[-.0307604,-1.66727e-5,-.0252713]),(.103,[-.0898471,-.00838224,.0184089]),
          (.104,[-.0980701,.00324376,.0182831]),(.079,[-.000103312,-.0386143,.0281156])]
    loads=[(m,Ts[i][:3,3]+Ts[i][:3,:3]@c)for i,(m,c)in enumerate(data)]
    loads.append((.100,Ts[-1][:3,3]+Ts[-1][:3,:3]@np.array([-.045,0,-.050])))
    # Conservatively retain 33g liquid, even after the intended draining step.
    loads.append((.033,p-n*.010))
    support=o[-1]
    moment=sum((np.cross(c-support,[0,0,-m*9.81])for m,c in loads),start=np.zeros(3))
    return dict(support_xyz_mm=(support*1000).tolist(),assumed_moving_mass_kg=sum(m for m,_ in loads),
                full_weight_n=sum(m for m,_ in loads)*9.81,
                gravity_moment_at_wrist_cradle_nm=float(np.linalg.norm(moment)),
                load_case='Hypothetical entire moving weight taken at wrist cradle; not unique fixture reactions or proof of strength')


if __name__=='__main__':
    previous=json.loads((ROOT.parent/'v03'/'trajectory_results.json').read_text())
    screens=[]
    for r in previous['routes']:
        screens.append(dict(route=r['id'],**stopping_screen(r['frames'])))
    fs,phases=make_route()
    q=np.deg2rad(fs[-1]['q']);load=support_load(q)
    speed=np.gradient(np.array([f['q']for f in fs]),.05,axis=0,edge_order=2)
    result=dict(assumptions=ASSUMPTIONS,stop_table=[stop_budget(v)for v in [20,40,60,80,100]],
      work_route_screens=screens,dock_xyz_mm=(DOCK*1000).tolist(),above_dock_xyz_mm=(ABOVE*1000).tolist(),
      gate_xyz_mm=(GATE*1000).tolist(),dock_phases=phases,dock_frames=fs,
      docking_stats=dict(duration_s=fs[-1]['t'],minimum_coarse_extra_clearance_mm=min(f['clearance_mm']for f in fs),
          parked_human_zone_gap_mm=fs[-1]['human_zone_gap_mm'],human_zone_radius_mm=250,
          peak_joint_speed_deg_s=np.max(np.abs(speed),axis=0).round(2).tolist(),
          minimum_joint_limit_margin_deg=float(np.rad2deg(np.minimum(np.deg2rad([f['q']for f in fs])-LOW,HIGH-np.deg2rad([f['q']for f in fs])).min())),
          stop_screen=stopping_screen(fs)),support_load=load)
    (ROOT/'protection_results.json').write_text(json.dumps(result,ensure_ascii=False,separators=(',',':')))
    print(json.dumps({k:v for k,v in result.items()if k!='dock_frames'},ensure_ascii=False,indent=2))
