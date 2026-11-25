"""Original-vertex quadric collapse: geometry, topology and backend identity."""
import math
import unittest
import numpy as np

from phosphor.engine import mesh_simplify as ms
from phosphor.engine.mesh_simplify import face_quadrics, vertex_quadrics, collapse_cost, face_flips
from phosphor.engine.model_object import parse_obj, _weld, _triangles
from tests.fixtures.dense_mesh import dense_mesh_obj


def brute(points, tris, faces, p):
    total = 0.
    for f in faces:
        a, b, c = points[tris[f]]
        n = np.cross(b-a, c-a)
        length = np.linalg.norm(n)
        if length == 0:
            continue
        total += length/2*(np.dot(n/length, p-a))**2
    return total


class HelperTests(unittest.TestCase):
    def setUp(self):
        r = np.random.default_rng(1)
        self.points = r.normal(size=(30, 3))
        self.tris = r.integers(0, 30, size=(37, 3)).astype(np.int64)
        self.tris[5] = [3, 3, 4]

    def test_face(self):
        q = face_quadrics(self.points, self.tris)
        self.assertEqual(q.shape, (37, 10))
        self.assertEqual(q.dtype, np.float64)
        self.assertTrue(np.all(q[5] == 0))
        p = np.array([.3, -.2, .7])
        v = np.array([*p, 1.])
        for f in range(37):
            c = q[f]
            matrix = np.array([[c[0], c[1], c[2], c[3]], [c[1], c[4], c[5], c[6]],
                               [c[2], c[5], c[7], c[8]], [c[3], c[6], c[8], c[9]]])
            self.assertAlmostEqual(v@matrix@v, brute(self.points, self.tris, [f], p), 10)

    def test_vertex(self):
        fq = face_quadrics(self.points, self.tris)
        vq = vertex_quadrics(fq, self.tris, 32)
        self.assertEqual(vq.shape, (32, 10))
        self.assertTrue(np.all(vq[30:] == 0))
        for i in range(30):
            ref = np.zeros(10)
            for k in range(3):
                for f in range(37):
                    if self.tris[f, k] == i:
                        ref = ref+fq[f]
            self.assertTrue(np.array_equal(vq[i], ref), i)

    def test_cost(self):
        fq = face_quadrics(self.points, self.tris)
        vq = vertex_quadrics(fq, self.tris, 30)
        q, pts = vq.ravel().tolist(), self.points.ravel().tolist()
        faces = lambda i: [f for f in range(37) if i in self.tris[f]]
        for keep, remove in [(0, 1), (7, 3), (12, 12), (29, 0)]:
            got = collapse_cost(q, pts, keep, remove)
            self.assertIsInstance(got, float)
            fs = faces(keep)+faces(remove)
            self.assertAlmostEqual(got, brute(self.points, self.tris, fs, self.points[keep]), 9)
            self.assertEqual(got, collapse_cost(vq.ravel(), self.points.ravel(), keep, remove))

    def test_flip(self):
        pts = [0., 0., 0., 1., 0., 0., 0., 1., 0., .2, .2, 0., -1., -1., 0.,
               0., 0., 1., 1., 1., 0., 0., 0., 0.]
        self.assertFalse(face_flips(pts, 0, 1, 2, 0, 3))
        self.assertTrue(face_flips(pts, 0, 1, 2, 0, 6))
        self.assertTrue(face_flips(pts, 1, 2, 0, 0, 6))
        self.assertTrue(face_flips(pts, 2, 0, 1, 0, 6))
        self.assertFalse(face_flips(pts, 2, 0, 1, 0, 4))
        self.assertTrue(face_flips(pts, 0, 1, 2, 2, 1))
        self.assertFalse(face_flips(pts, 0, 1, 7, 7, 2))
        self.assertTrue(face_flips(pts, 0, 1, 2, 2, 5))


def sphere(segments=24, bands=16, scale=(1., 1., 1.)):
    points = [(0., 0., 1.)]
    for i in range(1, bands):
        theta = math.pi*i/bands
        for j in range(segments):
            phi = 2*math.pi*j/segments
            points.append((math.sin(theta)*math.cos(phi), math.sin(theta)*math.sin(phi), math.cos(theta)))
    south = len(points)
    points.append((0., 0., -1.))
    faces = []
    for j in range(segments):
        k = (j+1)%segments
        faces.append((0, 1+j, 1+k))
        for i in range(bands-2):
            a, b = 1+i*segments+j, 1+(i+1)*segments+j
            c, d = 1+(i+1)*segments+k, 1+i*segments+k
            faces.extend(((a, b, c), (a, c, d)))
        faces.append((south, 1+(bands-2)*segments+k, 1+(bands-2)*segments+j))
    return np.asarray(points)*scale, np.asarray(faces, dtype=np.int64)


