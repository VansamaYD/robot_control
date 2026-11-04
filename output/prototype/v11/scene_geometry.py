"""Shared geometry contract; SI, fixed single-pot scene, not mesh physics."""
import math
import numpy as np


SCENE = {
    'schema_version': '1.0', 'revision': 'S_RACK-geometry-3',
    'parking': {'position_m': [-.240, -.300, .195], 'above_m': [-.240, -.300, .220],
                'label': '模拟翻鸡停靠点', 'support_contact_validated': False},
    'pot': {'radius_m': .2, 'depth_m': .12, 'profile': 'spherical_cap'},
    'chicken': {'center_m': [0, .05, .120], 'radii_m': [.13, .09, .025],
                'displacement': 'pot-clipped ellipsoid union; wire displacement approximation',
                'collision': 'combined chicken/rack conservative extruded ellipse',
                'legs': [{'center_m': [s*.070, .075, .112], 'radii_m': [.026, .033, .012]} for s in [-1, 1]]},
    'rack': {'support_height_m': .095, 'rod_radius_m': .002, 'foot_radius_m': .003,
             'flow': 'porous wires; drainage through gaps, no wire adhesion physics'},
    'collision_envelope': {'center_xy_m': [0., .05], 'radii_xy_m': [.13, .09],
                           'bottom_m': 0., 'top_m': .150},
    'ladle': {'radius_m': .025, 'depth_m': .025, 'capacity_m3': 2 * math.pi * .025**3 / 3},
    'initial_liquid_height_m': .08,
    'liquid_model': 'uncalibrated volume ledger / surface residence / visible runoff packets',
}

def rack_rods():
    """Shared render geometry, displacement and conservative-containment check."""
    cx, cy, z = 0., .05, SCENE['rack']['support_height_m']-.002
    radius = SCENE['rack']['rod_radius_m']
    rods = []
    angles = np.linspace(0, 2*math.pi, 65)
    points = [[cx+.115*math.cos(a), cy+.075*math.sin(a), z] for a in angles]
    rods += [{'a': a, 'b': b, 'radius_m': radius} for a,b in zip(points, points[1:])]
    for x in [-.08, -.04, 0., .04, .08]:
        extent = .075*math.sqrt(1-(x/.115)**2)
        rods.append({'a': [x, cy-extent, z], 'b': [x, cy+extent, z], 'radius_m': radius})
    for dy in [-.035, .035]:
        extent = .115*math.sqrt(1-(dy/.075)**2)
        rods.append({'a': [-extent, cy+dy, z], 'b': [extent, cy+dy, z], 'radius_m': radius})
    curvature = (.2**2+.12**2)/(.24)
    for x in [-.075, .075]:
        for y in [.02, .08]:
            r = SCENE['rack']['foot_radius_m']
            # Flat foot meets the spherical pot at its outermost edge.
            rho = math.hypot(x,y)+r
            bottom = curvature-math.sqrt(curvature**2-rho**2)
            rods.append({'a':[x,y,bottom], 'b':[x,y,z-radius], 'radius_m':r})
    return rods


SCENE['rack']['rods'] = rack_rods()


def validate_scene():
    """New solids must be contained by the already checked legacy obstacle."""
    envelope = SCENE['collision_envelope']
    if (SCENE['pot']['radius_m'] != .2 or SCENE['pot']['depth_m'] != .12 or
            envelope != {'center_xy_m':[0.,.05], 'radii_xy_m':[.13,.09], 'bottom_m':0., 'top_m':.150}):
        raise ValueError('COLLISION_GEOMETRY_NOT_SUPPORTED')
    bottom = SCENE['chicken']['center_m'][2]-SCENE['chicken']['radii_m'][2]
    if abs(bottom-SCENE['rack']['support_height_m']) > 1e-12 or bottom <= SCENE['initial_liquid_height_m']:
        raise ValueError('COLLISION_GEOMETRY_NOT_SUPPORTED')
    for part in [SCENE['chicken']]+SCENE['chicken']['legs']:
        c, r = np.array(part['center_m']), np.array(part['radii_m'])
        offset = np.abs(c[:2]-envelope['center_xy_m'])
        ratios = r[:2]/envelope['radii_xy_m']
        # Exact for the centered main ellipse; conservative AABB corner for
        # decorative parts. Both contain every surface point, not just samples.
        bound = float(np.max(ratios)) if np.max(offset) < 1e-12 else float(np.linalg.norm((offset+r[:2])/envelope['radii_xy_m']))
        if bound > 1+1e-12 or c[2]-r[2] < bottom-1e-12 or c[2]+r[2] > .15:
            raise ValueError('COLLISION_GEOMETRY_NOT_SUPPORTED')
    for rod in SCENE['rack']['rods']:
        for p in [np.array(rod['a']), np.array(rod['b'])]:
            bound = np.linalg.norm((p[:2]-envelope['center_xy_m'])/envelope['radii_xy_m'])+rod['radius_m']/.09
            if bound > 1 or p[2]-rod['radius_m'] < 0 or p[2]+rod['radius_m'] > .15:
                raise ValueError('COLLISION_GEOMETRY_NOT_SUPPORTED')


