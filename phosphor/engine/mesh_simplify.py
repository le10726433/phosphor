"""Quadric-error (Garland-Heckbert) half-edge-collapse simplifier (0.59.0-dev).

A half-edge collapse removes one vertex and keeps the other where it is, so the result is a
subset of the input vertices. Stage C (animated models) reuses `kept_vertex_indices` and the
result faces for every frame.

The collapse kernel is one source run two ways: compiled by Numba (nogil) on flat NumPy
arrays, or as plain Python on flat Python lists. Both do the same IEEE double operations in
the same order, so their output is identical; every tie is broken by vertex index.
"""
import numpy as np

# Quadric coefficients per face or vertex: [aa, ab, ac, ad, bb, bc, bd, cc, cd, dd] of the
# plane (a, b, c, d). Only elementwise NumPy in a fixed order, so results are reproducible.


def face_quadrics(points, triangles):
    """Area-weighted plane quadric of every triangle, (F, 10). Degenerate faces are zero."""
    p0 = points[triangles[:, 0]]
    e1 = points[triangles[:, 1]]-p0
    e2 = points[triangles[:, 2]]-p0
    e1x, e1y, e1z = e1[:, 0], e1[:, 1], e1[:, 2]
    e2x, e2y, e2z = e2[:, 0], e2[:, 1], e2[:, 2]
    nx = e1y*e2z-e1z*e2y
    ny = e1z*e2x-e1x*e2z
    nz = e1x*e2y-e1y*e2x
    length = np.sqrt(nx*nx+ny*ny+nz*nz)
    safe = np.where(length == 0, 1.0, length)
    a, b, c = nx/safe, ny/safe, nz/safe
    d = -(a*p0[:, 0]+b*p0[:, 1]+c*p0[:, 2])
    w = length*0.5
    q = np.empty((len(triangles), 10), dtype=np.float64)
    q[:, 0] = w*(a*a)
    q[:, 1] = w*(a*b)
    q[:, 2] = w*(a*c)
    q[:, 3] = w*(a*d)
    q[:, 4] = w*(b*b)
    q[:, 5] = w*(b*c)
    q[:, 6] = w*(b*d)
    q[:, 7] = w*(c*c)
    q[:, 8] = w*(c*d)
    q[:, 9] = w*(d*d)
    return np.where((length == 0).reshape(-1, 1), 0.0, q)


def vertex_quadrics(face_q, triangles, count):
    """Sum of the face quadrics around each vertex, (count, 10), in a fixed order."""
    result = np.zeros((count, 10), dtype=np.float64)
    for k in range(3):
        np.add.at(result, triangles[:, k], face_q)
    return result


# Kernel helpers: scalar code on flat sequences (Python lists or 1-D float64 arrays).

def collapse_cost(q, pts, keep, remove):
    """v^T (Q_keep + Q_remove) v at the position of `keep`."""
    x = pts[3*keep]
    y = pts[3*keep+1]
    z = pts[3*keep+2]
    i = 10*keep
    j = 10*remove
    c0 = q[i]+q[j]
    c1 = q[i+1]+q[j+1]
    c2 = q[i+2]+q[j+2]
    c3 = q[i+3]+q[j+3]
    c4 = q[i+4]+q[j+4]
    c5 = q[i+5]+q[j+5]
    c6 = q[i+6]+q[j+6]
    c7 = q[i+7]+q[j+7]
    c8 = q[i+8]+q[j+8]
    c9 = q[i+9]+q[j+9]
    return (c0*x*x+2.0*c1*x*y+2.0*c2*x*z+2.0*c3*x+c4*y*y+2.0*c5*y*z
            + 2.0*c6*y+c7*z*z+2.0*c8*z+c9)


