"""Offline access-logic specification. NOT a safety controller or live I/O.

Inputs denote independently established conditions, not simulated approvals.
The unvalidated default explicitly prevents human-access authorization.
"""
from dataclasses import dataclass,replace


@dataclass(frozen=True)
class Inputs:
    mode: str='FAULT'
    independent_holding_validated: bool=False
    dock_position_confirmed: bool=False
    all_axes_stopped_confirmed: bool=False
    support_seated: bool=False
    spring_latch_closed: bool=False
    drive_inhibit_feedback: bool=False
    feedback_fresh: bool=False
    no_active_fault: bool=False
    physical_separation_established: bool=False
    operator_finished: bool=False
    zone_clear_confirmed: bool=False
    pot_and_points_reconfirmed: bool=False
    explicit_restart_request: bool=False


def human_access_permitted(s):
    return s.mode=='FLIP' and all([s.independent_holding_validated,s.dock_position_confirmed,
      s.all_axes_stopped_confirmed,s.support_seated,s.spring_latch_closed,s.drive_inhibit_feedback,
      s.feedback_fresh,s.no_active_fault,s.physical_separation_established])


def undock_preconditions(s):
    # This only permits beginning a supervised takeover sequence, NOT releasing
    # the latch. Latch release additionally needs drive holding confirmation.
    return s.mode=='READY_TO_UNDOCK' and all([s.independent_holding_validated,
      s.dock_position_confirmed,s.all_axes_stopped_confirmed,s.support_seated,
      s.spring_latch_closed,s.drive_inhibit_feedback,s.feedback_fresh,s.no_active_fault,
      s.physical_separation_established,s.operator_finished,s.zone_clear_confirmed,
      s.pot_and_points_reconfirmed,s.explicit_restart_request])


if __name__=='__main__':
    assert not human_access_permitted(Inputs())
    good=Inputs(mode='FLIP',independent_holding_validated=True,dock_position_confirmed=True,
       all_axes_stopped_confirmed=True,support_seated=True,spring_latch_closed=True,
       drive_inhibit_feedback=True,feedback_fresh=True,no_active_fault=True,
       physical_separation_established=True)
    assert human_access_permitted(good)
    for field in ['independent_holding_validated','dock_position_confirmed','all_axes_stopped_confirmed',
       'support_seated','spring_latch_closed','drive_inhibit_feedback','feedback_fresh','no_active_fault',
       'physical_separation_established']:
        assert not human_access_permitted(replace(good,**{field:False}))
    assert not undock_preconditions(good)
    ready=replace(good,mode='READY_TO_UNDOCK',operator_finished=True,zone_clear_confirmed=True,
                  pot_and_points_reconfirmed=True,explicit_restart_request=True)
    assert undock_preconditions(ready) and not human_access_permitted(ready)
    assert not undock_preconditions(replace(ready,explicit_restart_request=False))
    print('PASS: default-deny, each required access condition, mutually exclusive modes, explicit restart.')
