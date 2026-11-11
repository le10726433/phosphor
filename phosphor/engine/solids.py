"""Platonic solids, polygons, curved wireframes, and closed drawing routes."""
from functools import lru_cache
import numpy as np

PLATONIC_SOLIDS = ('Cube', 'Tetrahedron', 'Octahedron', 'Dodecahedron', 'Icosahedron')
POLYGONS = {'Triangle (2D)': 3, 'Square (2D)': 4, 'Pentagon (2D)': 5}
CURVES = ('Tilting circle', 'Octant sphere')
SOLIDS = (*PLATONIC_SOLIDS, *POLYGONS, *CURVES)


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


@lru_cache(maxsize=None)
def geometry(name):
    if name == 'Tilting circle':
        return ring_graph(((0, 1),), 128)
    if name == 'Octant sphere':
        return ring_graph(((0, 1), (0, 2), (1, 2)), 96)
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
    vertices, edges = geometry(name)
    if name in POLYGONS:
        return np.array([*range(len(vertices)), 0])
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
