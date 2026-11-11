"""Imported 3D wireframes: OBJ parsing, feature-edge reduction and bounded routing."""
from dataclasses import dataclass
from functools import lru_cache
import heapq
import math
from pathlib import Path
import numpy as np

MODEL_OBJECT = '3D model'
MAX_MODEL_EDGES = 256  # Each pass has about 800 samples at 60 Hz; more cannot draw cleanly.
MAX_MODEL_VERTICES = 2*MAX_MODEL_EDGES
MAX_OBJ_BYTES = 64*1024*1024
MAX_OBJ_FACES = 200000
CREASE_DEGREES = 30.
MIN_FEATURE_EDGES = 12  # Fewer sharp edges than this means a smooth model.
WELD_FRACTION = 1e-6  # Of the model's bounding-box diagonal.


@dataclass(frozen=True)
class Model:
    name: str = 'Model'
    vertices: tuple = ()  # (x, y, z), centred and scaled to unit radius
    edges: tuple = ()  # (a, b) vertex indices, a < b

    def document(self):
        return dict(name=self.name, vertices=[list(v) for v in self.vertices], edges=[list(e) for e in self.edges])

    @classmethod
    def read(cls, value):
        if not isinstance(value, dict) or set(value) != {'name', 'vertices', 'edges'}:
            raise ValueError('Unsupported 3D model data.')
        if not isinstance(value['vertices'], list) or not isinstance(value['edges'], list):
            raise ValueError('Unsupported 3D model data.')
        return cls(value['name'], tuple(tuple(v) for v in value['vertices']), tuple(tuple(e) for e in value['edges']))

    def validate(self):
        if not isinstance(self.name, str) or not 1 <= len(self.name.strip()) <= 64:
            raise ValueError('A 3D model needs a name of 1-64 characters.')
        if not 1 <= len(self.edges) <= MAX_MODEL_EDGES or not 2 <= len(self.vertices) <= MAX_MODEL_VERTICES:
            raise ValueError(f'A 3D model needs 1-{MAX_MODEL_EDGES} edges and at most {MAX_MODEL_VERTICES} vertices.')
        for vertex in self.vertices:
            if len(vertex) != 3 or any(type(x) not in (int, float) or not math.isfinite(x) or abs(x) > 1.001 for x in vertex):
                raise ValueError('3D model vertices must be finite and normalized.')
        seen = set()
        for edge in self.edges:
            if len(edge) != 2 or any(type(i) is not int or not 0 <= i < len(self.vertices) for i in edge) or edge[0] >= edge[1] or edge in seen:
                raise ValueError('Invalid 3D model edge.')
            seen.add(edge)


@dataclass(frozen=True)
class ImportReport:
    source_edges: int
    kept_edges: int
    limited: bool  # More feature edges existed than the budget allows.
    smooth: bool  # Too few sharp edges; the sharpest available were kept instead.


def _index(token, count, line):
    try:
        value = int(token.split('/')[0])
    except ValueError as error:
        raise ValueError(f'Invalid OBJ element on line {line}.') from error
    index = value-1 if value > 0 else count+value
    if value == 0 or not 0 <= index < count:
        raise ValueError(f'OBJ line {line} refers to a missing vertex.')
    return index


def parse_obj(text):
    """Vertices, polygon faces and explicit polylines. Unused OBJ statements are ignored."""
    vertices, faces, lines = [], [], []
    for number, raw in enumerate(text.splitlines(), 1):
        parts = raw.split('#', 1)[0].split()
        if not parts:
            continue
        kind = parts[0]
        if kind == 'v':
            try:
                vertex = tuple(float(x) for x in parts[1:4])
            except ValueError as error:
                raise ValueError(f'Invalid OBJ vertex on line {number}.') from error
            if len(vertex) != 3 or not all(math.isfinite(x) for x in vertex):
                raise ValueError(f'Invalid OBJ vertex on line {number}.')
            vertices.append(vertex)
        elif kind == 'f' and len(parts) >= 4:
            faces.append([_index(token, len(vertices), number) for token in parts[1:]])
            if len(faces) > MAX_OBJ_FACES:
                raise ValueError(f'This OBJ has more than {MAX_OBJ_FACES} faces. Simplify it first.')
        elif kind == 'l' and len(parts) >= 3:
            lines.append([_index(token, len(vertices), number) for token in parts[1:]])
    if not vertices or not (faces or lines):
        raise ValueError('This OBJ has no faces or lines to draw.')
    return vertices, faces, lines


def _weld(vertices):
    points = np.asarray(vertices, dtype=float)
    span = float(np.linalg.norm(points.max(axis=0)-points.min(axis=0)))
    if span <= 0:
        raise ValueError('This OBJ has no size: every vertex is in the same place.')
    _, first, inverse = np.unique(np.rint(points/(span*WELD_FRACTION)).astype(np.int64), axis=0, return_index=True, return_inverse=True)
    return points[first], inverse.ravel()


def _face_normal(polygon, points):
    """Newell's method, which also handles non-planar and concave polygons."""
    x = y = z = 0.
    for a, b in zip(polygon, polygon[1:]+polygon[:1]):
        (ax, ay, az), (bx, by, bz) = points[a], points[b]
        x += (ay-by)*(az+bz)
        y += (az-bz)*(ax+bx)
        z += (ax-bx)*(ay+by)
    length = math.sqrt(x*x+y*y+z*z)
    return (x/length, y/length, z/length) if length > 1e-30 else None


