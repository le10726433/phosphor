"""Imported 3D wireframes: OBJ parsing, feature-edge reduction and bounded routing."""
from dataclasses import dataclass
from functools import lru_cache
import heapq
import math
from pathlib import Path
import numpy as np

MODEL_OBJECT = '3D model'
# Safety limit only (0.38.0-dev). Every kept feature edge is drawn; a pass at 60 Hz has about 800
# samples, so dense models trace dimly and coarsely unless Path Trace shows part of them.
MAX_MODEL_EDGES = 32768
MAX_MODEL_VERTICES = 2*MAX_MODEL_EDGES
# 0.59.0-dev: raised from 64 MB / 200,000 faces so dense scans reach the importer's
# simplifier; models over SUGGESTED_TRIANGLES open with Simplify pre-ticked.
MAX_OBJ_BYTES = 512*1024*1024
MAX_OBJ_FACES = 2_000_000
MAX_MODEL_BYTES = MAX_OBJ_BYTES
MAX_MODEL_FACES = MAX_OBJ_FACES
CREASE_DEGREES = 30.
MIN_FEATURE_EDGES = 12  # Fewer sharp edges than this means a smooth model.
WELD_FRACTION = 1e-6  # Of the model's bounding-box diagonal.


@dataclass(frozen=True)
class Model:
    name: str = 'Model'
    vertices: tuple = ()  # (x, y, z), centred and scaled to unit radius
    edges: tuple = ()  # (a, b) vertex indices, a < b
    animation: object = None

    # 0.55.0-dev: lru lookups (scene_plan, planned_route, model_route) hash the model on
    # every frame and audio block. Frozen fields never change, so the field hash is
    # computed once per instance. Equality is still the dataclass field comparison.
    def __hash__(self):
        value = self.__dict__.get('_hash')
        if value is None:
            fields = (self.name, self.vertices, self.edges)
            value = hash(fields if self.animation is None else (*fields, self.animation))
            object.__setattr__(self, '_hash', value)
        return value

    def __eq__(self, other):
        """Field equality, identity-fast after the first compare (P7c, hot spot H2).

        A cache that holds an equal model from an earlier load (reopening a scene, loading the
        model again) compared every vertex and edge on each lookup: 0.74 ms at 18,796 edges.
        Models found equal share a key object, so later compares are one identity test. The
        key only joins models proven equal, so equality itself is unchanged.
        """
        if self is other: return True
        if other.__class__ is not self.__class__: return NotImplemented
        mine, theirs = self.__dict__.get('_same'), other.__dict__.get('_same')
        if mine is not None and mine is theirs: return True
        hashes = self.__dict__.get('_hash'), other.__dict__.get('_hash')
        if None not in hashes and hashes[0] != hashes[1]: return False  # Cached hashes of the same fields.
        if (self.name, self.vertices, self.edges, self.animation) != (other.name, other.vertices, other.edges, other.animation):
            return False
        key = mine or theirs or object()
        object.__setattr__(self, '_same', key); object.__setattr__(other, '_same', key)
        return True

    def __reduce__(self):
        # Never carry the caches across pickling: string hashes differ per process.
        fields = (self.name, self.vertices, self.edges)
        return type(self), fields if self.animation is None else (*fields, self.animation)

    def document(self):
        value = dict(name=self.name, vertices=[list(v) for v in self.vertices], edges=[list(e) for e in self.edges])
        if self.animation is not None: value['animation'] = self.animation.document()
        return value

    @classmethod
    def read(cls, value):
        if not isinstance(value, dict) or set(value) not in ({'name', 'vertices', 'edges'}, {'name', 'vertices', 'edges', 'animation'}):
            raise ValueError('Unsupported 3D model data.')
        if not isinstance(value['vertices'], list) or not isinstance(value['edges'], list):
            raise ValueError('Unsupported 3D model data.')
        from phosphor.engine.model_animation import ModelAnimation
        animation = ModelAnimation.read(value['animation']) if 'animation' in value else None
        return cls(value['name'], tuple(tuple(v) for v in value['vertices']), tuple(tuple(e) for e in value['edges']), animation)

    def validate(self):
        """Raise ValueError for bad data. A model that passed once is not checked again:
        signal_engine.validate runs on every render (each frame and audio block)."""
        if self.__dict__.get('_valid'):
            return
        self._check()
        object.__setattr__(self, '_valid', True)

    def _check(self):
        if self.animation is not None:
            self.animation.validate()
            if self.animation.vertex_count != len(self.vertices):
                raise ValueError('Animation vertices do not match the model.')
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
    simplified: bool = False
    source_triangles: int = 0
    result_triangles: int = 0
    boundary_collapses: int = 0  # Open-edge vertices removed to reach the target.
    handles_closed: int = 0  # Holes/handles closed to reach the target (about).
    topology_changed: bool = False
    floor_triangles: int = 0  # Lowest count reachable without changing the outline/topology.


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


def _weld(vertices, label='OBJ'):
    points = np.asarray(vertices, dtype=float)
    span = float(np.linalg.norm(points.max(axis=0)-points.min(axis=0)))
    if span <= 0:
        raise ValueError(f'This {label} has no size: every vertex is in the same place.')
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


def _triangles(faces, welded):
    if isinstance(faces, np.ndarray) and faces.ndim == 2 and faces.shape[1] == 3 and np.issubdtype(faces.dtype, np.integer):
        tris = welded[faces]
        return tris[(tris[:, 0] != tris[:, 1]) & (tris[:, 1] != tris[:, 2]) &
                    (tris[:, 0] != tris[:, 2])].astype(np.int64)
    result = []
    for face in faces:
        polygon = []
        for vertex in face:
            vertex = int(welded[vertex])
            if not polygon or polygon[-1] != vertex:
                polygon.append(vertex)
        if len(polygon) > 1 and polygon[0] == polygon[-1]:
            polygon.pop()
        for i in range(1, len(polygon)-1):
            a, b, c = polygon[0], polygon[i], polygon[i+1]
            if a != b and b != c and a != c:
                result.append((a, b, c))
    return np.asarray(result, dtype=np.int64).reshape(-1, 3)