def face_flips(pts, i0, i1, i2, old, new):
    """True if replacing `old` by `new` in triangle (i0, i1, i2) flips or flattens it.
    A triangle that was already degenerate never counts as flipped."""
    j0, j1, j2 = i0, i1, i2
    if i0 == old:
        j0 = new
    elif i1 == old:
        j1 = new
    else:
        j2 = new
    e1x = pts[3*i1]-pts[3*i0]
    e1y = pts[3*i1+1]-pts[3*i0+1]
    e1z = pts[3*i1+2]-pts[3*i0+2]
    e2x = pts[3*i2]-pts[3*i0]
    e2y = pts[3*i2+1]-pts[3*i0+1]
    e2z = pts[3*i2+2]-pts[3*i0+2]
    nbx = e1y*e2z-e1z*e2y
    nby = e1z*e2x-e1x*e2z
    nbz = e1x*e2y-e1y*e2x
    e1x = pts[3*j1]-pts[3*j0]
    e1y = pts[3*j1+1]-pts[3*j0+1]
    e1z = pts[3*j1+2]-pts[3*j0+2]
    e2x = pts[3*j2]-pts[3*j0]
    e2y = pts[3*j2+1]-pts[3*j0+1]
    e2z = pts[3*j2+2]-pts[3*j0+2]
    nax = e1y*e2z-e1z*e2y
    nay = e1z*e2x-e1x*e2z
    naz = e1x*e2y-e1y*e2x
    dot = nbx*nax+nby*nay+nbz*naz
    lb = nbx*nbx+nby*nby+nbz*nbz
    return dot <= 0.0 and lb > 0.0


# Everything below _ring through _collapse is scalar code shared by both backends.
def _ring(v, tris, alive, head, nxt, out, mark, counter):
    counter[1] += 1
    token = counter[1]
    size = 0
    c = head[v]
    while c != -1:
        f = c // 3
        if alive[f]:
            k = c-3*f
            for j in range(1, 3):
                w = tris[3*f+(k+j)%3]
                if mark[w] != token:
                    mark[w] = token
                    out[size] = w
                    size += 1
        c = nxt[c]
    return size


def _degree(v, tris, alive, head, nxt, mark, counter):
    counter[1] += 1
    token = counter[1]
    size = 0
    c = head[v]
    while c != -1:
        f = c // 3
        if alive[f]:
            k = c-3*f
            for j in range(1, 3):
                w = tris[3*f+(k+j)%3]
                if mark[w] != token:
                    mark[w] = token
                    size += 1
        c = nxt[c]
    return size


def _det(pts, a, b, c):
    """a . (b x c): six times the signed volume of the tetrahedron (origin, a, b, c)."""
    ax = pts[3*a]
    ay = pts[3*a+1]
    az = pts[3*a+2]
    bx = pts[3*b]
    by = pts[3*b+1]
    bz = pts[3*b+2]
    cx = pts[3*c]
    cy = pts[3*c+1]
    cz = pts[3*c+2]
    return ax*(by*cz-bz*cy)+ay*(bz*cx-bx*cz)+az*(bx*cy-by*cx)


def _volume_delta(v, u, pts, tris, alive, head, nxt):
    """Change of the enclosed (6x signed) volume if v collapses into u."""
    delta = 0.0
    c = head[v]
    while c != -1:
        f = c // 3
        if alive[f]:
            a = tris[3*f]
            b = tris[3*f+1]
            d = tris[3*f+2]
            old = _det(pts, a, b, d)
            if a == u or b == u or d == u:
                delta -= old
            else:
                if a == v:
                    a = u
                elif b == v:
                    b = u
                else:
                    d = u
                delta += _det(pts, a, b, d)-old
        c = nxt[c]
    return delta


def _valid(v, u, pts, tris, alive, head, nxt, ring_a, na, ring_b, mark, counter,
           locked, comp, vol, closed):
    # A boundary vertex (lock 1, movable only in the boundary pass) may only slide along
    # a boundary edge: one shared face and one common neighbour (the link condition).
    edge = 2
    if locked[v] == 1:
        edge = 1
    nb = _ring(u, tris, alive, head, nxt, ring_b, mark, counter)
    counter[1] += 1
    token = counter[1]
    for i in range(nb):
        mark[ring_b[i]] = token
    common = 0
    for i in range(na):
        if mark[ring_a[i]] == token:
            common += 1
    if common != edge:
        return False
    shared = 0
    # Volume change of a closed component, accumulated in the same walk (_volume_delta).
    r = comp[v]
    track = closed[r] != 0 and vol[r] != 0.0
    delta = 0.0
    c = head[v]
    while c != -1:
        f = c // 3
        if alive[f]:
            a = tris[3*f]
            b = tris[3*f+1]
            d = tris[3*f+2]
            if a == u or b == u or d == u:
                if track:
                    delta -= _det(pts, a, b, d)
                shared += 1
                w = a
                if w == v or w == u:
                    w = b
                if w == v or w == u:
                    w = d
                if _degree(w, tris, alive, head, nxt, mark, counter) < 4:
                    return False
            elif face_flips(pts, a, b, d, v, u):
                return False
            elif track:
                old = _det(pts, a, b, d)
                if a == v:
                    a = u
                elif b == v:
                    b = u
                else:
                    d = u
                delta += _det(pts, a, b, d)-old
        c = nxt[c]
    if shared != edge:
        return False
    # Every face can turn by less than 90 degrees and the surface still fold through
    # itself (a bipyramid collapsing to an inside-out tetrahedron). On a closed
    # component the enclosed volume must keep its sign.
    if track:
        after = vol[r]+delta
        if (vol[r] > 0.0 and after <= 0.0) or (vol[r] < 0.0 and after >= 0.0):
            return False
    return True


