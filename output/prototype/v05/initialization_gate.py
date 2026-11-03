"""Offline startup-gate requirements, NOT a hardware/safety controller.

Never writes registers, enables torque, moves the arm, or releases a latch.
PREPARE_HOLD means prerequisites to begin a separately validated takeover
sequence have been met; it does not mean undocking or human access is allowed.
"""
from dataclasses import dataclass,replace
from enum import Enum
import math


class Decision(str,Enum):
    SERVICE_REQUIRED='SERVICE_REQUIRED'
    RECOVERY_REQUIRED='RECOVERY_REQUIRED'
    READ_ONLY='READ_ONLY'
    PREPARE_HOLD='PREPARE_HOLD'


@dataclass(frozen=True)
class Snapshot:
    asset_identity_verified: bool=False
    inventory_matches: bool=False
    calibration_matches: bool=False
    coordinate_mapping_verified: bool=False
    boot_settings_verified: bool=False
    feedback_fresh: bool=False
    alarm_free: bool=False
    encoder_branch_unambiguous: bool=False
    mechanical_support_verified: bool=False
    seated: bool=False
    latch_closed: bool=False
    drive_inhibit_confirmed: bool=False
    tool_profile_matches: bool=False
    dock_pose_independently_verified: bool=False
    q_rad: tuple=()


def startup_gate(s,reference_q,tolerance_rad,lower_rad,upper_rad):
    # Configuration failures require service, not automatic recalibration.
    if not all([s.asset_identity_verified,s.inventory_matches,s.calibration_matches,
                s.coordinate_mapping_verified,s.tool_profile_matches]):
        return Decision.SERVICE_REQUIRED
    if not all([s.feedback_fresh,s.alarm_free,s.boot_settings_verified,s.encoder_branch_unambiguous]):
        return Decision.READ_ONLY
    if not(len(s.q_rad)==len(reference_q)==len(tolerance_rad)==len(lower_rad)==len(upper_rad)==5):
        return Decision.READ_ONLY
    if not all(math.isfinite(x)for x in (*s.q_rad,*reference_q,*tolerance_rad,*lower_rad,*upper_rad)):
        return Decision.READ_ONLY
    if not all(t>0 for t in tolerance_rad):return Decision.READ_ONLY
    # Do not choose a shortest modulo-360 path: only the validated encoder branch
    # and actual limited physical interval are accepted.
    if not all(lo<=q<=hi for q,lo,hi in zip(s.q_rad,lower_rad,upper_rad)):
        return Decision.RECOVERY_REQUIRED
    supported=all([s.mechanical_support_verified,s.seated,s.latch_closed,
                   s.drive_inhibit_confirmed,s.dock_pose_independently_verified])
    near=all(abs(q-r)<=t for q,r,t in zip(s.q_rad,reference_q,tolerance_rad))
    if not supported or not near:return Decision.RECOVERY_REQUIRED
    return Decision.PREPARE_HOLD


def work_start_gate(*,hold_takeover_verified=False,holding_scheme_validated=False,
                    guards_restored=False,zone_clear=False,work_context_verified=False,
                    trajectory_matches_context=False,explicit_start=False):
    # Separate from startup: a closed guard or valid encoder never starts motion.
    return all([hold_takeover_verified,holding_scheme_validated,guards_restored,zone_clear,
                work_context_verified,trajectory_matches_context,explicit_start])


if __name__=='__main__':
    reference=tuple(math.radians(q)for q in [-75.74,-49.81,58.67,-8.86,2.789])
    lo=tuple(math.radians(q)for q in [-110,-100,-96.8,-95,-157.2])
    hi=tuple(math.radians(q)for q in [110,100,96.8,95,162.8])
    # 0.5deg is a test fixture input, NOT an established real dock tolerance.
    tol=(math.radians(.5),)*5
    s=Snapshot(**{k:True for k in Snapshot.__dataclass_fields__ if k!='q_rad'},q_rad=reference)
    check=lambda v:startup_gate(v,reference,tol,lo,hi)
    assert check(Snapshot())==Decision.SERVICE_REQUIRED
    assert check(s)==Decision.PREPARE_HOLD
    for key in ['asset_identity_verified','inventory_matches','calibration_matches','coordinate_mapping_verified','tool_profile_matches']:
        assert check(replace(s,**{key:False}))==Decision.SERVICE_REQUIRED
    for key in ['feedback_fresh','alarm_free','boot_settings_verified','encoder_branch_unambiguous']:
        assert check(replace(s,**{key:False}))==Decision.READ_ONLY
    for key in ['mechanical_support_verified','seated','latch_closed','drive_inhibit_confirmed','dock_pose_independently_verified']:
        assert check(replace(s,**{key:False}))==Decision.RECOVERY_REQUIRED
    assert check(replace(s,q_rad=reference[:-1]))==Decision.READ_ONLY
    assert check(replace(s,q_rad=(float('nan'),)+reference[1:]))==Decision.READ_ONLY
    assert check(replace(s,q_rad=(reference[0]+2*math.pi,)+reference[1:]))==Decision.RECOVERY_REQUIRED
    assert check(replace(s,q_rad=(reference[0]+math.radians(2),)+reference[1:]))==Decision.RECOVERY_REQUIRED
    assert not work_start_gate()
    ready=dict(hold_takeover_verified=True,holding_scheme_validated=True,guards_restored=True,
      zone_clear=True,work_context_verified=True,trajectory_matches_context=True,explicit_start=True)
    assert work_start_gate(**ready)
    for key in ready:assert not work_start_gate(**{**ready,key:False})
    print('PASS: normal dock startup, calibration mismatch, missing/stale feedback, unsupported pose, encoder branch, changed context and explicit start.')
