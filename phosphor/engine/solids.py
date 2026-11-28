"""Platonic solids, polyhedra, polygons, curved wireframes, configurable graphics, and closed drawing routes."""
from dataclasses import dataclass, replace
from functools import lru_cache
import math
import numpy as np

PLATONIC_SOLIDS = ('Cube', 'Tetrahedron', 'Octahedron', 'Dodecahedron', 'Icosahedron')
EXTRA_SOLIDS = (
    'Torus',
    'Trefoil knot',
    'Cinquefoil knot',
    'Möbius strip',
    'Hypercube (Tesseract)',
    'Geodesic sphere',
    'Hexagonal prism',
    'Square pyramid',
)
POLYGONS = {'Triangle (2D)': 3, 'Square (2D)': 4, 'Pentagon (2D)': 5}
EXTRA_2D = (
    'Hexagon (2D)',
    'Star (5-point)',
    'Star (8-point)',
    'Gear (12-tooth)',
    'Spiral',
    'Radar reticle',
)
CURVES = ('Tilting circle', 'Octant sphere')
EXTRA_CURVES = (
    'Lissajous (3:2)',
    'Harmonograph',
    'Helix',
    'Vortex cone',
    'Clock face',
)
SOLIDS = (*PLATONIC_SOLIDS, *EXTRA_SOLIDS, *POLYGONS, *EXTRA_2D, *CURVES, *EXTRA_CURVES)

# Reorganized taxonomy for Scene Builder, Add/Replace menus, Library Browser & Quick Add.
OBJECT_GROUPS = (
    ('2D shapes', (
        ('Polygons', ('Triangle (2D)', 'Square (2D)', 'Pentagon (2D)', 'Hexagon (2D)')),
        ('Stars & graphics', ('Star (5-point)', 'Star (8-point)', 'Gear (12-tooth)', 'Radar reticle', 'Clock face')),
    )),
    ('3D solids', (
        ('Platonic', PLATONIC_SOLIDS),
        ('Polyhedra & spheres', ('Hexagonal prism', 'Square pyramid', 'Geodesic sphere', 'Octant sphere')),
        ('Surfaces & 4D', ('Torus', 'Möbius strip', 'Hypercube (Tesseract)')),
    )),
    ('Curves & knots', (
        ('2D & harmonic', ('Tilting circle', 'Spiral', 'Lissajous (3:2)', 'Harmonograph')),
        ('3D paths & knots', ('Helix', 'Vortex cone', 'Trefoil knot', 'Cinquefoil knot')),
    )),
)

STAR_SOLIDS = frozenset(('Star (5-point)', 'Star (8-point)'))
GEAR_SOLIDS = frozenset(('Gear (12-tooth)',))
RADAR_SOLID = 'Radar reticle'
CLOCK_SOLID = 'Clock face'
PARAMETRIC_SOLIDS = frozenset((*STAR_SOLIDS, *GEAR_SOLIDS, RADAR_SOLID, CLOCK_SOLID))
ANIMATED_SOLIDS = frozenset((RADAR_SOLID, CLOCK_SOLID))
LOW_POLY_SOLIDS = frozenset((
    *PLATONIC_SOLIDS, 'Hexagonal prism', 'Square pyramid',
    *POLYGONS, 'Hexagon (2D)', 'Hypercube (Tesseract)',
))