def _edge_scores(points, faces, lines, welded):
    """Sharpness of every unique edge: 180 for an open or explicit edge, else the crease angle."""
    normals = {}
    pts = points.tolist()
    for face in faces:
        polygon = []
        for vertex in face:
            vertex = int(welded[vertex])
            if not polygon or polygon[-1] != vertex:
                polygon.append(vertex)
        if len(polygon) > 1 and polygon[0] == polygon[-1]:
            polygon.pop()
        if len(polygon) < 3:
            continue
        normal = _face_normal(polygon, pts)
        if normal is None:
            continue
        for a, b in zip(polygon, polygon[1:]+polygon[:1]):
            if a != b:
                normals.setdefault((min(a, b), max(a, b)), []).append(normal)
    scores = {}
    for edge, found in normals.items():
        if len(found) != 2:
            scores[edge] = 180.
        else:
            # Absolute dot product: exported meshes often have inconsistent winding.
            dot = min(1., abs(found[0][0]*found[1][0]+found[0][1]*found[1][1]+found[0][2]*found[1][2]))
            scores[edge] = math.degrees(math.acos(dot))
    for line in lines:
        for a, b in zip(line, line[1:]):
            a, b = int(welded[a]), int(welded[b])
            if a != b:
                scores[(min(a, b), max(a, b))] = 180.
    return scores


def reduce_edges(scores):
    """Keep the edges that define the form, within the sample budget."""
    ranked = sorted(scores, key=lambda edge: (-scores[edge], edge))
    sharp = [edge for edge in ranked if scores[edge] >= CREASE_DEGREES]
    smooth = len(sharp) < min(MIN_FEATURE_EDGES, len(ranked))
    chosen = ranked if smooth else sharp
    return chosen[:MAX_MODEL_EDGES], len(chosen) > MAX_MODEL_EDGES, smooth


def model_from_obj(text, name='Model'):
    vertices, faces, lines = parse_obj(text)
    points, welded = _weld(vertices)
    scores = _edge_scores(points, faces, lines, welded)
    if not scores:
        raise ValueError('This OBJ has no usable edges.')
    kept, limited, smooth = reduce_edges(scores)
    used = sorted({vertex for edge in kept for vertex in edge})
    index = {vertex: position for position, vertex in enumerate(used)}
    local = points[used]
    local = local-(local.min(axis=0)+local.max(axis=0))/2
    radius = float(np.linalg.norm(local, axis=1).max())
    if radius <= 0:
        raise ValueError('This OBJ has no size after reduction.')
    local = local/radius
    edges = tuple(sorted((index[a], index[b]) for a, b in kept))
    model = Model((name.strip() or 'Model')[:64], tuple(tuple(float(x) for x in point) for point in local), edges)
    model.validate()
    return model, ImportReport(len(scores), len(edges), limited, smooth)


def load_obj(path):
    path = Path(path)
    if path.stat().st_size > MAX_OBJ_BYTES:
        raise ValueError(f'OBJ files are limited to {MAX_OBJ_BYTES//(1024*1024)} MB.')
    return model_from_obj(path.read_text(encoding='utf-8', errors='replace'), path.stem)


def _connect_components(points, edges):
    """Straight connector edges joining separate parts by their nearest vertices."""
    parent = list(range(len(points)))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for a, b in edges:
        parent[root(a)] = root(b)
    connectors = []
    while True:
        roots = np.array([root(i) for i in range(len(points))])
        connected, remaining = np.flatnonzero(roots == roots[0]), np.flatnonzero(roots != roots[0])
        if not len(remaining):
            return connectors
        distances = np.linalg.norm(points[connected, None]-points[None, remaining], axis=2)
        i, j = np.unravel_index(int(np.argmin(distances)), distances.shape)
        a, b = int(connected[i]), int(remaining[j])
        connectors.append((min(a, b), max(a, b)))
        parent[root(b)] = root(a)


def _retraced_edges(points, edges):
    """Extra passes that pair odd vertices along short paths, so a closed route exists."""
    adjacency = [[] for _ in points]
    for a, b in edges:
        length = float(np.linalg.norm(points[a]-points[b]))
        adjacency[a].append((b, length))
        adjacency[b].append((a, length))
    odd = sorted(i for i, links in enumerate(adjacency) if len(links) % 2)
    extra = []
    while odd:
        start = odd.pop(0)
        targets, pending, best, previous = set(odd), [(0., start)], {start: 0.}, {}
        end = None
        while pending:
            distance, vertex = heapq.heappop(pending)
            if distance > best[vertex]:
                continue
            if vertex in targets:
                end = vertex
                break
            for neighbor, length in adjacency[vertex]:
                candidate = distance+length
                if candidate < best.get(neighbor, float('inf')):
                    best[neighbor], previous[neighbor] = candidate, vertex
                    heapq.heappush(pending, (candidate, neighbor))
        odd.remove(end)
        while end != start:
            extra.append((previous[end], end))
            end = previous[end]
    return extra


@lru_cache(maxsize=64)
def model_route(model):
    """Vertices, edges including connectors, closed route, and the connector edges."""
    points = np.array(model.vertices, dtype=float)
    points.setflags(write=False)
    edges = list(model.edges)
    connectors = _connect_components(points, edges)
    edges += connectors
    adjacency = [[] for _ in points]
    for index, (a, b) in enumerate(edges+_retraced_edges(points, edges)):
        adjacency[a].append((b, index))
        adjacency[b].append((a, index))
    used, stack, route = set(), [0], []
    while stack:
        vertex = stack[-1]
        while adjacency[vertex] and adjacency[vertex][-1][1] in used:
            adjacency[vertex].pop()
        if adjacency[vertex]:
            neighbor, index = adjacency[vertex].pop()
            used.add(index)
            stack.append(neighbor)
        else:
            route.append(stack.pop())
    route = np.array(route[::-1])
    route.setflags(write=False)
    joined = np.array(connectors, dtype=int).reshape(-1, 2)
    joined.setflags(write=False)
    return points, edges, route, joined
