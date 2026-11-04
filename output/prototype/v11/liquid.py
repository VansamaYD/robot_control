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
        self.exchange_rate = 0.
        # Diagnostic cumulative impacts, never another volume-ledger reservoir.
        self.coverage = {'left': 0., 'center': 0., 'right': 0.}
        self.hit_bounds = None

    @property
    def height(self):
        return self.geometry.height_at(self.pot)

    def total(self):
        return self.pot + self.ladle + self.surface + self.spill + sum(p['v'] for p in self.packets)

    def scoop_diagnostics(self, tcp, normal, target):
        angle = math.degrees(math.acos(float(np.clip(normal[2], -1, 1))))
        available = self.geometry.scoop_capacity(tcp, normal, self.height)
        reason = 'READY'
        if self.pot <= 1e-12:
            reason = 'EMPTY_POT'
        elif angle > math.degrees(math.acos(.95)):
            reason = 'LADLE_TILTED'
        elif self.geometry.connected_volume(tcp, normal, self.height) is None:
            reason = 'MOUTH_ABOVE_LIQUID'
        elif available <= 1e-12:
            reason = 'SCOOP_REGION_BLOCKED'
        elif available + 1e-9 < target:
            reason = 'INSUFFICIENT_SUBMERSION'
        elif self.pot + self.ladle + 1e-9 < target:
            reason = 'INSUFFICIENT_POT_VOLUME'
        return {'ready': reason == 'READY', 'reason': reason, 'tilt_deg': angle,
                'tcp_m': np.asarray(tcp).tolist(), 'liquid_height_m': self.height,
                'pot_m3': self.pot, 'ladle_m3': self.ladle,
                'requested_m3': target, 'geometric_capacity_m3': available,
                'suggestion': 'return_home_or_reset_simulation' if reason != 'READY' else None}

    def retained_capacity(self, normal):
        # Quasi-static hemispherical cup: volume below its lowest rim point.
        angle = math.acos(float(np.clip(normal[2], -1, 1)))
        r = SCENE['ladle']['radius_m']
        h = r * max(0., 1 - math.sin(min(math.pi/2, angle))) if angle < math.pi/2 else 0.
        return math.pi * h*h * (r - h/3)

    def advance(self, dt, tcp, normal, scoop_target=None):
        if not math.isfinite(dt) or not 0 < dt <= .1:
            raise ValueError('INVALID_LIQUID_TIMESTEP')
        tcp, normal = np.asarray(tcp), np.asarray(normal)
        # Geometry, not a recipe flag, determines water entry and equalization.
        # scoop_target is retained for SDK compatibility; the action only waits
        # for/validates a minimum volume, and cannot inject liquid.
        connected = self.geometry.connected_volume(tcp, normal, self.height)
        self.exchange_rate = 0.
        if connected is not None:
            amount = float(np.clip(connected-self.ladle, -80e-6*dt, 80e-6*dt))
            amount = min(amount, self.pot)
            self.pot -= amount; self.ladle += amount
            self.exchange_rate = amount/dt
        angle = math.acos(float(np.clip(normal[2], -1, 1)))
        excess = max(0., self.ladle - self.retained_capacity(normal))
        if excess > 0:
            flow = 80e-6 * math.sin(min(math.pi/2, angle)) * math.sqrt(excess/self.capacity)
            volume = min(excess, flow * dt)
            outlet = self.geometry.rim_lowest(tcp, normal)
            if self.geometry.submerged(outlet, self.height):
                # Immersed cup and pot communicate: do not emit an airborne jet
                # or drain a filled immersed cup down to its dry-air retention.
                pass
            else:
                self.ladle -= volume
                self.packets.append({'p': outlet, 'velocity': np.array([0., 0., -.05]), 'v': volume})
        remaining = []
        for packet in self.packets:
            start = packet['p']
            if self.geometry.submerged(start, self.height):
                self.pot += packet['v']
                continue
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
                hit = start+(end-start)*hit_chicken
                x = float(hit[0]-self.geometry.center[0])
                zone = 'left' if x < -.025 else 'right' if x > .025 else 'center'
                self.coverage[zone] += packet['v']
                xy = hit[:2]
                if self.hit_bounds is None:
                    self.hit_bounds = [xy.copy(), xy.copy()]
                else:
                    self.hit_bounds = [np.minimum(self.hit_bounds[0], xy), np.maximum(self.hit_bounds[1], xy)]
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
                'exchange_m3_s': self.exchange_rate,
                'coverage': {'zone_hit_m3': dict(self.coverage),
                    'covered_zones': sum(v >= 1e-6 for v in self.coverage.values()),
                    'bounds_xy_m': [v.tolist() for v in self.hit_bounds] if self.hit_bounds is not None else None,
                    'model': 'cumulative_impacts_not_wetted_area'},
                'packets': [{'position_m': p['p'].tolist(), 'volume_m3': p['v']} for p in self.packets]}