@dataclass(frozen=True)
class ShapeParams:
    """Optional per-object parameters for configurable/animated built-in shapes (Star, Gear, Radar, Clock)."""
    points: int = 5              # Star: 3..32 points
    inner_radius: float = 0.42   # Star: 0.15..0.90 inner-to-outer radius ratio
    teeth: int = 12              # Gear: 4..48 teeth
    tooth_depth: float = 0.24    # Gear: 0.08..0.45 radial tooth depth
    sweep_speed: float = 0.35    # Radar reticle: revolutions/sec (-4..4, positive = clockwise)
    clock_speed: float = 60.0    # Clock face: time multiplier (-3600..3600; 60 = 1 min/sec)
    start_hour: float = 10.0     # Clock face: starting hour (0..12)
    start_minute: float = 10.0   # Clock face: starting minute (0..60)

    def document(self, solid=None):
        """Serialize only the fields relevant to `solid` (or all fields if solid is None)."""
        if solid in STAR_SOLIDS:
            return dict(points=int(self.points), inner_radius=float(self.inner_radius))
        if solid in GEAR_SOLIDS:
            return dict(teeth=int(self.teeth), tooth_depth=float(self.tooth_depth))
        if solid == RADAR_SOLID:
            return dict(sweep_speed=float(self.sweep_speed))
        if solid == CLOCK_SOLID:
            return dict(clock_speed=float(self.clock_speed), start_hour=float(self.start_hour),
                        start_minute=float(self.start_minute))
        return dict(
            points=int(self.points), inner_radius=float(self.inner_radius),
            teeth=int(self.teeth), tooth_depth=float(self.tooth_depth),
            sweep_speed=float(self.sweep_speed), clock_speed=float(self.clock_speed),
            start_hour=float(self.start_hour), start_minute=float(self.start_minute),
        )

    @classmethod
    def read(cls, value, solid=None):
        if not isinstance(value, dict):
            raise ValueError('Shape parameters must be an object.')
        allowed = {'points', 'inner_radius', 'teeth', 'tooth_depth',
                   'sweep_speed', 'clock_speed', 'start_hour', 'start_minute'}
        if not set(value) <= allowed:
            raise ValueError('Unsupported shape parameter fields.')
        params = replace(default_shape_params(solid), **value)
        validate_shape_params(params)
        return params


def default_shape_params(solid=None):
    """Default ShapeParams matching each built-in configurable shape."""
    if solid == 'Star (8-point)':
        return ShapeParams(points=8, inner_radius=0.48)
    return ShapeParams()


def validate_shape_params(params):
    if not isinstance(params, ShapeParams):
        raise ValueError('Invalid shape parameters.')
    if type(params.points) is not int or not 3 <= params.points <= 32:
        raise ValueError('Star points must be a whole number from 3 to 32.')
    if not (isinstance(params.inner_radius, (int, float)) and not isinstance(params.inner_radius, bool)
            and math.isfinite(params.inner_radius) and 0.15 <= params.inner_radius <= 0.90):
        raise ValueError('Star inner radius must be between 0.15 and 0.90.')
    if type(params.teeth) is not int or not 4 <= params.teeth <= 48:
        raise ValueError('Gear teeth must be a whole number from 4 to 48.')
    if not (isinstance(params.tooth_depth, (int, float)) and not isinstance(params.tooth_depth, bool)
            and math.isfinite(params.tooth_depth) and 0.08 <= params.tooth_depth <= 0.45):
        raise ValueError('Gear tooth depth must be between 0.08 and 0.45.')
    if not (isinstance(params.sweep_speed, (int, float)) and not isinstance(params.sweep_speed, bool)
            and math.isfinite(params.sweep_speed) and -4.0 <= params.sweep_speed <= 4.0):
        raise ValueError('Radar sweep speed must be between -4 and 4 rev/s.')
    if not (isinstance(params.clock_speed, (int, float)) and not isinstance(params.clock_speed, bool)
            and math.isfinite(params.clock_speed) and -3600.0 <= params.clock_speed <= 3600.0):
        raise ValueError('Clock speed must be between -3600 and 3600×.')
    if not (isinstance(params.start_hour, (int, float)) and not isinstance(params.start_hour, bool)
            and math.isfinite(params.start_hour) and 0.0 <= params.start_hour <= 12.0):
        raise ValueError('Clock start hour must be between 0 and 12.')
    if not (isinstance(params.start_minute, (int, float)) and not isinstance(params.start_minute, bool)
            and math.isfinite(params.start_minute) and 0.0 <= params.start_minute <= 60.0):
        raise ValueError('Clock start minute must be between 0 and 60.')

# Closed single-loop objects whose vertex sequence 0..N-1..0 is already the optimal route.
_CLOSED_LOOPS = frozenset((
    *POLYGONS,
    'Hexagon (2D)',
    'Star (5-point)',
    'Star (8-point)',
    'Gear (12-tooth)',
    'Trefoil knot',
    'Cinquefoil knot',
    'Lissajous (3:2)',
    'Harmonograph',
))
# Open-ended smooth paths traced forward then back (or closed smoothly).
_RETRACE_PATHS = frozenset(('Spiral', 'Helix', 'Vortex cone'))