def source_triangle_count(vertices, faces, label='OBJ'):
    _, welded = _weld(vertices, label)
    return len(_triangles(faces, welded))


def model_from_obj(text, name='Model', simplify_to=None):
    return model_from_mesh(*parse_obj(text), name, 'OBJ', simplify_to)


def model_from_mesh(vertices, faces, lines, name='Model', label='OBJ', simplify_to=None, progress=None, vertex_indices=None):
    """progress: optional mesh_simplify.Progress, updated with the stage as this runs."""
    def stage(text):
        if progress is not None:
            progress.stage, progress.state = text, None
    if simplify_to is not None:
        from phosphor.engine import mesh_simplify
        stage('Welding vertices…')
        points, welded = _weld(vertices, label)
        tris = _triangles(faces, welded)
        locked = np.zeros(len(points), dtype=bool)
        for line in lines:
            locked[welded[np.asarray(line, dtype=np.int64)]] = True
        stage('Simplifying…')
        result = mesh_simplify.simplify(points, tris, simplify_to, locked, relax=True,
                                        progress=progress)
        stage('Finding feature edges…')
        rep = np.full(len(points), -1, dtype=np.int64)
        rep[welded[::-1]] = np.arange(len(welded))[::-1]
        faces = rep[result.kept_vertex_indices[result.faces]].tolist()
        model, report = _model_from_mesh(vertices, faces, lines, name, label, vertex_indices)
        return model, ImportReport(report.source_edges, report.kept_edges, report.limited,
                                   report.smooth, True, result.source_triangles,
                                   result.result_triangles, result.boundary_collapses,
                                   result.handles_closed, result.topology_changed,
                                   result.floor_triangles)
    stage('Finding feature edges…')
    return _model_from_mesh(vertices, faces, lines, name, label, vertex_indices)


def _model_from_mesh(vertices, faces, lines, name, label, vertex_indices=None):
    if isinstance(faces, np.ndarray):
        faces = faces.tolist()
    if isinstance(lines, np.ndarray):
        lines = lines.tolist()
    points, welded = _weld(vertices, label)
    scores = _edge_scores(points, faces, lines, welded)
    if not scores:
        raise ValueError(f'This {label} has no usable edges.')
    kept, limited, smooth = reduce_edges(scores)
    used = sorted({vertex for edge in kept for vertex in edge})
    if vertex_indices is not None:
        rep = np.full(len(points), -1, dtype=np.int64)
        rep[welded[::-1]] = np.arange(len(welded))[::-1]
        vertex_indices.extend(rep[used].tolist())
    index = {vertex: position for position, vertex in enumerate(used)}
    local = points[used]
    local = local-(local.min(axis=0)+local.max(axis=0))/2
    radius = float(np.linalg.norm(local, axis=1).max())
    if radius <= 0:
        raise ValueError(f'This {label} has no size after reduction.')
    local = local/radius
    edges = tuple(sorted((index[a], index[b]) for a, b in kept))
    model = Model((name.strip() or 'Model')[:64], tuple(tuple(float(x) for x in point) for point in local), edges)
    model.validate()
    return model, ImportReport(len(scores), len(edges), limited, smooth)


def load_obj(path, simplify_to=None):
    path = Path(path)
    if path.stat().st_size > MAX_OBJ_BYTES:
        raise ValueError(f'OBJ files are limited to {MAX_OBJ_BYTES//(1024*1024)} MB.')
    return model_from_obj(path.read_text(encoding='utf-8', errors='replace'), path.stem, simplify_to)


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
    # Each step joins the part holding vertex 0 to its nearest other part. Before
    # 0.55.0-dev every step rebuilt the full connected × remaining distance matrix
    # (O(parts × V²)); now each remaining vertex keeps its nearest connected vertex and
    # only the newly joined part's distances are computed. The result is identical: the
    # same norms, and ties go to the lowest connected index, then the lowest remaining
    # index, exactly as argmin over the row-major matrix did.
    labels = np.array([root(i) for i in range(len(points))])
    remaining = np.flatnonzero(labels != labels[0])
    joined = np.flatnonzero(labels == labels[0])
    best = np.full(len(remaining), np.inf)
    nearest = np.zeros(len(remaining), dtype=np.int64)
    connectors = []
    while len(remaining):
        for start in range(0, len(joined), 256):  # Bounded memory for large parts.
            rows = joined[start:start+256]
            distances = np.linalg.norm(points[rows, None]-points[None, remaining], axis=2)
            low = np.argmin(distances, axis=0)  # First (lowest) joined index per column.
            value = distances[low, np.arange(len(remaining))]
            # A part joined later may hold lower indices: an equal distance then wins.
            better = (value < best) | ((value == best) & (rows[low] < nearest))
            best[better], nearest[better] = value[better], rows[low[better]]
        minimum = best.min()
        ties = np.flatnonzero(best == minimum)
        j = ties[np.lexsort((remaining[ties], nearest[ties]))[0]]
        a, b = int(nearest[j]), int(remaining[j])
        connectors.append((min(a, b), max(a, b)))
        moved = labels[remaining] == labels[b]
        joined = remaining[moved]  # Ascending, like remaining.
        remaining, best, nearest = remaining[~moved], best[~moved], nearest[~moved]
    return connectors


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
