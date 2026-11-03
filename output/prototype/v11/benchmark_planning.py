"""Repeatable old/batched collision comparison on the same 610-point lift."""
from pathlib import Path
import cProfile
import io
import json
import pstats
import time
import engine
from collision_model import clearance as batched
from trajectory_sim import clearance as legacy
from presets import LIFT, move


def main():
    result = {}
    original = engine.clearance
    try:
        for name, checker in [('legacy', legacy), ('batched', batched)]:
            engine.clearance = checker
            simulation = engine.Engine(realtime=False)
            profile = cProfile.Profile()
            profile.enable(); started = time.perf_counter()
            path, gap = simulation.plan(move('benchmark lift', LIFT, 12.2), simulation.q)
            elapsed = time.perf_counter()-started; profile.disable()
            output = io.StringIO()
            pstats.Stats(profile, stream=output).sort_stats('cumtime').print_stats(10)
            result[name] = {'wall_s': elapsed, 'samples': len(path), 'minimum_clearance_m': gap,
                            'profile': output.getvalue()}
    finally:
        engine.clearance = original
    result['ratio'] = result['legacy']['wall_s']/result['batched']['wall_s']
    (Path(__file__).parent/'evidence/planning-comparison.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({name: {k: v for k, v in value.items() if k != 'profile'} if isinstance(value, dict) else value for name, value in result.items()}, indent=2))


if __name__ == '__main__': main()