def ring_graph(planes, samples):
    """Merge shared axis poles so rings connect along their actual intersections."""
    vertices, edges, lookup = [], [], {}
    angle = np.arange(samples) * 2 * np.pi / samples
    for first, second in planes:
        ring = []
        for c, s in zip(np.cos(angle), np.sin(angle)):
            point = np.zeros(3)
            point[first], point[second] = c, s
            key = tuple(np.round(point, 12))
            if key not in lookup:
                lookup[key] = len(vertices)
                vertices.append(point)
            ring.append(lookup[key])
        edges.extend(tuple(sorted((ring[i], ring[(i+1) % samples]))) for i in range(samples))
    return np.array(vertices), sorted(set(edges))


def edges_for(vertices):
    distances = np.linalg.norm(vertices[:, None] - vertices[None, :], axis=2)
    edge_length = distances[distances > 1e-8].min()
    return [(i, j) for i in range(len(vertices)) for j in range(i + 1, len(vertices))
            if np.isclose(distances[i, j], edge_length)]


def _normalize(vertices):
    vertices = np.asarray(vertices, dtype=float)
    radius = np.linalg.norm(vertices, axis=1).max()
    return vertices / radius if radius > 1e-12 else vertices


def _torus_wireframe(major_segs=16, minor_segs=10, R=0.68, r=0.32):
    u = np.arange(major_segs) * 2 * np.pi / major_segs
    v = np.arange(minor_segs) * 2 * np.pi / minor_segs
    verts = []
    for ui in u:
        cu, su = np.cos(ui), np.sin(ui)
        for vj in v:
            cv, sv = np.cos(vj), np.sin(vj)
            verts.append([(R + r * cv) * cu, (R + r * cv) * su, r * sv])
    edges = []
    for i in range(major_segs):
        ni = (i + 1) % major_segs
        for j in range(minor_segs):
            nj = (j + 1) % minor_segs
            a = i * minor_segs + j
            edges.append((min(a, ni * minor_segs + j), max(a, ni * minor_segs + j)))
            edges.append((min(a, i * minor_segs + nj), max(a, i * minor_segs + nj)))
    return _normalize(verts), sorted(set(edges))


def _torus_knot(p, q, samples=256):
    t = np.arange(samples) * 2 * np.pi / samples
    r = np.cos(q * t) + 2.0
    x = r * np.cos(p * t)
    y = r * np.sin(p * t)
    z = -np.sin(q * t)
    verts = _normalize(np.column_stack((x, y, z)))
    edges = [(i, (i + 1) % samples) for i in range(samples)]
    return verts, edges


def _mobius_strip(u_segs=32, width=0.34):
    # Two boundary loops + periodic rungs forming a single connected wireframe graph.
    u = np.arange(u_segs) * 2 * np.pi / u_segs
    verts = []
    for side in (-1.0, 1.0):
        for ui in u:
            half = 0.5 * ui
            r = 1.0 + side * width * np.cos(half)
            verts.append([r * np.cos(ui), r * np.sin(ui), side * width * np.sin(half)])
    edges = []
    for i in range(u_segs):
        ni = (i + 1) % u_segs
        # At i == u_segs - 1, side -1 connects to side +1 (the half-twist!).
        a0, b0 = i, (u_segs if i == u_segs - 1 else ni)
        a1, b1 = u_segs + i, (0 if i == u_segs - 1 else u_segs + ni)
        edges.append((min(a0, b0), max(a0, b0)))
        edges.append((min(a1, b1), max(a1, b1)))
        if i % 4 == 0:
            edges.append((i, u_segs + i))
    return _normalize(verts), sorted(set(edges))


def _tesseract():
    # 16 vertices of a 4D hypercube projected into 3D (inner cube scaled 0.5 inside outer cube).
    verts = []
    for i in range(16):
        x = 1.0 if i & 1 else -1.0
        y = 1.0 if i & 2 else -1.0
        z = 1.0 if i & 4 else -1.0
        w = 1.0 if i & 8 else -1.0
        proj = 1.0 / (2.2 - 0.8 * w)
        verts.append([x * proj, y * proj, z * proj])
    edges = []
    for i in range(16):
        for bit in range(4):
            j = i ^ (1 << bit)
            if i < j:
                edges.append((i, j))
    return _normalize(verts), edges