INTEGRATION_NODES, INTEGRATION_WEIGHTS = np.polynomial.legendre.leggauss(32)


class Geometry:
    def __init__(self):
        self.radius = SCENE['pot']['radius_m']
        self.depth = SCENE['pot']['depth_m']
        self.curvature = (self.radius**2 + self.depth**2) / (2 * self.depth)
        self.center = np.array(SCENE['chicken']['center_m'])
        self.radii = np.array(SCENE['chicken']['radii_m'])
        self.parts = [(self.center,self.radii)]+[(np.array(p['center_m']),np.array(p['radii_m'])) for p in SCENE['chicken']['legs']]
        self.heights = np.linspace(0, self.depth, 481)
        # Cross-sections are clipped to the pot. A conservative collision envelope
        # must never be used as displaced-fluid volume.
        x = np.linspace(-self.radius, self.radius, 801)
        area = []
        for z in self.heights:
            r = self.pot_radius(z)
            pot_y = np.sqrt(np.maximum(0., r*r - x*x))
            intervals = []
            for center, radii in self.parts:
                slice_factor = 1-((z-center[2])/radii[2])**2
                if slice_factor <= 0: continue
                a, b = radii[:2]*math.sqrt(slice_factor)
                extent = b*np.sqrt(np.maximum(0.,1-((x-center[0])/a)**2))
                low = np.maximum(-pot_y,center[1]-extent)
                high = np.minimum(pot_y,center[1]+extent)
                high = np.where(np.abs(x-center[0]) < a, high, low)
                intervals.append((low, np.maximum(low,high)))
            merged = np.zeros_like(x)
            if intervals:
                lows, highs = np.array([i[0] for i in intervals]),np.array([i[1] for i in intervals])
                order = np.argsort(lows,axis=0)
                lows,highs = np.take_along_axis(lows,order,axis=0),np.take_along_axis(highs,order,axis=0)
                end = np.full_like(x,-np.inf)
                for low,high in zip(lows,highs):
                    merged += np.maximum(0.,high-np.maximum(low,end))
                    end = np.maximum(end,high)
            displaced = float(np.trapezoid(merged,x))
            area.append(max(0.,math.pi*r*r-displaced))
        area = np.array(area)
        dh = self.heights[1] - self.heights[0]
        self.volumes = np.r_[0., np.cumsum((area[1:] + area[:-1]) * .5 * dh)]
        # Wires are porous, not a sealed platform. Cylinder intersections are
        # a small displacement approximation, never a full tray volume.
        for rod in SCENE['rack']['rods']:
            a,b,rad = np.array(rod['a']),np.array(rod['b']),rod['radius_m']
            if abs(a[2]-b[2]) < 1e-10:
                t = np.clip(self.heights-a[2],-rad,rad)
                section = rad*rad*np.arccos(-t/rad)+t*np.sqrt(np.maximum(0.,rad*rad-t*t))
                self.volumes -= np.linalg.norm(b-a)*section
            else:
                self.volumes -= math.pi*rad*rad*np.clip(self.heights-min(a[2],b[2]),0,abs(b[2]-a[2]))

    def pot_radius(self, z):
        return math.sqrt(max(0., 2 * self.curvature * z - z*z))

    def volume_at(self, height):
        return float(np.interp(height, self.heights, self.volumes))

    def height_at(self, volume):
        return float(np.interp(volume, self.volumes, self.heights))

    def inside_chicken(self, point):
        return any(float(np.sum(((np.asarray(point)-c)/r)**2)) <= 1 for c,r in self.parts)

    def segment_chicken(self, start, end):
        start, end = np.asarray(start), np.asarray(end)
        hits = []
        for center,radii in self.parts:
            a,b = (start-center)/radii,(end-start)/radii
            aa,bb,cc = float(b@b),float(2*a@b),float(a@a-1)
            if cc <= 0: return 0.
            disc = bb*bb-4*aa*cc
            if aa <= 1e-20 or disc < 0: continue
            hits += [t for t in [(-bb-math.sqrt(disc))/(2*aa),(-bb+math.sqrt(disc))/(2*aa)] if 0<=t<=1]
        return min(hits) if hits else None

    def surface_position(self, hit, progress):
        """Illustrative gravity-side travel on the main ellipsoid, not CFD."""
        local = (np.asarray(hit)-self.center)/self.radii
        azimuth = math.atan2(local[1],local[0]) if np.linalg.norm(local[:2]) > 1e-6 else -math.pi/2
        theta0 = math.acos(float(np.clip(local[2],-1,1)))
        theta = theta0+(math.pi-.06-theta0)*float(np.clip(progress,0,1))
        return self.center+(self.radii+.0008)*np.array([math.sin(theta)*math.cos(azimuth),math.sin(theta)*math.sin(azimuth),math.cos(theta)])

    def runoff_outlet(self, hit):
        p = self.surface_position(hit,1)
        p[2] = self.center[2]-self.radii[2]-.002
        return p

    def scoop_capacity(self, tcp, normal, height):
        radius = SCENE['ladle']['radius_m']
        if np.dot(normal, [0, 0, 1]) < .95:
            return 0.
        submerged = min(radius, max(0., height - (tcp[2] - radius)))
        if submerged == 0:
            return 0.
        # The bowl is hemispherical, not a full-radius cylinder at its bottom.
        # Its bottom cross-section has zero radius; the old test rejected valid
        # manually shifted poses near the curved wall.
        for d in np.linspace(0., radius, 13):
            z = tcp[2] - radius + d
            cross_radius = math.sqrt(max(0., 2*radius*d-d*d))
            if z < 0 or math.hypot(tcp[0], tcp[1]) + cross_radius > self.pot_radius(max(.001, z)):
                return 0.
        # Conservative free-region check at the liquid height, with bowl footprint.
        point = np.array([tcp[0], tcp[1], min(height, tcp[2])])
        distance = np.linalg.norm((point[:2] - self.center[:2]) / (self.radii[:2] + radius))
        if distance <= 1:
            return 0.
        return math.pi * submerged**2 * (radius - submerged / 3)

    def submerged(self, point, height):
        return (0 <= point[2] <= height and
                math.hypot(point[0], point[1]) <= self.pot_radius(point[2]) and
                not self.inside_chicken(point))

    def rim_lowest(self, tcp, normal):
        normal = np.asarray(normal, dtype=float)
        normal = normal/max(1e-12, np.linalg.norm(normal))
        down = np.array([0., 0., -1.])
        tangent = down-normal*np.dot(down, normal)
        return np.asarray(tcp)+SCENE['ladle']['radius_m']*tangent/max(1e-12, np.linalg.norm(tangent))

    def connected_volume(self, tcp, normal, height):
        """Communicating fluid volume, or None if the mouth is dry/unsupported.

        Integrates a sphere intersected with its bowl half-space and the world
        horizontal water plane. Inverted trapped-air behavior is not modeled.
        """
        normal = np.asarray(normal, dtype=float)
        normal = normal/max(1e-12, np.linalg.norm(normal))
        if normal[2] < -1e-8 or not self.submerged(self.rim_lowest(tcp, normal), height):
            return None
        r = SCENE['ladle']['radius_m']; level = height-tcp[2]
        horizontal = float(np.linalg.norm(normal[:2]))
        if level >= r*horizontal:
            return SCENE['ladle']['capacity_m3']
        if horizontal < 1e-10:
            return None
        upper = min(r, level)
        if upper <= -r:
            return 0.
        z = (upper+r)/2*INTEGRATION_NODES+(upper-r)/2
        radius = np.sqrt(np.maximum(0., r*r-z*z))
        cut = np.clip(-normal[2]*z/horizontal, -radius, radius)
        area = radius*radius*(np.arcsin(np.clip(cut/np.maximum(radius, 1e-15), -1, 1))+np.pi/2)+cut*np.sqrt(np.maximum(0., radius*radius-cut*cut))
        return min(SCENE['ladle']['capacity_m3'], max(0., float(np.dot(INTEGRATION_WEIGHTS, area))*(upper+r)/2))
