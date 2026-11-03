"""Shared geometry contract; SI, fixed single-pot scene, not mesh physics."""
import math
import numpy as np


SCENE = {
    'schema_version': '1.0', 'revision': 'S_BASE-geometry-1',
    'pot': {'radius_m': .2, 'depth_m': .12, 'profile': 'spherical_cap'},
    'chicken': {'center_m': [0, .05, .075], 'radii_m': [.13, .09, .075],
                'displacement': 'pot-clipped ellipsoid, numerical quadrature',
                'collision': 'legacy conservative extruded ellipse'},
    'ladle': {'radius_m': .025, 'depth_m': .025, 'capacity_m3': 2 * math.pi * .025**3 / 3},
    'initial_liquid_height_m': .08,
    'liquid_model': 'uncalibrated volume ledger / ballistic packets / exponential return',
}


class Geometry:
    def __init__(self):
        self.radius = SCENE['pot']['radius_m']
        self.depth = SCENE['pot']['depth_m']
        self.curvature = (self.radius**2 + self.depth**2) / (2 * self.depth)
        self.center = np.array(SCENE['chicken']['center_m'])
        self.radii = np.array(SCENE['chicken']['radii_m'])
        self.heights = np.linspace(0, self.depth, 481)
        # Cross-sections are clipped to the pot. A conservative collision envelope
        # must never be used as displaced-fluid volume.
        x = np.linspace(-self.radius, self.radius, 801)
        area = []
        for z in self.heights:
            r = self.pot_radius(z)
            pot_y = np.sqrt(np.maximum(0., r*r - x*x))
            slice_factor = 1 - ((z - self.center[2]) / self.radii[2])**2
            if slice_factor <= 0:
                displaced = 0.
            else:
                a, b = self.radii[:2] * math.sqrt(slice_factor)
                chicken_y = b * np.sqrt(np.maximum(0., 1 - ((x - self.center[0]) / a)**2))
                overlap = np.maximum(0., np.minimum(pot_y, self.center[1] + chicken_y)
                                     - np.maximum(-pot_y, self.center[1] - chicken_y))
                overlap[np.abs(x - self.center[0]) >= a] = 0
                overlap[np.abs(x) >= r] = 0
                displaced = float(np.trapezoid(overlap, x))
            area.append(max(0., math.pi*r*r - displaced))
        area = np.array(area)
        dh = self.heights[1] - self.heights[0]
        self.volumes = np.r_[0., np.cumsum((area[1:] + area[:-1]) * .5 * dh)]

    def pot_radius(self, z):
        return math.sqrt(max(0., 2 * self.curvature * z - z*z))

    def volume_at(self, height):
        return float(np.interp(height, self.heights, self.volumes))

    def height_at(self, volume):
        return float(np.interp(volume, self.volumes, self.heights))

    def inside_chicken(self, point):
        return float(np.sum(((np.asarray(point) - self.center) / self.radii)**2)) <= 1

    def segment_chicken(self, start, end):
        start, end = np.asarray(start), np.asarray(end)
        a = (start - self.center) / self.radii
        b = (end - start) / self.radii
        aa, bb, cc = float(b@b), float(2*a@b), float(a@a - 1)
        if cc <= 0:
            return 0.
        discriminant = bb*bb - 4*aa*cc
        if aa <= 1e-20 or discriminant < 0:
            return None
        roots = [(-bb - math.sqrt(discriminant)) / (2*aa), (-bb + math.sqrt(discriminant)) / (2*aa)]
        return next((t for t in sorted(roots) if 0 <= t <= 1), None)

    def scoop_capacity(self, tcp, normal, height):
        radius = SCENE['ladle']['radius_m']
        if np.dot(normal, [0, 0, 1]) < .95:
            return 0.
        submerged = min(radius, max(0., height - (tcp[2] - radius)))
        if submerged == 0:
            return 0.
        if math.hypot(tcp[0], tcp[1]) + radius > self.pot_radius(max(.001, tcp[2] - radius)):
            return 0.
        # Conservative free-region check at the liquid height, with bowl footprint.
        point = np.array([tcp[0], tcp[1], min(height, tcp[2])])
        distance = np.linalg.norm((point[:2] - self.center[:2]) / (self.radii[:2] + radius))
        if distance <= 1:
            return 0.
        return math.pi * submerged**2 * (radius - submerged / 3)