def _geodesic_sphere():
    # Frequency-2 subdivided icosahedron (42 vertices, 120 edges).
    phi = (1.0 + np.sqrt(5.0)) / 2.0
    ring = np.array([[0., a, b * phi] for a in (-1., 1.) for b in (-1., 1.)])
    base = np.concatenate([np.roll(ring, s, axis=1) for s in range(3)])
    base = base / np.linalg.norm(base, axis=1, keepdims=True)
    # Build icosahedron triangular faces from nearest-neighbor edge length.
    dists = np.linalg.norm(base[:, None] - base[None, :], axis=2)
    elen = dists[dists > 1e-6].min()
    adj = {i: {j for j in range(len(base)) if abs(dists[i, j] - elen) < 1e-4} for i in range(len(base))}
    faces = set()
    for i in range(len(base)):
        for j in adj[i]:
            if j > i:
                for k in adj[i] & adj[j]:
                    if k > j:
                        faces.add((i, j, k))
    verts = list(base)
    mid_cache = {}
    def midpoint(a, b):
        key = (min(a, b), max(a, b))
        if key not in mid_cache:
            m = verts[a] + verts[b]
            m = m / np.linalg.norm(m)
            mid_cache[key] = len(verts)
            verts.append(m)
        return mid_cache[key]
    edges = set()
    for a, b, c in faces:
        ab, bc, ca = midpoint(a, b), midpoint(b, c), midpoint(c, a)
        for tri in ((a, ab, ca), (b, bc, ab), (c, ca, bc), (ab, bc, ca)):
            for u, v in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
                edges.add((min(u, v), max(u, v)))
    return _normalize(verts), sorted(edges)


def _prism(sides=6, height=1.2):
    ang = np.arange(sides) * 2 * np.pi / sides
    top = np.column_stack((np.cos(ang), np.sin(ang), np.full(sides, height * 0.5)))
    bot = np.column_stack((np.cos(ang), np.sin(ang), np.full(sides, -height * 0.5)))
    verts = _normalize(np.vstack((top, bot)))
    edges = []
    for i in range(sides):
        ni = (i + 1) % sides
        edges.append((min(i, ni), max(i, ni)))
        edges.append((min(sides + i, sides + ni), max(sides + i, sides + ni)))
        edges.append((i, sides + i))
    return verts, sorted(set(edges))


def _pyramid(sides=4):
    ang = np.arange(sides) * 2 * np.pi / sides + np.pi / 4
    base = np.column_stack((np.cos(ang), np.sin(ang), np.full(sides, -0.55)))
    apex = np.array([[0.0, 0.0, 0.85]])
    verts = _normalize(np.vstack((base, apex)))
    edges = []
    for i in range(sides):
        ni = (i + 1) % sides
        edges.append((min(i, ni), max(i, ni)))
        edges.append((i, sides))
    return verts, sorted(set(edges))


def _star(points=5, inner=0.42):
    count = points * 2
    ang = np.arange(count) * np.pi / points + np.pi / 2
    rad = np.where(np.arange(count) % 2 == 0, 1.0, inner)
    verts = np.column_stack((rad * np.cos(ang), rad * np.sin(ang), np.zeros(count)))
    edges = [(i, (i + 1) % count) for i in range(count)]
    return verts, edges


def _gear(teeth=12, inner=0.76, outer=1.0):
    verts = []
    step = 2 * np.pi / teeth
    for i in range(teeth):
        a0 = i * step
        for frac, r in ((0.0, inner), (0.18, inner), (0.28, outer), (0.62, outer), (0.72, inner)):
            a = a0 + frac * step
            verts.append([r * np.cos(a), r * np.sin(a), 0.0])
    n = len(verts)
    return np.asarray(verts, dtype=float), [(i, (i + 1) % n) for i in range(n)]


def _spiral(turns=4.0, samples=192):
    t = np.linspace(0.0, turns * 2 * np.pi, samples)
    r = np.linspace(0.04, 1.0, samples)
    verts = np.column_stack((r * np.cos(t), r * np.sin(t), np.zeros(samples)))
    edges = [(i, i + 1) for i in range(samples - 1)]
    return verts, edges


