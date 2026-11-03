"""SO-101 FK/IK using factual transforms from the official new-calibration URDF.

Only the five motion joints, not the gripper. SI units internally.
Source accessed 2026-11-03:
https://github.com/TheRobotStudio/SO-ARM100/blob/main/Simulation/SO101/so101_new_calib.urdf
Custom tool and mounting geometry are unbuilt design assumptions.
"""
import numpy as np

JOINTS = [
    ('shoulder_pan', [.0388353,-8.97657e-9,.0624], [3.14159,4.18253e-17,-3.14159], [-1.91986,1.91986]),
    ('shoulder_lift', [-.0303992,-.0182778,-.0542], [-1.5708,-1.5708,0], [-1.74533,1.74533]),
    ('elbow_flex', [-.11257,-.028,1.73763e-16], [-3.63608e-16,8.74301e-16,1.5708], [-1.69,1.69]),
    ('wrist_flex', [-.1349,.0052,3.62355e-17], [4.02456e-15,8.67362e-16,-1.5708], [-1.65806,1.65806]),
    ('wrist_roll', [5.55112e-17,-.0611,.0181], [1.5708,.0486795,3.14159], [-2.74385,2.84121]),
]
LOW=np.array([j[3][0] for j in JOINTS])
HIGH=np.array([j[3][1] for j in JOINTS])
TOOL=np.array([-.080,0,-.090])  # 80mm drop, 90mm reach; local +X is bowl normal.


def rz(a):
    c,s=np.cos(a),np.sin(a)
    return np.array([[c,-s,0],[s,c,0],[0,0,1.]])


def rpy(v):
    r,p,y=v
    cr,sr=np.cos(r),np.sin(r);cp,sp=np.cos(p),np.sin(p)
    return rz(y)@np.array([[cp,0,sp],[0,1,0],[-sp,0,cp]])@np.array([[1,0,0],[0,cr,-sr],[0,sr,cr]])


ORIGINS=[]
for _,xyz,angles,_ in JOINTS:
    T=np.eye(4);T[:3,:3]=rpy(angles);T[:3,3]=xyz;ORIGINS.append(T)


def fk(q, base):
    T=np.eye(4);T[:3,3]=base[:3];T[:3,:3]=rz(base[3])
    origins=[];axes=[];frames=[]
    for oi,qi in zip(ORIGINS,q):
        T=T@oi
        origins.append(T[:3,3].copy());axes.append(T[:3,2].copy())
        rot=np.eye(4);rot[:3,:3]=rz(qi);T=T@rot
        frames.append(T.copy())
    p=T[:3,3]+T[:3,:3]@TOOL
    n=T[:3,0]
    return p,n,np.array(origins),np.array(axes),frames


def feature(q,base):
    p,n,*_=fk(q,base)
    return np.r_[p*10,n]  # 100mm = one radian orientation weight.


def solve(p,n,base,seed=None,restarts=12):
    target=np.r_[np.asarray(p)*10,n]
    rng=np.random.default_rng(42)
    seeds=[np.asarray(seed)] if seed is not None else [np.zeros(5)]
    seeds += [rng.uniform(LOW+.08,HIGH-.08) for _ in range(restarts)]
    best=None
    for q0 in seeds:
        q=q0.copy()
        for _ in range(110):
            f=feature(q,base);e=target-f
            if np.linalg.norm(e)<1e-5:break
            J=np.column_stack([(feature(q+np.eye(5)[j]*1e-5,base)-f)/1e-5 for j in range(5)])
            dq=np.linalg.solve(J.T@J+np.eye(5)*1e-5,J.T@e)
            dq*=min(1,.18/max(np.linalg.norm(dq),1e-9))
            # Backtracking avoids climbing across joint limits.
            previous=np.linalg.norm(e)
            for step in [1,.5,.25,.1]:
                trial=np.clip(q+step*dq,LOW+.015,HIGH-.015)
                if np.linalg.norm(target-feature(trial,base))<previous:
                    q=trial;break
            else:break
        pos,normal,*_=fk(q,base)
        pe=np.linalg.norm(pos-p);ne=np.arccos(np.clip(np.dot(normal,n),-1,1))
        score=pe*10+ne
        if best is None or score<best[0]:best=(score,q.copy(),pe,ne)
        if pe<.0001 and ne<.001:break
    return best[1:]


if __name__=='__main__':
    base=np.array([0,-.270,.150,0])
    print('zero',fk(np.zeros(5),base)[:2])
    for p in [[0,-.100,.075],[0,-.100,.215],[-.045,-.020,.215],[.045,-.020,.215]]:
        q,pe,ne=solve(np.array(p),np.array([0,0,1.]),base,restarts=20)
        print('p',p,'qdeg',np.rad2deg(q).round(1),'error mm/deg',pe*1000,np.rad2deg(ne))