def _evaluate(v, pts, q, tris, locked, alive, verts, head, nxt,
              key, best, ring_a, ring_b, mark, counter, comp, vol, closed, state):
    best[v] = -1
    # state[1] is the pass: 1 moves only free vertices, 2 also boundary vertices.
    if locked[v] >= state[1] or not verts[v]:
        return
    na = _ring(v, tris, alive, head, nxt, ring_a, mark, counter)
    for i in range(na):
        u = ring_a[i]
        if verts[u] and _valid(v, u, pts, tris, alive, head, nxt,
                               ring_a, na, ring_b, mark, counter,
                               locked, comp, vol, closed):
            cost = collapse_cost(q, pts, u, v)
            if best[v] == -1 or cost < key[v] or (cost == key[v] and u < best[v]):
                key[v] = cost
                best[v] = u


def _less(a, b, key):
    return key[a] < key[b] or (key[a] == key[b] and a < b)


def _sift(i, heap, pos, key, counter):
    v = heap[i]
    while i > 0:
        parent = (i-1)//2
        w = heap[parent]
        if not _less(v, w, key):
            break
        heap[i] = w
        pos[w] = i
        i = parent
    while 2*i+1 < counter[0]:
        child = 2*i+1
        if child+1 < counter[0] and _less(heap[child+1], heap[child], key):
            child += 1
        w = heap[child]
        if not _less(w, v, key):
            break
        heap[i] = w
        pos[w] = i
        i = child
    heap[i] = v
    pos[v] = i


def _remove(v, heap, pos, key, counter):
    i = pos[v]
    if i == -1:
        return
    counter[0] -= 1
    pos[v] = -1
    if i < counter[0]:
        heap[i] = heap[counter[0]]
        pos[heap[i]] = i
        _sift(i, heap, pos, key, counter)


def _update(v, heap, pos, key, best, counter):
    if best[v] == -1:
        _remove(v, heap, pos, key, counter)
        return
    if pos[v] == -1:
        pos[v] = counter[0]
        heap[counter[0]] = v
        counter[0] += 1
    _sift(pos[v], heap, pos, key, counter)


def _find(parent, x):
    while parent[x] != x:
        parent[x] = parent[parent[x]]
        x = parent[x]
    return x


def _fill(pts, q, tris, locked, face_alive, vert_alive, head, nxt, heap, pos, key, best,
          ring_a, ring_b, mark, counter, comp, vol, closed, state):
    counter[0] = 0
    for v in range(len(head)):
        pos[v] = -1
    for v in range(len(head)):
        _evaluate(v, pts, q, tris, locked, face_alive, vert_alive, head, nxt,
                  key, best, ring_a, ring_b, mark, counter, comp, vol, closed, state)
        _update(v, heap, pos, key, best, counter)