def _helix(turns=4.0, samples=192, cone=False):
    t = np.linspace(0.0, turns * 2 * np.pi, samples)
    r = np.linspace(0.08, 1.0, samples) if cone else np.full(samples, 0.72)
    z = np.linspace(-1.0, 1.0, samples)
    verts = _normalize(np.column_stack((r * np.cos(t), r * np.sin(t), z)))
    edges = [(i, i + 1) for i in range(samples - 1)]
    return verts, edges


def _lissajous(a=3, b=2, delta=np.pi / 2, samples=256):
    t = np.arange(samples) * 2 * np.pi / samples
    verts = _normalize(np.column_stack((np.sin(a * t + delta), np.sin(b * t), 0.35 * np.sin((a + b) * t))))
    edges = [(i, (i + 1) % samples) for i in range(samples)]
    return verts, edges


def _harmonograph(samples=320):
    t = np.arange(samples) * 2 * np.pi / samples
    x = 0.65 * np.sin(3 * t + 0.4) + 0.35 * np.cos(5 * t)
    y = 0.65 * np.sin(4 * t) + 0.35 * np.sin(6 * t + 0.8)
    z = 0.3 * np.sin(2 * t)
    verts = _normalize(np.column_stack((x, y, z)))
    edges = [(i, (i + 1) % samples) for i in range(samples)]
    return verts, edges


@lru_cache(maxsize=None)
def geometry(name):
    if name == 'Tilting circle':
        return ring_graph(((0, 1),), 128)
    if name == 'Octant sphere':
        return ring_graph(((0, 1), (0, 2), (1, 2)), 96)
    if name == 'Torus': return _torus_wireframe()
    if name == 'Trefoil knot': return _torus_knot(2, 3)
    if name == 'Cinquefoil knot': return _torus_knot(3, 5)
    if name == 'Möbius strip': return _mobius_strip()
    if name == 'Hypercube (Tesseract)': return _tesseract()
    if name == 'Geodesic sphere': return _geodesic_sphere()
    if name == 'Hexagonal prism': return _prism(6)
    if name == 'Square pyramid': return _pyramid(4)
    if name == 'Hexagon (2D)':
        ang = np.arange(6) * 2 * np.pi / 6 + np.pi / 2
        return np.column_stack((np.cos(ang), np.sin(ang), np.zeros(6))), [(i, (i + 1) % 6) for i in range(6)]
    if name == 'Star (5-point)': return _star(5, 0.42)
    if name == 'Star (8-point)': return _star(8, 0.48)
    if name == 'Gear (12-tooth)': return _gear(12)
    if name == 'Spiral': return _spiral()
    if name == 'Radar reticle':
        verts, edges, _, _ = dynamic_solid_route_at(RADAR_SOLID, None, 0.0)
        return verts, [(int(a), int(b)) for a, b in edges]
    if name == 'Lissajous (3:2)': return _lissajous(3, 2)
    if name == 'Harmonograph': return _harmonograph()
    if name == 'Helix': return _helix(cone=False)
    if name == 'Vortex cone': return _helix(cone=True)
    if name == 'Clock face':
        verts, edges, _, _ = dynamic_solid_route_at(CLOCK_SOLID, None, 0.0)
        return verts, [(int(a), int(b)) for a, b in edges]

    cube = np.array([[1 if i & (1 << a) else -1 for a in range(3)]
                     for i in range(8)], dtype=float)
    tetra = np.array([[1, 1, 1], [1, -1, -1], [-1, 1, -1], [-1, -1, 1]], dtype=float)
    octa = np.concatenate((np.eye(3), -np.eye(3)))
    phi = (1 + np.sqrt(5)) / 2
    if name == 'Cube':
        vertices = cube
    elif name == 'Tetrahedron':
        vertices = tetra
    elif name == 'Octahedron':
        vertices = octa
    elif name in ('Dodecahedron', 'Icosahedron'):
        a, b = (1 / phi, phi) if name == 'Dodecahedron' else (1, phi)
        ring = np.array([[0, first * a, second * b] for first in (-1, 1) for second in (-1, 1)])
        vertices = np.concatenate([np.roll(ring, shift, axis=1) for shift in range(3)])
        if name == 'Dodecahedron':
            vertices = np.concatenate((cube, vertices))
    elif name in POLYGONS:
        count = POLYGONS[name]
        angle = np.arange(count) * 2 * np.pi / count + (np.pi / 4 if count == 4 else np.pi / 2)
        vertices = np.column_stack((np.cos(angle), np.sin(angle), np.zeros(count)))
    else:
        raise ValueError(f'Unknown solid: {name}')
    edges = edges_for(vertices)
    vertices = vertices / np.linalg.norm(vertices, axis=1).max()
    return vertices, edges