def torus(rings=32, segments=16):
    points, faces = [], []
    for i in range(rings):
        u = 2*math.pi*i/rings
        for j in range(segments):
            v = 2*math.pi*j/segments
            points.append(((2+.7*math.cos(v))*math.cos(u),
                           (2+.7*math.cos(v))*math.sin(u), .7*math.sin(v)))
            a, b = i*segments+j, ((i+1)%rings)*segments+j
            c, d = ((i+1)%rings)*segments+(j+1)%segments, i*segments+(j+1)%segments
            faces.extend(((a, b, c), (a, c, d)))
    return np.asarray(points), np.asarray(faces, dtype=np.int64)


def grid(size=12):
    points = np.array([(i, j, 0.) for i in range(size+1) for j in range(size+1)])
    faces = []
    for i in range(size):
        for j in range(size):
            a = i*(size+1)+j
            b, d, c = a+size+1, a+1, a+size+2
            faces.extend(((a, b, c), (a, c, d)))
    return points, np.asarray(faces, dtype=np.int64)


def cube(size=8):
    points, faces = [], []
    plane, triangles = grid(size)
    for axis in range(3):
        for side in (-1, 1):
            offset = len(points)
            for x, y, _ in plane:
                p = [0., 0., 0.]
                p[axis] = float(side)
                p[(axis+1)%3] = 2*x/size-1
                p[(axis+2)%3] = 2*y/size-1
                points.append(p)
            for a, b, c in triangles:
                faces.append((offset+a, offset+b, offset+c) if side == 1 else
                             (offset+a, offset+c, offset+b))
    points, welded = _weld(points)
    return points, _triangles(np.asarray(faces, dtype=np.int64), welded)


def dense():
    vertices, faces, _ = parse_obj(dense_mesh_obj())
    points, welded = _weld(vertices)
    return points, _triangles(faces, welded)


def edges(faces):
    pairs = np.sort(np.concatenate((faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]])), axis=1)
    return np.unique(pairs, axis=0, return_counts=True)


