"""A sampled SO-101 kinematic route with coarse collision screening.

No mesh collision, physics, real actuator dynamics, or fluid simulation.
All limits in CONFIG are proposed bench-test limits, not rated capabilities.
"""
from pathlib import Path
import sys, json, math
import numpy as np
from arm_model import fk,solve,LOW,HIGH,TOOL
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'v02'))
from geometry_sim import Scenario,evaluate,ellipse_distance

CONFIG={
 'pot_diameter_mm':400,'pot_depth_mm':120,'liquid_height_mm':80,
 'chicken_size_mm':[260,180,150],'chicken_offset_y_mm':50,
 'ladle_diameter_mm':50,'ladle_depth_mm':25,
 'base_xyz_mm':[0,-400,80],'base_yaw_deg':90,
 'tool_offset_local_mm':[-80,0,-90],
 'arm_capsule_radius_mm':[25,25,20,20],
 'tool_stem_radius_mm':4,'collision_margin_mm':7.5,
 'sample_period_s':.05,
 'proposed_joint_speed_deg_s':60,'proposed_joint_accel_deg_s2':150,
 'proposed_joint_jerk_deg_s3':1000,
 'dry_tool_mass_kg':.100,'liquid_mass_kg':.033,
}
BASE=np.r_[np.array(CONFIG['base_xyz_mm'])/1000,np.pi/2]
PICK=np.array([0,-.100,.075])
LIFT=np.array([0,-.100,.195])
TARGETS=[np.array([-.040,-.020,.195]),np.array([0,-.020,.195]),np.array([.040,-.020,.195])]
UP=np.array([0,0,1.])


def normal(angle):return np.array([np.sin(angle),0,np.cos(angle)])


def smooth(s):return 10*s**3-15*s**4+6*s**5


def segments(target):
    # One scoop per target. Every translation has zero speed/accel at each end.
    return [
      ('取汁停留',.7,PICK,PICK,0,0,True),
      ('竖直抬勺',3.3,PICK,LIFT,0,0,True),
      ('高位平移',2.0,LIFT,target,0,0,True),
      ('定点倾倒',3.0,target,target,0,np.pi/4,True),
      ('等待滴流',.7,target,target,np.pi/4,np.pi/4,False),
      ('恢复水平',2.5,target,target,np.pi/4,0,False),
      ('空勺返回',1.6,target,LIFT,0,0,False),
      ('取汁区下降',3.6,LIFT,PICK,0,0,False),
    ]


def pot_distance(points):
    # Euclidean point-to-spherical-cap wall/rim in the rho/z plane.
    a=.200;H=.120;R=(a*a+H*H)/(2*H)
    rho=np.linalg.norm(points[:,:2],axis=1);z=points[:,2]
    theta=np.clip(np.arctan2(rho,R-z),0,np.arcsin(a/R))
    return np.hypot(rho-R*np.sin(theta),z-(R-R*np.cos(theta)))


def chicken_distance(points):
    d=ellipse_distance(points[:,0]*1000,points[:,1]*1000-50,130,90)/1000
    dz=np.maximum(np.maximum(-points[:,2],points[:,2]-.150),0)
    outside=np.hypot(np.maximum(d,0),dz)
    return np.where((d<0)&(dz==0),-.001,outside)


def samples(a,b,spacing=.003):
    return a+(b-a)*np.linspace(0,1,max(2,int(np.ceil(np.linalg.norm(b-a)/spacing))+1))[:,None]


def clearance(q):
    p,n,origins,axes,frames=fk(q,BASE)
    # Base/shoulder are represented separately; moving segments use capsules.
    margin=.0075
    closest=1e9;label=''
    chains=[]
    for i,(a,b)in enumerate(zip(origins[:-1],origins[1:])):
        chains.append((f'arm_{i+1}',samples(a,b),CONFIG['arm_capsule_radius_mm'][i]/1000))
    T=frames[-1];R=T[:3,:3];o=T[:3,3]
    bend=o+R@np.array([0,0,-.090])
    chains += [('stem_upper',samples(o,bend),.004),('stem_drop',samples(bend,p),.004)]
    # Bowl surface: horizontal/tilted hemisphere; rim is at local X=-80mm.
    bp=[]
    for theta in np.linspace(0,np.pi/2,13):
        for phi in np.linspace(0,2*np.pi,32,endpoint=False):
            local=np.array([-.025*np.cos(theta),.025*np.sin(theta)*np.cos(phi),.025*np.sin(theta)*np.sin(phi)])
            bp.append(p+R@local)
    chains.append(('bowl',np.array(bp),0))
    for name,pts,radius in chains:
        v=min(float(np.min(pot_distance(pts))),float(np.min(chicken_distance(pts))))-radius-margin
        if v<closest:closest,label=v,name
    # Coarse self-collision: sufficiently separated moving segments only.
    # Adjacent links and the folded tool intentionally share joints.
    for i in range(4):
        for j in range(i+2,4):
            ai,aj=chains[i],chains[j]
            d=float(np.min(np.linalg.norm(ai[1][:,None,:]-aj[1][None,:,:],axis=2)))-ai[2]-aj[2]-margin
            if d<closest:closest,label=d,f'self_{i+1}_{j+1}'
    for i in range(3):
        for j in [4,5,6]:
            ai,aj=chains[i],chains[j]
            d=float(np.min(np.linalg.norm(ai[1][:,None,:]-aj[1][None,:,:],axis=2)))-ai[2]-aj[2]-margin
            if d<closest:closest,label=d,f'self_{ai[0]}_{aj[0]}'
    return closest,label,p,n,origins,frames,bend


