"""Same sampled envelopes as v03, batched without removing collision samples."""
import numpy as np
from arm_model import fk
from trajectory_sim import BASE, CONFIG, pot_distance, chicken_distance, samples

theta, phi = np.meshgrid(np.linspace(0, np.pi/2, 13), np.linspace(0, 2*np.pi, 32, endpoint=False), indexing='ij')
BOWL_LOCAL = np.column_stack([-.025*np.cos(theta).ravel(),
                              (.025*np.sin(theta)*np.cos(phi)).ravel(),
                              (.025*np.sin(theta)*np.sin(phi)).ravel()])


def clearance(q):
    p, n, origins, axes, frames = fk(q, BASE)
    chains = [(f'arm_{i+1}', samples(a, b), CONFIG['arm_capsule_radius_mm'][i]/1000)
              for i, (a, b) in enumerate(zip(origins[:-1], origins[1:]))]
    T = frames[-1]; R = T[:3, :3]; origin = T[:3, 3]
    bend = origin + R @ np.array([0., 0., -.09])
    chains += [('stem_upper', samples(origin, bend), .004),
               ('stem_drop', samples(bend, p), .004), ('bowl', BOWL_LOCAL @ R.T + p, 0.)]
    points = np.concatenate([chain[1] for chain in chains])
    distance = np.minimum(pot_distance(points), chicken_distance(points))
    closest, label, offset = float('inf'), '', 0
    for name, points, radius in chains:
        gap = float(np.min(distance[offset:offset+len(points)])) - radius - .0075
        offset += len(points)
        if gap < closest: closest, label = gap, name
    for i in range(4):
        for j in range(i+2, 4):
            a, b = chains[i], chains[j]
            gap = float(np.min(np.linalg.norm(a[1][:, None, :]-b[1][None, :, :], axis=2)))-a[2]-b[2]-.0075
            if gap < closest: closest, label = gap, f'self_{i+1}_{j+1}'
    for i in range(3):
        for j in (4, 5, 6):
            a, b = chains[i], chains[j]
            gap = float(np.min(np.linalg.norm(a[1][:, None, :]-b[1][None, :, :], axis=2)))-a[2]-b[2]-.0075
            if gap < closest: closest, label = gap, f'self_{a[0]}_{b[0]}'
    return closest-1e-12, label, p, n, origins, frames, bend