class SimplifyTests(unittest.TestCase):
    def assert_subset(self, points, result):
        kept = result.kept_vertex_indices
        self.assertEqual(kept.dtype, np.int64)
        self.assertTrue(np.array_equal(kept, np.unique(kept)))
        self.assertTrue(np.all((kept >= 0) & (kept < len(points))))
        self.assertTrue(np.array_equal(points[kept][result.faces], points[kept[result.faces]]))

    def test_targets_normals_and_closed_topology(self):
        for name, fixture, minimum, euler in [('sphere', sphere(), 4, 2),
                                               ('torus', torus(), 32, 0)]:
            points, faces = fixture
            for target in (len(faces)//2, len(faces)//4, len(faces)//10, minimum):
                with self.subTest(name=name, target=target):
                    result = ms.simplify(points, faces, target, backend='python')
                    self.assertLessEqual(result.result_triangles, target)
                    self.assertGreaterEqual(result.result_triangles, target-1)
                    self.assert_subset(points, result)
                    p = points[result.kept_vertex_indices][result.faces]
                    normal = np.cross(p[:, 1]-p[:, 0], p[:, 2]-p[:, 0])
                    centre = p.mean(axis=1)
                    outward = centre.copy()
                    if name == 'torus':
                        radius = np.sqrt(centre[:, 0]**2+centre[:, 1]**2)
                        outward[:, :2] -= 2*centre[:, :2]/radius[:, None]
                    if target >= len(faces)//4:
                        # Outward by position only while the mesh still follows the
                        # surface; coarser meshes have slivers in meridian planes.
                        self.assertTrue(np.all(np.sum(normal*outward, axis=1) > 0))
                    # Collapses keep winding, so the closed surface stays consistently
                    # oriented and encloses positive volume.
                    directed = np.concatenate((result.faces[:, [0, 1]], result.faces[:, [1, 2]], result.faces[:, [2, 0]]))
                    self.assertEqual(len(np.unique(directed, axis=0)), len(directed))
                    volume = np.sum(p[:, 0]*np.cross(p[:, 1], p[:, 2]))
                    self.assertGreater(volume, 0)
                    pairs, counts = edges(result.faces)
                    self.assertTrue(np.all(counts == 2))
                    self.assertEqual(len(result.kept_vertex_indices)-len(pairs)+len(result.faces), euler)

    def test_open_boundary_cycle_and_locked_interior(self):
        points, faces = grid()
        pairs, counts = edges(faces)
        boundary = pairs[counts == 1]
        locked = np.zeros(len(points), dtype=bool)
        locked[len(points)//2] = True
        result = ms.simplify(points, faces, len(faces)//4, locked, backend='python')
        self.assert_subset(points, result)
        self.assertIn(len(points)//2, result.kept_vertex_indices)
        self.assertTrue(np.all(np.isin(np.unique(boundary), result.kept_vertex_indices)))
        pairs, counts = edges(result.kept_vertex_indices[result.faces])
        # Exact boundary edges retain the entire original cycle and its cyclic order.
        np.testing.assert_array_equal(pairs[counts == 1], boundary)

    def test_cube_corners_and_bounds(self):
        points, faces = cube()
        result = ms.simplify(points, faces, 48, backend='python')
        self.assertIn(result.result_triangles, (47, 48))
        corners = np.flatnonzero(np.all(np.abs(points) == 1, axis=1))
        self.assertEqual(len(corners), 8)
        self.assertTrue(np.all(np.isin(corners, result.kept_vertex_indices)))
        kept = points[result.kept_vertex_indices]
        np.testing.assert_array_equal(kept.min(axis=0), points.min(axis=0))
        np.testing.assert_array_equal(kept.max(axis=0), points.max(axis=0))

    def test_nonmanifold_locks_and_input_immutability(self):
        # Three sheets share the edge (0, 1). Every other edge is an open boundary,
        # so this deliberately unsimplifiable mesh must stop early, unchanged.
        points = np.array([(0., 0., 0.), (1., 0., 0.), (0., 1., 0.),
                           (0., -1., 0.), (0., 0., 1.), (1., 1., 0.),
                           (1., -1., 0.), (1., 0., 1.)])
        faces = np.array([(0, 1, 2), (1, 5, 2), (1, 0, 3),
                          (0, 6, 3), (0, 1, 4), (1, 7, 4)], dtype=np.int64)
        before_points, before_faces = points.copy(), faces.copy()
        result = ms.simplify(points, faces, 4, backend='python')
        self.assertEqual(result.result_triangles, 6)
        np.testing.assert_array_equal(result.kept_vertex_indices[result.faces], faces)
        np.testing.assert_array_equal(points, before_points)
        np.testing.assert_array_equal(faces, before_faces)

    def test_python_determinism_all_fixtures(self):
        for fixture in (sphere(), sphere(scale=(1., 1.5, .7)), torus(), cube(), grid(), dense()):
            points, faces = fixture
            target = max(4, len(faces)//4)
            a = ms.simplify(points, faces, target, backend='python')
            b = ms.simplify(points, faces, target, backend='python')
            self.assert_subset(points, a)
            np.testing.assert_array_equal(a.kept_vertex_indices, b.kept_vertex_indices)
            np.testing.assert_array_equal(a.faces, b.faces)

    def test_backends_identical_and_numba_deterministic(self):
        points, faces = sphere()
        try:
            ms.simplify(points, faces, 100, backend='numba')
        except RuntimeError:
            self.skipTest(ms.status()['reason'])
        for fixture in (sphere(), sphere(scale=(1., 1.5, .7)), torus(), cube(), grid(), dense()):
            points, faces = fixture
            for target in (len(faces)//2, len(faces)//4, max(4, len(faces)//10)):
                with self.subTest(vertices=len(points), target=target):
                    a = ms.simplify(points, faces, target, backend='python')
                    b = ms.simplify(points, faces, target, backend='numba')
                    c = ms.simplify(points, faces, target, backend='numba')
                    self.assertEqual(b.backend, 'numba')
                    for other in (b, c):
                        np.testing.assert_array_equal(a.kept_vertex_indices, other.kept_vertex_indices)
                        np.testing.assert_array_equal(a.faces, other.faces)

    def test_bad_targets(self):
        points, faces = sphere()
        for target in (3, 0, -1, True, 4.5, '10', len(faces), len(faces)+1):
            with self.subTest(target=target), self.assertRaises(ValueError):
                ms.simplify(points, faces, target, backend='python')
        with self.assertRaisesRegex(ValueError, 'cannot be simplified'):
            ms.validate_target(4, 4)


def jittered_plane(seed, size=16, amount=.3):
    """Flat grid with jittered interior vertices: every collapse costs 0, so only the
    flip guard keeps faces from folding over."""
    rng = np.random.default_rng(seed)
    x, y = np.meshgrid(np.arange(size+1.), np.arange(size+1.), indexing='ij')
    points = np.stack([x.ravel(), y.ravel(), np.zeros(x.size)], 1)
    inner = (x.ravel() > 0) & (x.ravel() < size) & (y.ravel() > 0) & (y.ravel() < size)
    points[inner, :2] += rng.uniform(-amount, amount, (int(inner.sum()), 2))
    faces = []
    for i in range(size):
        for j in range(size):
            a = i*(size+1)+j
            b = a+size+1
            faces += [[a, b, a+1], [a+1, b, b+1]] if (i+j) % 2 else [[a, b, b+1], [a, b+1, a+1]]
    return points, np.array(faces, dtype=np.int64)


class FlipGuardTests(unittest.TestCase):
    @staticmethod
    def normal_z(points, result):
        p = points[result.kept_vertex_indices][result.faces]
        return np.cross(p[:, 1]-p[:, 0], p[:, 2]-p[:, 0])[:, 2]

    def test_guard_prevents_folds_that_happen_without_it(self):
        for seed in range(3):
            with self.subTest(seed=seed):
                points, faces = jittered_plane(seed)
                p = points[faces]
                self.assertTrue(np.all(np.cross(p[:, 1]-p[:, 0], p[:, 2]-p[:, 0])[:, 2] > 0))
                guarded = ms.simplify(points, faces, 120, backend='python')
                self.assertTrue(np.all(self.normal_z(points, guarded) > 0))
                original = ms.face_flips
                ms.face_flips = lambda *args: False
                try:
                    unguarded = ms.simplify(points, faces, 120, backend='python')
                finally:
                    ms.face_flips = original
                self.assertTrue(np.any(self.normal_z(points, unguarded) <= 0))


# The 5-vertex bipyramid the sphere fixture reached on macOS arm64 just before folding:
# collapsing vertex 2 into 0 turns every face by under 90 degrees, yet the tetrahedron
# it leaves is inside out.
BIPYRAMID = (np.array([[0.35355339, 0.61237244, 0.70710678],
                       [-0.80010315, -0.46193977, 0.38268343],
                       [0.65328148, -0.65328148, 0.38268343],
                       [0.80010315, 0.46193977, -0.38268343],
                       [-0.5, -0.5, -0.70710678]]),
             np.array([[2, 3, 0], [0, 3, 1], [1, 3, 4], [0, 1, 2], [1, 4, 2], [2, 4, 3]],
                      dtype=np.int64))


def volume(points, result):
    p = points[result.kept_vertex_indices][result.faces]
    return float(np.sum(p[:, 0]*np.cross(p[:, 1], p[:, 2])))


class VolumeGuardTests(unittest.TestCase):
    def test_collapse_that_turns_a_closed_surface_inside_out_is_refused(self):
        points, faces = BIPYRAMID
        b = ms._buffers(points, faces, ms._locks(faces, len(points), None), True)
        pts, q, tris, locks, alive, verts, head, nxt = b[:8]
        ring_a, ring_b, mark, counter, comp, vol, closed = b[12], b[13], b[15], b[16], b[17], b[18], b[19]
        for c in range(len(tris)):
            nxt[c] = head[tris[c]]
            head[tris[c]] = c
        total = sum(ms._det(pts, *tris[3*f:3*f+3]) for f in range(len(faces)))
        self.assertGreater(total, 0)
        vol[0] = total
        closed[0] = 1
        # Every surviving face passes the per-face flip test ...
        for a, bb, d in faces:
            if 2 in (a, bb, d) and 0 not in (a, bb, d):
                self.assertFalse(ms.face_flips(pts, a, bb, d, 2, 0))
        # ... but the enclosed volume would change sign, so the collapse is invalid.
        self.assertLessEqual(total+ms._volume_delta(2, 0, pts, tris, alive, head, nxt), 0)
        na = ms._ring(2, tris, alive, head, nxt, ring_a, mark, counter)
        self.assertFalse(ms._valid(2, 0, pts, tris, alive, head, nxt, ring_a, na, ring_b,
                                   mark, counter, locks, comp, vol, closed))
        closed[0] = 0  # Without the guard the same collapse is accepted.
        self.assertTrue(ms._valid(2, 0, pts, tris, alive, head, nxt, ring_a, na, ring_b,
                                  mark, counter, locks, comp, vol, closed))

    def test_closed_fixtures_keep_positive_volume_down_to_the_minimum(self):
        for name, (points, faces) in (('sphere', sphere()), ('ellipsoid', sphere(scale=(1., 1.5, .7))),
                                      ('cube', cube()), ('bipyramid', BIPYRAMID)):
            for target in (4, 5, 6, 8, 12):
                if target >= len(faces):
                    continue
                with self.subTest(name=name, target=target):
                    result = ms.simplify(points, faces, target, backend='python')
                    self.assertGreater(volume(points, result), 0)


class RelaxedImportTests(unittest.TestCase):
    def test_boundary_pass_runs_only_when_the_strict_pass_stalls(self):
        points, faces = grid()
        strict = ms.simplify(points, faces, 8, backend='python')
        self.assertGreater(strict.result_triangles, 8)
        relaxed = ms.simplify(points, faces, 8, backend='python', relax=True)
        self.assertLessEqual(relaxed.result_triangles, 8)
        self.assertGreater(relaxed.boundary_collapses, 0)
        self.assertFalse(relaxed.topology_changed)
        # Reachable targets give exactly the strict result: relax adds nothing.
        easy = ms.simplify(points, faces, len(faces)//4, backend='python', relax=True)
        plain = ms.simplify(points, faces, len(faces)//4, backend='python')
        np.testing.assert_array_equal(easy.faces, plain.faces)
        np.testing.assert_array_equal(easy.kept_vertex_indices, plain.kept_vertex_indices)
        self.assertEqual(easy.boundary_collapses, 0)

    def test_handles_close_only_below_the_topological_floor(self):
        points, faces = torus()
        strict = ms.simplify(points, faces, 8, backend='python')
        self.assertGreater(strict.result_triangles, 8)
        relaxed = ms.simplify(points, faces, 8, backend='python', relax=True)
        self.assertTrue(relaxed.topology_changed)
        self.assertEqual(relaxed.handles_closed, 1)
        self.assertEqual(relaxed.floor_triangles, strict.result_triangles)
        self.assertLessEqual(relaxed.result_triangles, 8)
        self.assertGreaterEqual(relaxed.result_triangles, ms.MIN_TARGET)
        kept = relaxed.kept_vertex_indices
        self.assertTrue(np.all(np.diff(kept) > 0))
        self.assertTrue(np.all(np.isin(kept, np.arange(len(points)))))

    def test_relaxed_backends_identical(self):
        try:
            ms.simplify(*sphere(), 100, backend='numba')
        except RuntimeError:
            self.skipTest(ms.status()['reason'])
        for fixture in (grid(), torus(), sphere(), perforated()):
            points, faces = fixture
            for target in (8, max(4, len(faces)//10)):
                with self.subTest(vertices=len(points), target=target):
                    a = ms.simplify(points, faces, target, backend='python', relax=True)
                    b = ms.simplify(points, faces, target, backend='numba', relax=True)
                    np.testing.assert_array_equal(a.kept_vertex_indices, b.kept_vertex_indices)
                    np.testing.assert_array_equal(a.faces, b.faces)
                    self.assertEqual(a.boundary_collapses, b.boundary_collapses)
                    self.assertEqual(a.handles_closed, b.handles_closed)

    def test_import_report_names_what_was_given_up(self):
        from phosphor.engine import model_object
        points, faces = torus()
        model, report = model_object.model_from_mesh(points, faces.tolist(), [], 'torus', 'OBJ', 8)
        self.assertTrue(report.simplified and report.topology_changed)
        self.assertEqual(report.handles_closed, 1)
        self.assertLessEqual(report.result_triangles, 8)
        points, faces = grid()
        model, report = model_object.model_from_mesh(points, faces.tolist(), [], 'grid', 'OBJ', 8)
        self.assertGreater(report.boundary_collapses, 0)
        self.assertFalse(report.topology_changed)
        self.assertEqual((model_object.MAX_MODEL_FACES, model_object.MAX_MODEL_BYTES),
                         (2_000_000, 512*1024*1024))

    def test_progress_reports_the_kernel_face_count(self):
        points, faces = sphere()
        progress = ms.Progress()
        self.assertIsNone(progress.fraction())
        result = ms.simplify(points, faces, 100, backend='python', progress=progress)
        self.assertEqual(int(progress.state[0]), result.result_triangles)
        self.assertEqual(progress.fraction(), 1.0)


TETRA = np.array([[0, 2, 1], [0, 1, 3], [1, 2, 3], [0, 3, 2]], dtype=np.int64)


def perforated(size=36, period=6):
    """A flat plate with a square hole in every period x period block (open everywhere)."""
    points = np.array([(i, j, 0.) for i in range(size+1) for j in range(size+1)])
    faces = []
    for i in range(size):
        for j in range(size):
            if i % period in (2, 3) and j % period in (2, 3):
                continue
            a = i*(size+1)+j
            faces.extend(((a, a+size+1, a+size+2), (a, a+size+2, a+1)))
    return points, np.asarray(faces, dtype=np.int64)


def scaffold(cells=3, period=3):
    """A closed voxel scaffold: struts along all three axes every period voxels. One
    connected surface of high genus, like a printed lattice."""
    n = cells*period+1
    filled = np.zeros((n, n, n), dtype=bool)
    i, j, k = np.indices(filled.shape)
    on = (i % period == 0).astype(int)+(j % period == 0)+(k % period == 0)
    filled[on >= 2] = True
    index, points, faces = {}, [], []
    def vertex(p):
        if p not in index:
            index[p] = len(points); points.append(p)
        return index[p]
    for x, y, z in zip(*np.nonzero(filled)):
        for axis in range(3):
            for sign in (-1, 1):
                q = [x, y, z]; q[axis] += sign
                if 0 <= q[axis] < n and filled[tuple(q)]:
                    continue
                # Quad on the face of the voxel cube [x,x+1]^3 facing axis*sign, wound outward.
                c = [x, y, z]; c[axis] += (sign > 0)
                u, v = (axis+1) % 3, (axis+2) % 3
                corners = []
                for du, dv in ((0, 0), (1, 0), (1, 1), (0, 1)):
                    p = list(c); p[u] += du; p[v] += dv; corners.append(vertex(tuple(p)))
                if sign < 0:
                    corners = corners[::-1]
                a, b, cc, d = corners
                faces.extend(((a, b, cc), (a, cc, d)))
    return np.asarray(points, dtype=float), np.asarray(faces, dtype=np.int64)


def ulp_nudged(points, seed):
    """Move about half the coordinates by one ulp: the drift between platforms' libm."""
    rng = np.random.default_rng(seed)
    out = points.copy()
    mask = rng.random(out.shape) < .5
    out[mask] = np.nextafter(out[mask], np.where(rng.random(int(mask.sum())) < .5, np.inf, -np.inf))
    return out


class ClosureTests(unittest.TestCase):
    """close_handles never tears or degenerates the mesh, keeps simple shapes oriented
    manifolds, stops near the target and counts what it closed."""

    def test_manifold_check(self):
        self.assertTrue(ms._manifold(TETRA))
        self.assertTrue(ms._manifold(TETRA[:3]))  # An open fan.
        flipped = TETRA.copy()
        flipped[0] = flipped[0, ::-1]
        self.assertFalse(ms._manifold(flipped))
        pinched = np.vstack((TETRA, [[0, 5, 4], [0, 4, 6], [4, 5, 6], [0, 6, 5]]))
        self.assertFalse(ms._manifold(pinched))  # Two tetrahedra sharing only vertex 0.
        fin = np.vstack((TETRA, [[0, 1, 7]]))
        self.assertFalse(ms._manifold(fin))  # Three faces on edge 0-1.

    def test_squashed_sheets_cancel(self):
        points = np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0], [.01, .01, 0]])
        low = points.min(axis=0)-.5
        # Vertex 3 merges into 0 (equal distances to the centroid: the lower index wins).
        # [1, 3, 2] becomes [1, 0, 2], the opposite of [0, 1, 2], so both go.
        faces = np.array([[0, 1, 2], [1, 3, 2]], dtype=np.int64)
        self.assertEqual(len(ms._cluster(points, low, faces, .1)), 0)
        # Same orientation: a duplicate, kept once.
        faces = np.array([[0, 1, 2], [3, 1, 2]], dtype=np.int64)
        np.testing.assert_array_equal(ms._cluster(points, low, faces, .1), [[0, 1, 2]])

    def test_shifted_grids_are_searched_from_their_own_finest_size(self):
        # Vertices 0 and 4 are .02 apart but straddle the unshifted grid's cell boundary at
        # every size down to the start; only a shifted grid joins them into a tetrahedron.
        points = np.array([[1.49, .2, .2], [0, 0, 0], [2, 2, 0], [0, 0, 1], [1.51, .2, .2]])
        faces = np.array([[4, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]], dtype=np.int64)
        new = ms._finest_closure(points, faces)
        self.assertIsNotNone(new)
        self.assertEqual((ms._euler(new), ms._open_edges(new)), (2, 0))
        self.assertEqual(sorted(set(new.ravel().tolist())), [0, 1, 2, 3])

    def test_closure_rounds_leave_progress_indeterminate(self):
        progress = ms.Progress()
        result = ms.simplify(*perforated(), 40, backend='python', relax=True, progress=progress)
        self.assertTrue(result.topology_changed)
        self.assertIsNone(progress.fraction())

    def test_closure_stops_near_the_target_on_a_lattice(self):
        # Nearly every local merge on a lattice pinches a vertex; the only manifold
        # clustering is the whole scaffold squashed into a few triangles. The closure must
        # take a pinched one near the target instead of that.
        points, faces = scaffold(2, 3)
        floor = ms.simplify(points, faces, 4, backend='python').result_triangles
        for target in (floor*2//3, floor//3):
            with self.subTest(target=target):
                result = ms.simplify(points, faces, target, backend='python', relax=True)
                self.assertTrue(result.topology_changed)
                self.assertLessEqual(result.result_triangles, target)
                self.assertGreaterEqual(result.result_triangles, target*3//4)
                self.assertGreater(result.handles_closed, 0)

    def test_closure_survives_platform_drift(self):
        # The Mac's sin/cos differ from Windows' by an ulp; the result must stay a closed
        # sphere with exactly one handle closed whatever the drift.
        points, faces = torus()
        for seed in range(12):
            with self.subTest(seed=seed):
                result = ms.simplify(ulp_nudged(points, seed), faces, 8, backend='python', relax=True)
                self.assertLessEqual(result.result_triangles, 8)
                self.assertTrue(ms._manifold(result.faces))
                self.assertEqual(ms._euler(result.faces), 2)
                self.assertEqual(result.handles_closed, 1)

    def test_holes_close_gradually_and_are_counted(self):
        points, faces = perforated()
        strict = ms.simplify(points, faces, 40, backend='python')
        loops = ms._loops(strict.faces)
        self.assertEqual(loops, 1+36)  # The outline and every hole survive the strict pass.
        previous = 0
        for target in (300, 120, 40):
            with self.subTest(target=target):
                result = ms.simplify(points, faces, target, backend='python', relax=True)
                self.assertLessEqual(result.result_triangles, target)
                self.assertGreater(result.result_triangles, target//2)  # No coarse overshoot.
                self.assertTrue(ms._manifold(result.faces))
                self.assertLessEqual(ms._open_edges(result.faces), ms._open_edges(strict.faces))
                self.assertGreaterEqual(result.handles_closed, previous)
                self.assertLessEqual(result.handles_closed, 36)
                previous = result.handles_closed

    def test_relaxed_results_are_manifold(self):
        for name, (points, faces) in (('torus', torus()), ('sphere', sphere()), ('cube', cube()),
                                      ('grid', grid()), ('perforated', perforated())):
            for target in (4, 8, 20, len(faces)//10):
                with self.subTest(name=name, target=target):
                    result = ms.simplify(points, faces, target, backend='python', relax=True)
                    self.assertTrue(ms._manifold(result.faces))
                    kept = result.kept_vertex_indices
                    self.assertTrue(np.all(np.diff(kept) > 0))


if __name__ == '__main__':
    unittest.main()