def _drain(pts, q, tris, locked, target, face_alive, vert_alive, head, nxt,
           heap, pos, key, best, ring_a, ring_b, touched, mark, counter,
           comp, vol, closed, state, faces_left):
    while faces_left > target and counter[0] > 0:
        v = heap[0]
        old_key = key[v]
        old_best = best[v]
        _remove(v, heap, pos, key, counter)
        _evaluate(v, pts, q, tris, locked, face_alive, vert_alive, head, nxt,
                  key, best, ring_a, ring_b, mark, counter, comp, vol, closed, state)
        if best[v] == -1:
            continue
        if key[v] != old_key or best[v] != old_best:
            _update(v, heap, pos, key, best, counter)
            continue
        u = best[v]
        r = comp[v]
        if closed[r]:
            vol[r] = vol[r]+_volume_delta(v, u, pts, tris, face_alive, head, nxt)
        if locked[v] == 1:
            state[2] += 1
        c = head[v]
        while c != -1:
            f = c//3
            if face_alive[f]:
                if tris[3*f] == u or tris[3*f+1] == u or tris[3*f+2] == u:
                    face_alive[f] = 0
                    faces_left -= 1
                else:
                    tris[c] = u
            c = nxt[c]
        state[0] = faces_left
        # u's new list: v's live corners, then u's live corners, each in list order.
        # Dropping dead corners keeps every later walk short; walks skip dead faces
        # anyway, so the result is the same as keeping them.
        first = -1
        last = -1
        for start in range(2):
            c = head[v]
            if start == 1:
                c = head[u]
            while c != -1:
                following = nxt[c]
                if face_alive[c//3]:
                    if last == -1:
                        first = c
                    else:
                        nxt[last] = c
                    last = c
                c = following
        if last != -1:
            nxt[last] = -1
        head[u] = first
        head[v] = -1
        for k in range(10):
            q[10*u+k] = q[10*u+k]+q[10*v+k]
        vert_alive[v] = 0
        count = _ring(u, tris, face_alive, head, nxt, touched, mark, counter)
        for i in range(count+1):
            x = u
            if i > 0:
                x = touched[i-1]
            _evaluate(x, pts, q, tris, locked, face_alive, vert_alive, head, nxt,
                      key, best, ring_a, ring_b, mark, counter, comp, vol, closed, state)
            _update(x, heap, pos, key, best, counter)
    return faces_left


def _collapse(pts, q, tris, locked, target, face_alive, vert_alive, head, nxt,
              heap, pos, key, best, ring_a, ring_b, touched, mark, counter,
              comp, vol, closed, qb, state, relax):
    """state: [faces left, pass (1 or 2), boundary collapses, unused]; a caller on another
    thread may read it for progress. Pass 2 (relax only, when pass 1 stalls above the
    target) lets boundary vertices slide along the boundary under boundary-plane quadrics."""
    n = len(head)
    for c in range(len(tris)):
        v = tris[c]
        nxt[c] = head[v]
        head[v] = c
    # Connected components (union-find), each one's enclosed volume and closedness.
    for v in range(n):
        comp[v] = v
    for f in range(len(face_alive)):
        a = _find(comp, tris[3*f])
        b = _find(comp, tris[3*f+1])
        d = _find(comp, tris[3*f+2])
        if b < a:
            a, b = b, a
        comp[b] = a
        a = _find(comp, a)
        d = _find(comp, d)
        if d < a:
            a, d = d, a
        comp[d] = a
    for v in range(n):
        comp[v] = _find(comp, v)
    for v in range(n):
        vol[v] = 0.0
        closed[v] = 1
    for v in range(n):
        if locked[v] == 1 or locked[v] == 3:
            closed[comp[v]] = 0
    for f in range(len(face_alive)):
        r = comp[tris[3*f]]
        vol[r] = vol[r]+_det(pts, tris[3*f], tris[3*f+1], tris[3*f+2])
    faces_left = len(face_alive)
    state[0] = faces_left
    state[1] = 1
    _fill(pts, q, tris, locked, face_alive, vert_alive, head, nxt, heap, pos, key, best,
          ring_a, ring_b, mark, counter, comp, vol, closed, state)
    faces_left = _drain(pts, q, tris, locked, target, face_alive, vert_alive, head, nxt,
                        heap, pos, key, best, ring_a, ring_b, touched, mark, counter,
                        comp, vol, closed, state, faces_left)
    if relax and faces_left > target:
        state[1] = 2
        for v in range(n):
            if vert_alive[v]:
                for k in range(10):
                    q[10*v+k] = q[10*v+k]+qb[10*v+k]
        _fill(pts, q, tris, locked, face_alive, vert_alive, head, nxt, heap, pos, key, best,
              ring_a, ring_b, mark, counter, comp, vol, closed, state)
        faces_left = _drain(pts, q, tris, locked, target, face_alive, vert_alive, head, nxt,
                            heap, pos, key, best, ring_a, ring_b, touched, mark, counter,
                            comp, vol, closed, state, faces_left)
    return faces_left


import gc
import math
import os
import threading
import types
from dataclasses import dataclass

ENABLED = os.environ.get('PHOSPHOR_NUMBA', '1') != '0'
MIN_TARGET = 4
PYTHON_LIMIT = 4000


@dataclass(frozen=True, eq=False)
class Simplified:
    kept_vertex_indices: np.ndarray
    faces: np.ndarray
    source_triangles: int
    backend: str
    boundary_collapses: int = 0  # Boundary vertices removed by the relaxed pass.
    handles_closed: int = 0  # Holes plus handles closed by the topology fallback (_closed_count).
    topology_changed: bool = False
    floor_triangles: int = 0  # Lowest count reached before the topology fallback.

    @property
    def result_triangles(self):
        return len(self.faces)


class Progress:
    """Shared with a worker thread: stage text plus the kernel's live face count."""
    def __init__(self):
        self.stage = ''
        self.state = None
        self.source = 0
        self.target = 0

    def fraction(self):
        """0..1 while simplifying, else None (an indeterminate stage)."""
        state = self.state
        if state is None or self.source <= self.target:
            return None
        return min(1.0, max(0.0, (self.source-int(state[0]))/(self.source-self.target)))


def validate_target(target, source_triangles):
    if source_triangles <= MIN_TARGET:
        raise ValueError(f'This model has only {source_triangles} triangles; it cannot be simplified.')
    if type(target) is not int or not MIN_TARGET <= target < source_triangles:
        raise ValueError(f'Choose a target between 4 and {source_triangles-1} triangles.')


_condition = threading.Condition()
_thread = None
_kernel = None
_version = None
_reason = None if ENABLED else 'Numba disabled by PHOSPHOR_NUMBA=0'


# Lock levels: 0 free, 1 open boundary, 2 locked (explicit lines), 3 locked and open
# (non-manifold, or a locked boundary vertex). Pass 1 moves level 0, pass 2 levels 0-1.
FREE, BOUNDARY, LOCKED, OPEN_LOCKED = 0, 1, 2, 3


def _locks(tris, count, locked):
    edges = np.sort(np.concatenate((tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]])), axis=1)
    edges, counts = np.unique(edges, axis=0, return_counts=True)
    boundary = np.zeros(count, dtype=bool)
    boundary[edges[counts == 1].ravel()] = True
    hard = np.zeros(count, dtype=bool)
    hard[edges[counts > 2].ravel()] = True
    user = np.zeros(count, dtype=bool) if locked is None else np.asarray(locked, dtype=bool)
    locks = np.zeros(count, dtype=np.int64)
    locks[boundary] = BOUNDARY
    locks[user & ~boundary] = LOCKED
    locks[hard | (user & boundary)] = OPEN_LOCKED
    return locks


