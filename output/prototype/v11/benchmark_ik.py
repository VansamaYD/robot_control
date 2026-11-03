"""Offline same-samples comparison; does not contact any running simulator."""
import json
import time
from pathlib import Path
import numpy as np
from engine import Engine, Robot, arm, BASE
import ik_solver


def legacy(p, n, base, seed, guard=lambda: None, **kwargs):
    guard()
    q, pe, ne = arm.solve(p, n, base, seed=seed, restarts=0)
    return q, pe, ne, {'attempts': 1, 'iterations': 0,
                       'joint_step_rad': float(np.max(np.abs(q-seed)))}


def main():
    current = ik_solver.solve
    results = []
    try:
        for label, solver in [('legacy', legacy), ('analytic_adaptive', current)]:
            ik_solver.solve = solver
            engine = Engine(realtime=False)
            action = Robot().move_tcp('lift', [0, -.1, .195], [0, 0, 1], 12.2)
            timings = []
            for _ in range(3):
                start = time.perf_counter()
                path, gap = engine.plan(action, engine.q)
                timings.append(time.perf_counter()-start)
            p, *_ = arm.fk(path[-1], BASE)
            results.append({'solver': label, 'seconds': timings, 'samples': len(path),
                            'gap_m': gap, 'end_error_m': float(np.linalg.norm(p-action['p']))})
    finally:
        ik_solver.solve = current
    report = {'scope': 'offline canonical lift, identical collision samples; not a success-rate benchmark',
              'results': results}
    print(json.dumps(report, indent=2))
    (Path(__file__).parent/'evidence/ik-benchmark.json').write_text(json.dumps(report, indent=2)+'\n')


if __name__ == '__main__':
    main()
