"""Conservative-volume approximation, not CFD or a calibrated cooking model."""
import math
import numpy as np
from scene_geometry import Geometry, SCENE


class Liquid:
    def __init__(self, geometry=None):
        self.geometry = geometry or Geometry()
        self.initial = self.geometry.volume_at(SCENE['initial_liquid_height_m'])
        self.pot = self.initial
        self.ladle = 0.
        self.surface = 0.
        self.spill = 0.
        self.packets = []
        self.capacity = SCENE['ladle']['capacity_m3']

    @property
    def height(self):
        return self.geometry.height_at(self.pot)

    def total(self):
        return self.pot + self.ladle + self.surface + self.spill + sum(p['v'] for p in self.packets)

    def advance(self, dt, tcp, normal, scoop_target=None):
        if not math.isfinite(dt) or not 0 < dt <= .1:
            raise ValueError('INVALID_LIQUID_TIMESTEP')
        tcp, normal = np.asarray(tcp), np.asarray(normal)
        if scoop_target is not None:
            allowed = self.geometry.scoop_capacity(tcp, normal, self.height)
            amount = max(0., min(scoop_target, allowed, self.capacity) - self.ladle)
            amount = min(amount, self.pot, 80e-6 * dt)
            self.pot -= amount
            self.ladle += amount
        angle = math.acos(float(np.clip(normal[2], -1, 1)))
        if angle > math.radians(25) and self.ladle > 0:
            flow = 40e-6 * max(0., math.sin(min(math.pi/2, angle - math.radians(25))))
            volume = min(self.ladle, flow * dt)
            self.ladle -= volume
            self.packets.append({'p': tcp.copy(), 'velocity': np.array([0., 0., -.05]), 'v': volume})
        remaining = []
        for packet in self.packets:
            start = packet['p']
            end = start + packet['velocity'] * dt + np.array([0., 0., -.5 * 9.81 * dt**2])
            packet['velocity'][2] -= 9.81 * dt
            hit_chicken = self.geometry.segment_chicken(start, end)
            hit_surface = None
            if start[2] >= self.height >= end[2] and end[2] < start[2]:
                t = (start[2] - self.height) / (start[2] - end[2])
                point = start + (end - start)*t
                if math.hypot(point[0], point[1]) <= self.geometry.pot_radius(self.height):
                    hit_surface = t
            if hit_chicken is not None and (hit_surface is None or hit_chicken <= hit_surface):
                self.surface += packet['v']
            elif hit_surface is not None:
                self.pot += packet['v']
            elif end[2] < 0:
                self.spill += packet['v']
            else:
                packet['p'] = end
                remaining.append(packet)
        self.packets = remaining
        returned = self.surface * (1 - math.exp(-dt / 2))
        self.surface -= returned
        self.pot += returned
        excess = max(0., self.pot - self.geometry.volumes[-1])
        self.pot -= excess
        self.spill += excess
        if not all(math.isfinite(v) and v >= -1e-14 for v in [self.pot, self.ladle, self.surface, self.spill]):
            raise RuntimeError('LIQUID_LEDGER_INVALID')
        if abs(self.total() - self.initial) > max(1e-12, self.initial * 1e-9):
            raise RuntimeError('LIQUID_CONSERVATION_FAILED')

    def snapshot(self):
        return {'mode': 'uncalibrated_volume_approximation', 'height_m': self.height,
                'capacity_m3': self.capacity, 'pot_m3': self.pot, 'ladle_m3': self.ladle,
                'air_m3': sum(p['v'] for p in self.packets), 'surface_m3': self.surface,
                'spill_m3': self.spill, 'initial_m3': self.initial,
                'balance_error_m3': self.total() - self.initial,
                'packets': [{'position_m': p['p'].tolist(), 'volume_m3': p['v']} for p in self.packets]}