def boundary_quadrics(points, tris):
    """Plane through each open edge, perpendicular to its face, weighted by length^2 (n, 10)."""
    corners = np.concatenate((tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]))
    owner = np.tile(np.arange(len(tris)), 3)
    keyed = np.sort(corners, axis=1)
    _, inverse, counts = np.unique(keyed, axis=0, return_inverse=True, return_counts=True)
    open_ = counts[inverse.ravel()] == 1
    result = np.zeros((len(points), 10), dtype=np.float64)
    if not np.any(open_):
        return result
    a, b, f = corners[open_, 0], corners[open_, 1], owner[open_]
    p0 = points[tris[f, 0]]
    normal = np.cross(points[tris[f, 1]]-p0, points[tris[f, 2]]-p0)
    edge = points[b]-points[a]
    m = np.cross(edge, normal)
    length = np.sqrt(np.sum(m*m, axis=1))
    keep = length > 0
    a, b, edge, m, length = a[keep], b[keep], edge[keep], m[keep], length[keep]
    m = m/length[:, None]
    d = -np.sum(m*points[a], axis=1)
    w = np.sum(edge*edge, axis=1)
    x, y, z = m[:, 0], m[:, 1], m[:, 2]
    q = np.stack((w*x*x, w*x*y, w*x*z, w*x*d, w*y*y, w*y*z, w*y*d, w*z*z, w*z*d, w*d*d), axis=1)
    np.add.at(result, a, q)
    np.add.at(result, b, q)
    return result


def _buffers(points, tris, locks, python, relax=False):
    n, f = len(points), len(tris)
    q = vertex_quadrics(face_quadrics(points, tris), tris, n)
    # Pass 2 only moves open-boundary vertices, so closed meshes skip these quadrics.
    open_ = relax and bool(np.any(np.asarray(locks) == BOUNDARY))
    qb = boundary_quadrics(points, tris) if open_ else np.zeros((n, 10), dtype=np.float64)
    values = [points.ravel().copy(), q.ravel(), tris.ravel().copy(),
              np.asarray(locks, dtype=np.int64), np.ones(f, dtype=np.int64),
              np.ones(n, dtype=np.int64), np.full(n, -1, dtype=np.int64),
              np.full(3*f, -1, dtype=np.int64), np.zeros(n, dtype=np.int64),
              np.full(n, -1, dtype=np.int64), np.zeros(n, dtype=np.float64),
              np.full(n, -1, dtype=np.int64)]
    values.extend(np.zeros(n, dtype=np.int64) for _ in range(4))
    values.append(np.zeros(2, dtype=np.int64))
    values.extend((np.zeros(n, dtype=np.int64), np.zeros(n, dtype=np.float64),
                   np.zeros(n, dtype=np.int64), qb.ravel(), np.zeros(4, dtype=np.int64)))
    return [v.tolist() for v in values] if python else values