@lru_cache(maxsize=None)
def route_for(name):
    if name in ANIMATED_SOLIDS:
        return dynamic_solid_route_at(name, None, 0.0)[2]
    vertices, edges = geometry(name)
    if name in _CLOSED_LOOPS:
        return np.array([*range(len(vertices)), 0])
    if name in _RETRACE_PATHS:
        return np.array([*range(len(vertices)), *range(len(vertices) - 2, -1, -1)])
    # Double edges gives a closed route for every connected graph without blanking.
    # Preserve the cube's existing shorter route by duplicating a perfect matching.
    degree = np.bincount(np.array(edges).ravel(), minlength=len(vertices))
    extra = ([(i, i ^ 1) for i in range(0, 8, 2)] if name == 'Cube'
             else ([] if np.all(degree % 2 == 0) else edges))
    adjacency = [[] for _ in vertices]
    for index, (a, b) in enumerate(edges + extra):
        adjacency[a].append((b, index))
        adjacency[b].append((a, index))
    used, stack, circuit = set(), [0], []
    while stack:
        vertex = stack[-1]
        while adjacency[vertex] and adjacency[vertex][-1][1] in used:
            adjacency[vertex].pop()
        if adjacency[vertex]:
            neighbor, index = adjacency[vertex].pop()
            used.add(index)
            stack.append(neighbor)
        else:
            circuit.append(stack.pop())
    return np.array(circuit[::-1])


@lru_cache(maxsize=256)
def solid_geometry(solid, params=None):
    """Geometry (vertices, edges) for a built-in solid, honoring Star/Gear ShapeParams when provided."""
    if params is None:
        return geometry(solid)
    if solid in STAR_SOLIDS:
        verts, edges = _star(int(params.points), float(params.inner_radius))
        verts = np.asarray(verts, dtype=float)
        verts.setflags(write=False)
        return verts, edges
    if solid in GEAR_SOLIDS:
        inner = max(0.35, 1.0 - float(params.tooth_depth))
        verts, edges = _gear(int(params.teeth), inner=inner, outer=1.0)
        verts = np.asarray(verts, dtype=float)
        verts.setflags(write=False)
        return verts, edges
    return geometry(solid)


@lru_cache(maxsize=256)
def solid_route(solid, params=None):
    """Closed vertex route for a built-in solid, honoring Star/Gear ShapeParams when provided."""
    if params is None:
        return route_for(solid)
    if solid in STAR_SOLIDS or solid in GEAR_SOLIDS:
        verts, _ = solid_geometry(solid, params)
        route = np.array([*range(len(verts)), 0], dtype=int)
        route.setflags(write=False)
        return route
    return route_for(solid)