def gravity_torque(q,loaded):
    p,n,origins,axes,frames=fk(q,BASE)
    # Native links through wrist only. Original gripper assembly is replaced by
    # the assumed custom dry-tool assembly; do not double-count its 87g URDF mass.
    data=[(.100006,[-.0307604,-1.66727e-5,-.0252713]),
          (.103,[-.0898471,-.00838224,.0184089]),
          (.104,[-.0980701,.00324376,.0182831]),
          (.079,[-.000103312,-.0386143,.0281156])]
    loads=[]
    for i,(mass,com)in enumerate(data):loads.append((i,mass,frames[i][:3,3]+frames[i][:3,:3]@com))
    T=frames[-1]
    loads.append((4,.100,T[:3,3]+T[:3,:3]@np.array([-.045,0,-.050])))
    if loaded:loads.append((4,.033,p-n*.010))
    torques=np.zeros(5)
    for j in range(5):
        for last,mass,com in loads:
            if last>=j:torques[j]+=np.dot(axes[j],np.cross(com-origins[j],np.array([0,0,-9.81*mass])))
    return torques


def run(output_dir=None):
    # Verify selected UI dimensions against the previous geometric model.
    s=Scenario('selected',diameter=400,depth=120,liquid_height=80,
               chicken_offset_y=50,ladle_diameter=50,ladle_depth=25)
    geom,_=evaluate(s)
    assert geom['feasible']
    outputs=[]
    for tid,target in enumerate(TARGETS):
        q,pe,ne=solve(PICK,UP,BASE,restarts=20)
        assert pe<.0002 and ne<.002,'Initial pose has no IK solution'
        frames_out=[];elapsed=0
        errors=[];gaps=[];torques=[];phases=[]
        for phase,T,p0,p1,a0,a1,loaded in segments(target):
            dt=CONFIG['sample_period_s']
            count=round(T/dt)
            phases.append(dict(name=phase,start=round(elapsed,3),duration=T))
            for j in range(count+1):
                if frames_out and j==0:continue
                u=j/count;v=smooth(u);p=p0+(p1-p0)*v;angle=a0+(a1-a0)*v;n=normal(angle)
                qq,pe,ne=solve(p,n,BASE,seed=q,restarts=0)
                if pe>.0002 or ne>.002:
                    raise RuntimeError(f'IK discontinuity target {tid}, {phase}: {pe*1000:.2f}mm {np.rad2deg(ne):.2f}deg')
                q=qq
                gap,which,actual,actual_n,origins,Ts,bend=clearance(q)
                gaps.append(gap);errors.append((pe,ne));torques.append(gravity_torque(q,loaded))
                frames_out.append(dict(t=round(elapsed+j*T/count,4),phase=phase,
                  q=np.rad2deg(q).round(4).tolist(),p=(actual*1000).round(3).tolist(),
                  n=actual_n.round(5).tolist(),joints=(origins*1000).round(3).tolist(),
                  bend=(bend*1000).round(3).tolist(),R=Ts[-1][:3,:3].round(6).tolist(),
                  clearance_mm=round(gap*1000,3),nearest=which))
            elapsed+=T
        qs=np.deg2rad(np.array([f['q'] for f in frames_out]))
        # Central differences for joint derivative screening; proposed limits only.
        velocity=np.gradient(qs,CONFIG['sample_period_s'],axis=0,edge_order=2)
        accel=np.gradient(velocity,CONFIG['sample_period_s'],axis=0,edge_order=2)
        jerk=np.gradient(accel,CONFIG['sample_period_s'],axis=0,edge_order=2)
        stats=dict(duration_s=round(elapsed,2),sample_count=len(frames_out),
            max_position_error_mm=float(max(e[0]for e in errors)*1000),
            max_normal_error_deg=float(np.rad2deg(max(e[1]for e in errors))),
            minimum_coarse_clearance_mm=float(min(gaps)*1000),
            minimum_joint_limit_margin_deg=float(np.rad2deg(np.minimum(qs-LOW,HIGH-qs).min())),
            peak_joint_speed_deg_s=np.rad2deg(np.max(np.abs(velocity),axis=0)).round(2).tolist(),
            peak_joint_accel_deg_s2=np.rad2deg(np.max(np.abs(accel),axis=0)).round(2).tolist(),
            peak_joint_jerk_deg_s3=np.rad2deg(np.max(np.abs(jerk),axis=0)).round(2).tolist(),
            peak_estimated_gravity_torque_nm=np.max(np.abs(torques),axis=0).round(3).tolist())
        outputs.append(dict(id=chr(65+tid),target_mm=(target*1000).tolist(),phases=phases,stats=stats,frames=frames_out))
        print(chr(65+tid),json.dumps(stats,ensure_ascii=False))
    out={'config':CONFIG,'scope':'Five-joint FK/IK, proposed custom tool, sampled capsule/bowl screening. Not full mesh, real dynamics, fluid or safety validation.',
         'urdf_source':'https://github.com/TheRobotStudio/SO-ARM100/blob/main/Simulation/SO101/so101_new_calib.urdf',
         'pick_mm':(PICK*1000).tolist(),'lift_mm':(LIFT*1000).tolist(),'routes':outputs}
    base=Path(output_dir) if output_dir is not None else Path(__file__).resolve().parent
    base.mkdir(parents=True,exist_ok=True)
    (base/'trajectory_results.json').write_text(json.dumps(out,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    (base/'summary.json').write_text(json.dumps({**{k:v for k,v in out.items()if k!='routes'},'routes':[{k:v for k,v in r.items()if k!='frames'}for r in outputs]},ensure_ascii=False,indent=2),encoding='utf-8')
    return out


if __name__=='__main__':run()