STATE = 21  # Index of the state buffer in _buffers.


def _run(kernel, buffers, target, relax=False):
    return kernel(*buffers[:4], target, *buffers[4:], int(relax))


def _compile():
    global _kernel, _version, _reason
    collecting = gc.isenabled()
    gc.disable()
    try:
        import numba
        ns = dict(globals())
        for function in (collapse_cost, face_flips, _ring, _degree, _det, _volume_delta,
                         _valid, _evaluate, _less, _sift, _remove, _update, _find, _fill,
                         _drain, _collapse):
            ns[function.__name__] = numba.njit(nogil=True)(
                types.FunctionType(function.__code__, ns, function.__name__))
        points = np.array([[1., 0., 0.], [-1., 0., 0.], [0., 1., 0.],
                           [0., -1., 0.], [0., 0., 1.], [0., 0., -1.]])
        tris = np.array([[0, 2, 4], [2, 1, 4], [1, 3, 4], [3, 0, 4],
                         [2, 0, 5], [1, 2, 5], [3, 1, 5], [0, 3, 5]], dtype=np.int64)
        kernel = ns['_collapse']
        _run(kernel, _buffers(points, tris, np.zeros(6, dtype=np.int64), False), 4)
        with _condition:
            _version = numba.__version__
            _kernel = kernel
    except Exception as error:
        with _condition:
            _reason = f'{type(error).__name__}: {error}'
    finally:
        if collecting:
            gc.enable()
        with _condition:
            _condition.notify_all()


def warm_up():
    global _thread
    with _condition:
        if ENABLED and _reason is None and _thread is None:
            _thread = threading.Thread(target=_compile, name='mesh-simplify-jit', daemon=True)
            _thread.start()


def status():
    with _condition:
        return dict(numba=_version, ready=_kernel is not None, reason=_reason)


def _euler(faces):
    if len(faces) == 0:
        return 0
    edges = np.unique(np.sort(np.concatenate((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]])), axis=1), axis=0)
    return len(np.unique(faces))-len(edges)+len(faces)


def _manifold(faces):
    """True for an oriented 2-manifold, possibly with boundary: no edge has more than two
    faces, a shared edge runs in opposite directions in its two faces, and the faces around
    every vertex form a single fan (no pinched vertices)."""
    if len(faces) == 0:
        return False
    a = faces.ravel()  # Corner c = 3*face+k sits at vertex a[c] ...
    b = faces[:, [1, 2, 0]].ravel()  # ... and starts the directed edge a[c] -> b[c].
    n = int(a.max())+1
    key = a*n+b
    order = np.argsort(key, kind='stable')
    ordered = key[order]
    if np.any(ordered[1:] == ordered[:-1]):
        return False  # A directed edge twice: more than two faces, or flipped orientation.
    twin = b*n+a
    at = np.minimum(np.searchsorted(ordered, twin), len(ordered)-1)
    paired = ordered[at] == twin
    # Corner c and the corner of the same vertex in the twin edge's face are neighbours in
    # that vertex's fan; label propagation joins each fan to its lowest corner index.
    p = np.nonzero(paired)[0]
    t = order[at[paired]]
    q = 3*(t//3)+(t % 3+1) % 3
    label = np.arange(len(a), dtype=np.int64)
    while True:
        new = label.copy()
        np.minimum.at(new, p, label[q])
        np.minimum.at(new, q, label[p])
        new = new[new]
        if np.array_equal(new, label):
            break
        label = new
    return len(np.unique(a*len(a)+label)) == len(np.unique(a))


def _cluster(P, low, faces, size):
    """Merge the points sharing a grid cell into the original point nearest the cell's
    centroid (ties by index). Only elementwise IEEE operations decide anything."""
    cell = np.floor((P-low)/size).astype(np.int64)
    _, label = np.unique(cell, axis=0, return_inverse=True)
    label = label.ravel()
    m = int(label.max())+1
    count = np.bincount(label, minlength=m)
    mean = np.stack([np.bincount(label, P[:, k], minlength=m)/count for k in range(3)], axis=1)
    d = P-mean[label]
    distance = d[:, 0]*d[:, 0]+d[:, 1]*d[:, 1]+d[:, 2]*d[:, 2]
    order = np.lexsort((np.arange(len(P)), distance, label))
    first = order[np.r_[True, label[order][1:] != label[order][:-1]]]
    rep = np.empty(m, dtype=np.int64)
    rep[label[first]] = first
    new = rep[label[faces]]
    new = new[(new[:, 0] != new[:, 1]) & (new[:, 1] != new[:, 2]) & (new[:, 0] != new[:, 2])]
    if len(new) == 0:
        return new
    # Faces now on the same three vertices: opposite orientations cancel in pairs (a
    # squashed sheet, which is how a handle closes); whatever orientation is left over
    # keeps its first face.
    a, b, c = new[:, 0], new[:, 1], new[:, 2]
    odd = ((a > b).astype(np.int64)+(a > c)+(b > c)) % 2
    _, group = np.unique(np.sort(new, axis=1), axis=0, return_inverse=True)
    group = group.ravel()
    g = int(group.max())+1
    net = np.bincount(group[odd == 0], minlength=g)-np.bincount(group[odd == 1], minlength=g)
    wanted = np.nonzero((net[group] != 0) & (odd == (net[group] < 0)))[0]
    _, index = np.unique(group[wanted], return_index=True)
    return new[np.sort(wanted[index])]


def _rim(faces):
    """The open (one-face) edges, as an (n, 2) array of sorted vertex pairs."""
    edges = np.sort(np.concatenate((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]])), axis=1)
    edges, counts = np.unique(edges, axis=0, return_counts=True)
    return edges[counts == 1]