@lru_cache(maxsize=1)
def _radar_base():
    """Cached static vertices, ring/spoke edges, and continuous route template for the Radar reticle."""
    samples = 48
    ang = np.arange(samples, dtype=float) * (2.0 * np.pi / samples)
    ca, sa = np.cos(ang), np.sin(ang)
    verts = [[0.0, 0.0, 0.0]]  # 0 = center
    for radius in (0.33, 0.66, 1.0):
        for c, s in zip(ca, sa):
            verts.append([radius * c, radius * s, 0.0])
    verts.append([math.cos(math.pi / 4.0), math.sin(math.pi / 4.0), 0.0])  # 145 = dynamic sweep tip
    r0 = list(range(1, 1 + samples))
    r1 = list(range(1 + samples, 1 + 2 * samples))
    r2 = list(range(1 + 2 * samples, 1 + 3 * samples))
    tip = 1 + 3 * samples
    route = [
        0, tip, 0, tip, 0,
        r0[0], *r0[1:], r0[0],
        r1[0], *r1[1:], r1[0],
        r2[0], *r2[1:], r2[0],
        r1[0], r0[0], 0,
        r2[samples // 4], 0,
        r2[samples // 2], 0,
        r2[3 * samples // 4], 0,
    ]
    edges = sorted({(min(a, b), max(a, b)) for a, b in zip(route[:-1], route[1:]) if a != b})
    base_verts = np.asarray(verts, dtype=float)
    base_verts.setflags(write=False)
    route_arr = np.asarray(route, dtype=int)
    route_arr.setflags(write=False)
    edges_arr = np.asarray(edges, dtype=int)
    edges_arr.setflags(write=False)
    return base_verts, edges_arr, route_arr, tip


@lru_cache(maxsize=1)
def _clock_base():
    """Cached dial vertices, ticks, hub, route template, and rim<->hub connector indices for Clock face."""
    samples = 60
    verts = []
    for k in range(samples):
        ang = math.pi / 2.0 - 2.0 * math.pi * (k / samples)
        verts.append([math.cos(ang), math.sin(ang), 0.0])
    for h in range(12):
        k = h * 5
        ang = math.pi / 2.0 - 2.0 * math.pi * (k / samples)
        r = 0.78 if h % 3 == 0 else 0.88
        verts.append([r * math.cos(ang), r * math.sin(ang), 0.0])
    hub = len(verts)      # 72
    verts.append([0.0, 0.0, 0.0])
    min_tip = len(verts)  # 73
    verts.append([0.0, 0.76, 0.0])
    hr_tip = len(verts)   # 74
    verts.append([0.48, 0.0, 0.0])

    # Trace dial + all 12 hour ticks continuously, then fast jump to center hub for freely rotating hands.
    route = []
    for k in range(samples):
        route.append(k)
        if k % 5 == 0:
            tick_idx = samples + (k // 5)
            route.extend((tick_idx, k))
    route.append(0)
    # Single connector from 12 o'clock rim (0) to center hub (72), draw both hands cleanly, jump back to 0.
    route.extend((hub, min_tip, hub, min_tip, hub, hr_tip, hub, hr_tip, hub, 0))

    connectors = np.array([[0, hub]], dtype=int)
    connectors.setflags(write=False)
    edges = sorted({(min(a, b), max(a, b)) for a, b in zip(route[:-1], route[1:]) if a != b})
    base_verts = np.asarray(verts, dtype=float)
    base_verts.setflags(write=False)
    route_arr = np.asarray(route, dtype=int)
    route_arr.setflags(write=False)
    edges_arr = np.asarray(edges, dtype=int)
    edges_arr.setflags(write=False)
    return base_verts, edges_arr, route_arr, connectors, min_tip, hr_tip


def dynamic_solid_route_at(solid, params=None, seconds=0.0):
    """Animated (vertices, edges, route, connectors) at pose time `seconds` for Radar reticle & Clock face."""
    p = params if params is not None else default_shape_params(solid)
    t = float(seconds)
    if solid == RADAR_SOLID:
        base_verts, edges, route, tip = _radar_base()
        verts = base_verts.copy()
        theta = math.pi / 4.0 - 2.0 * math.pi * float(p.sweep_speed) * t
        verts[tip, 0] = math.cos(theta)
        verts[tip, 1] = math.sin(theta)
        verts.setflags(write=False)
        return verts, edges, route, np.empty((0, 2), dtype=int)
    if solid == CLOCK_SOLID:
        base_verts, edges, route, connectors, min_tip, hr_tip = _clock_base()
        verts = base_verts.copy()
        minutes = float(p.start_minute) + (t * float(p.clock_speed)) / 60.0
        hours = float(p.start_hour) + minutes / 60.0
        ang_m = math.pi / 2.0 - 2.0 * math.pi * (minutes / 60.0)
        ang_h = math.pi / 2.0 - 2.0 * math.pi * (hours / 12.0)
        verts[min_tip, 0] = 0.76 * math.cos(ang_m)
        verts[min_tip, 1] = 0.76 * math.sin(ang_m)
        verts[hr_tip, 0] = 0.48 * math.cos(ang_h)
        verts[hr_tip, 1] = 0.48 * math.sin(ang_h)
        verts.setflags(write=False)
        return verts, edges, route, connectors
    verts, edges = solid_geometry(solid, p)
    return verts, np.asarray(edges, dtype=int), solid_route(solid, p), np.empty((0, 2), dtype=int)
