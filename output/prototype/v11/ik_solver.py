"""Bounded, deterministic local IK; candidates still need full path validation."""
import numpy as np
import arm_model as arm

POSITION_TOL = .0002
NORMAL_TOL = .002


def feature_jacobian(q, base):
    p, n, origins, axes, *_ = arm.fk(q, base)
    jac = np.vstack((10 * np.cross(axes, p-origins).T,
                     np.cross(axes, n).T))
    return np.r_[10*p, n], jac


def solve(p, n, base, seed, guard=lambda: None, fallback=True, max_jump=None):
    """Analytic Jacobian / adaptive damping; nearby seeds only on failure.

    A converged alternate branch is never accepted if it exceeds max_jump.
    No collision or fluid acceptance happens here.
    """
    target = np.r_[10*np.asarray(p), n]
    seed = np.asarray(seed)
    best = None
    attempts, iterations = 0, 0
    offsets = [np.zeros(5)]
    if fallback:
        offsets += [np.array([0, a, -a, 0, 0]) for a in (.04, -.04)]
        offsets += [np.array([0, 0, a, -a, 0]) for a in (.04, -.04)]
    for offset in offsets:
        guard()
        attempts += 1
        q = np.clip(seed+offset, arm.LOW+1e-6, arm.HIGH-1e-6)
        damping = 1e-5
        for _ in range(70):
            guard()
            iterations += 1
            f, jac = feature_jacobian(q, base)
            error = target-f
            if np.linalg.norm(error) < 1e-6:
                break
            accepted = False
            for _ in range(4):
                guard()
                step = np.linalg.solve(jac.T@jac + damping*np.eye(5), jac.T@error)
                step *= min(1., .18/max(np.linalg.norm(step), 1e-12))
                for scale in (1., .5, .25, .1):
                    trial = np.clip(q+scale*step, arm.LOW+1e-6, arm.HIGH-1e-6)
                    tp, tn, *_ = arm.fk(trial, base)
                    if np.linalg.norm(target-np.r_[10*tp, tn]) < np.linalg.norm(error)-1e-14:
                        q = trial
                        damping = max(1e-8, damping*.3)
                        accepted = True
                        break
                if accepted:
                    break
                damping = min(1e3, damping*10)
            if not accepted:
                break
        pos, normal, *_ = arm.fk(q, base)
        pe = float(np.linalg.norm(pos-p))
        ne = float(np.arccos(np.clip(normal@np.asarray(n), -1, 1)))
        continuous = max_jump is None or np.max(np.abs(q-seed)) <= max_jump+1e-12
        valid = pe <= POSITION_TOL and ne <= NORMAL_TOL and continuous
        score = pe*10+ne + (0 if continuous else 100)
        if best is None or score < best[0]:
            best = (score, q.copy(), pe, ne)
        if valid:
            # A valid solution wins even if an invalid result had lower mixed error.
            best = (score, q.copy(), pe, ne)
            break
    _, q, pe, ne = best
    return q, pe, ne, {'attempts': attempts, 'iterations': iterations,
                       'solver': 'analytic_adaptive_dls',
                       'position_error_m': pe, 'normal_error_rad': ne,
                       'joint_step_rad': float(np.max(np.abs(q-seed))),
                       'limit_margin_rad': float(np.min(np.minimum(q-arm.LOW, arm.HIGH-q)))}