def _open_edges(faces):
    return len(_rim(faces))


def _loops(faces):
    """Number of boundary loops (connected pieces of the open edges)."""
    rim = _rim(faces)
    if len(rim) == 0:
        return 0
    vertices, pairs = np.unique(rim, return_inverse=True)
    pairs = pairs.reshape(-1, 2)
    label = np.arange(len(vertices), dtype=np.int64)
    while True:
        new = label.copy()
        np.minimum.at(new, pairs[:, 0], label[pairs[:, 1]])
        np.minimum.at(new, pairs[:, 1], label[pairs[:, 0]])
        new = new[new]
        if np.array_equal(new, label):
            return len(np.unique(label))
        label = new


def _closed_count(before, after):
    """Holes plus handles closed between two manifold-ish meshes: each boundary loop gone is
    a hole (Euler characteristic +1), the rest of the rise counts two per handle."""
    holes = max(0, _loops(before)-_loops(after))
    rise = _euler(after)-_euler(before)
    return holes+max(0, (rise-holes)//2)


# Quarter-octave cell-size steps and grid offsets, written as literals so every platform
# builds the same grids (no pow or other libm calls).
_STEPS = (1.0, 1.189207115002721, 1.4142135623730951, 1.6817928305074290)
_SHIFTS = (0.0, 0.25, 0.5, 0.75)
_ROUNDS = 16


def _finest_closure(P, faces, target=MIN_TARGET):
    """A valid clustering of P's faces, or None. Cell sizes climb a fixed quarter-octave
    ladder (each at four grid offsets) from the finest size that merges anything. Valid:
    the Euler characteristic rose and there are no more open edges than before, so a
    degenerate or torn result is never used.

    The finest oriented manifold that keeps at least target faces wins, then the finest
    one that keeps at least half of target. A lattice or a detailed scan rarely has either
    (nearly every local merge pinches a vertex, and the first manifold is the whole model
    squashed into a few triangles), so otherwise it is the valid clustering with the
    fewest faces still at or above target, or failing that the one with the most below it. Pinched vertices only lock the collapses that follow.
    Ties go to the earlier rung, so every platform picks the same grid."""
    low = P.min(axis=0)
    high = P.max(axis=0)
    dx, dy, dz = (float(high[k])-float(low[k]) for k in range(3))
    diagonal = math.sqrt(dx*dx+dy*dy+dz*dz)
    if diagonal <= 0:
        return None
    before = _euler(faces)
    rim = _open_edges(faces)
    base = diagonal
    # Start where no grid offset merges anything: a shifted grid can merge points that an
    # unshifted cell boundary separates at a much finer size.
    for _ in range(64):
        if all(len(np.unique(np.floor((P-(low-base*shift))/base).astype(np.int64), axis=0)) == len(P)
               for shift in _SHIFTS):
            break
        base = base/2
    near = above = below = None
    sizes = (base*(2**octave)*step for octave in range(64) for step in _STEPS)
    for size in sizes:
        if size > 2*diagonal:
            break
        # Counts are not monotonic across offsets, so only a whole rung below half of
        # target ends the search (or the end of the ladder, at twice the diagonal).
        under = True
        for shift in _SHIFTS:
            new = _cluster(P, low-size*shift, faces, size)
            under = under and 2*len(new) < target
            if len(new) < MIN_TARGET or _euler(new) <= before or _open_edges(new) > rim:
                continue
            if len(new) >= target:
                if _manifold(new):
                    return new
                if above is None or len(new) < len(above):
                    above = new
            else:
                if near is None and 2*len(new) >= target and _manifold(new):
                    near = new
                if below is None or len(new) > len(below):
                    below = new
        if under and not (near is None and above is None and below is None):
            break
    for choice in (near, above, below):
        if choice is not None:
            return choice
    return None


def close_handles(points, kept, faces, target, refine):
    """Topology fallback: merge nearby kept vertices on a grid (each cell keeps the original
    vertex nearest its centroid), which closes holes and handles the collapses must keep.

    Each round takes a valid clustering near target (_finest_closure), then refine(kept, faces)
    runs the collapses again on it, until the target is reached or no valid clustering is
    left; the last valid state is returned. Returns (kept, faces, boundary collapses), or
    None when not even one round found a valid clustering."""
    slid, changed = 0, False
    for _ in range(_ROUNDS):
        if len(faces) <= target:
            break
        new = _finest_closure(points[kept], faces, target)
        if new is None:
            break
        used = np.unique(new)
        kept, faces = kept[used], np.searchsorted(used, new).astype(np.int64)
        changed = True
        if len(faces) > target:
            kept, faces, more = refine(kept, faces)
            slid += more
    return (kept, faces, slid) if changed else None


def simplify(points, triangles, target, locked=None, backend='auto', relax=False, progress=None):
    """Collapse to an original-vertex subset, preserving boundaries and locked vertices.

    relax (import): if the topology-preserving collapses stall above the target, slide
    open-boundary vertices along their boundary, then, if still above, close handles by
    clustering. The result reports what was given up. progress: a Progress to poll."""
    validate_target(target, len(triangles))
    if backend not in ('auto', 'numba', 'python'):
        raise ValueError('Unknown simplification backend: '+str(backend))
    points = np.ascontiguousarray(points, dtype=np.float64)
    tris = np.ascontiguousarray(triangles, dtype=np.int64)
    user = None if locked is None else np.asarray(locked, dtype=bool)
    kernel = None
    if backend != 'python':
        warm_up()
        with _condition:
            if backend == 'numba' or len(tris) > PYTHON_LIMIT:
                _condition.wait_for(lambda: _kernel is not None or _reason is not None)
            kernel = _kernel
            if backend == 'numba' and kernel is None:
                raise RuntimeError(_reason)

    def collapse(subset, faces):
        """Run the kernel on points[subset]; returns (kept, faces, boundary collapses)."""
        locks = _locks(faces, len(subset), None if user is None else user[subset])
        buffers = _buffers(points[subset], faces, locks, kernel is None, relax)
        if progress is not None:
            progress.source, progress.target = len(faces), target
            progress.state = buffers[STATE]
        _run(_collapse if kernel is None else kernel, buffers, target, relax)
        alive = np.asarray(buffers[2], dtype=np.int64).reshape(-1, 3)[np.asarray(buffers[4], dtype=bool)]
        used = np.unique(alive)
        if progress is not None and relax:
            progress.state = None  # A closure search may follow: indeterminate again.
        return subset[used], np.searchsorted(used, alive).astype(np.int64), int(buffers[STATE][2])
    kept, faces, slid = collapse(np.arange(len(points), dtype=np.int64), tris)
    floor = len(faces)
    handles, changed = 0, False
    if relax and len(faces) > target:
        if progress is not None:
            progress.stage = 'Closing holes…'
            progress.state = None
        closed = close_handles(points, kept, faces, target, collapse)
        if closed is not None:
            floor_faces = faces
            kept, faces, more = closed
            slid += more
            handles = _closed_count(floor_faces, faces)
            changed = True
    return Simplified(kept, faces, len(tris), 'python' if kernel is None else 'numba',
                      slid, handles, changed, floor)
