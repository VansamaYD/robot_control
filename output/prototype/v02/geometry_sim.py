"""Conservative static ladle-clearance screening; units are millimetres.

Not a robot, fluid, heat-transfer, chicken-deformation, or handle-collision model.
Run: python3 output/prototype/v02/geometry_sim.py
"""
from dataclasses import asdict, dataclass
from pathlib import Path
import json
import math
import numpy as np


@dataclass
class Scenario:
    name: str
    diameter: float = 360
    depth: float = 115
    liquid_height: float = 65
    chicken_length: float = 260
    chicken_width: float = 180
    chicken_offset_y: float = 0
    ladle_diameter: float = 65
    ladle_depth: float = 28
    clearance: float = 7.5
    lip_submersion: float = 5


def wok_radius(z, s):
    radius = ((s.diameter / 2) ** 2 + s.depth ** 2) / (2 * s.depth)
    return np.sqrt(np.maximum(0, 2 * radius * z - z ** 2))


def ellipse_distance(x, y, a, b):
    """Exact exterior distance by monotone Lagrange-multiplier bisection.

    Interior values are -1: enough to reject these candidate bowl centres.
    """
    x, y = np.abs(x), np.abs(y)
    outside = (x / a) ** 2 + (y / b) ** 2 >= 1
    low = np.zeros_like(x)
    high = np.full_like(x, max(a, b) * (np.max(x) + np.max(y) + a + b))
    for _ in range(48):
        mid = (low + high) / 2
        equation = (a * x / (mid + a*a))**2 + (b * y / (mid + b*b))**2
        low = np.where(equation > 1, mid, low)
        high = np.where(equation > 1, high, mid)
    t = (low + high) / 2
    distance = np.hypot(x - a*a*x / (t+a*a), y - b*b*y / (t+b*b))
    return np.where(outside, distance, -1)


def evaluate(s, grid_step=2):
    # Spherical-cap ladle, horizontal, lip 5 mm below the liquid surface.
    bowl_radius = s.ladle_diameter / 2
    cap_radius = (bowl_radius**2 + s.ladle_depth**2) / (2*s.ladle_depth)
    u = np.linspace(0, s.ladle_depth, 121)
    z = s.liquid_height - s.lip_submersion - s.ladle_depth + u
    cross_section = np.sqrt(np.maximum(0, 2*cap_radius*u-u*u))
    if np.min(z) < s.clearance:
        wall_limit = -1.0
    else:
        wall_limit = float(np.min(wok_radius(z, s)-cross_section-s.clearance))
    grid = np.arange(-s.diameter/2, s.diameter/2+0.01, grid_step)
    x, y = np.meshgrid(grid, grid)
    distance = ellipse_distance(x, y-s.chicken_offset_y,
                                s.chicken_length/2, s.chicken_width/2)
    # Chicken projected envelope is treated as a vertical extrusion: conservative.
    scores = np.minimum(wall_limit - np.hypot(x,y),
                        distance - bowl_radius - s.clearance)
    valid = scores >= 0
    index = np.unravel_index(np.argmax(scores), scores.shape)
    best = [float(x[index]), float(y[index])]
    result = dict(asdict(s), liquid_diameter=round(float(2*wok_radius(s.liquid_height,s)),1),
                  bowl_center_radial_limit=round(wall_limit,2),
                  feasible_grid_points=int(np.sum(valid)),
                  feasible_area_mm2=round(float(np.sum(valid)*grid_step**2),1),
                  best_center_xy=best, best_extra_clearance_mm=round(float(scores[index]),2),
                  feasible=bool(np.any(valid)), grid_step_mm=grid_step)
    points = np.column_stack((x[valid],y[valid])).tolist()
    return result, points


SCENARIOS = [
    Scenario('36cm / 鸡居中'),
    Scenario('36cm / 鸡偏移35mm', chicken_offset_y=35),
    Scenario('40cm / 鸡居中', diameter=400, depth=120, liquid_height=70),
    Scenario('40cm / 鸡偏移35mm', diameter=400, depth=120, liquid_height=70, chicken_offset_y=35),
    Scenario('36cm / 偏移 / 低液位45mm', chicken_offset_y=35, liquid_height=45),
    Scenario('36cm / 居中 / 小勺50mm', ladle_diameter=50, ladle_depth=25),
    Scenario('36cm / 偏移 / 液位80mm', chicken_offset_y=35, liquid_height=80),
    Scenario('36cm / 偏移 / 小勺50mm', chicken_offset_y=35, ladle_diameter=50, ladle_depth=25),
    Scenario('40cm / 偏移 / 液位80mm', diameter=400, depth=120, liquid_height=80, chicken_offset_y=35),
]


def verify_math():
    # Independent known geometries, rather than snapshots of current results.
    s = SCENARIOS[0]
    assert abs(float(wok_radius(s.depth,s))-s.diameter/2)<1e-9
    assert float(wok_radius(0,s))==0
    ds = ellipse_distance(np.array([150.,0.,0.]), np.array([0.,120.,0.]),130,90)
    assert np.allclose(ds[:2],[20,30],atol=1e-7)
    assert ds[2]<0


if __name__=='__main__':
    verify_math()
    base=Path(__file__).resolve().parent
    results=[]
    for s in SCENARIOS:
        result, points=evaluate(s)
        results.append(result)
        print(json.dumps(result,ensure_ascii=False))
    (base/'geometry_results.json').write_text(json.dumps({
        'model':'Static spherical wok + spherical-cap ladle + extruded elliptical chicken envelope',
        'scope':'Ladle bowl only; no handle, joints, fluid, heat or path verification',
        'scenarios':results},ensure_ascii=False,indent=2),encoding='utf-8')
