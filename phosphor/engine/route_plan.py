"""Shorter closed beam routes for any line graph (signal preference: route minimizing).

Every shape is a set of points and drawn lines that one closed route must cover;
stereo X/Y has no blanking, so jumps between separate parts are visible travel and
repeated lines are drawn again. Two methods, kept separate so they can be compared:

- SHORTEST_JUMPS keeps how each connected part is traced (once round if every
  point has even degree, otherwise a depth-first walk that retraces), and visits the
  parts in a nearest-first tour instead of returning to the start after each one.
- SHORTEST_PATH minimizes the whole route (a route-inspection / Chinese-postman
  plan): every odd-degree point is paired with another, each pair joined by the
  shorter of retracing the existing lines between them or a straight jump; separate
  groups are then linked by their shortest jumps, and an Euler circuit traces it.

A plan is used only if it is shorter than the shape's original route, so no mode
can make a shape longer. Pure geometry: no Qt, deterministic for given input.
"""
import heapq
import math

import numpy as np

ROUTE_OFF = 'Off (original routes)'
SHORTEST_JUMPS = 'Shortest jumps'
SHORTEST_PATH = 'Shortest total path'
ROUTE_MODES = (ROUTE_OFF, SHORTEST_JUMPS, SHORTEST_PATH)
MAX_PLAN_VERTICES = 20000   # Larger graphs keep their original route.
MAX_ODD = 600               # Pairing cost grows with the square of odd points.


def route_length(vertices, route):
    route = np.asarray(route, dtype=int)
    if len(route) < 2: return 0.
    return float(np.linalg.norm(np.diff(np.asarray(vertices, dtype=float)[route], axis=0), axis=1).sum())


def _components(count, adjacency, used):
    seen = set(); result = []
    for start in sorted(used):
        if start in seen: continue
        stack = [start]; part = []
        seen.add(start)
        while stack:
            v = stack.pop(); part.append(v)
            for n, _ in adjacency[v]:
                if n not in seen: seen.add(n); stack.append(n)
        result.append(sorted(part))
    return result


def _euler(adjacency_lists, start):
    """Hierholzer on a multigraph given as {v: [(n, edge id), ...]}; all degrees even."""
    used = set(); pointer = {v: 0 for v in adjacency_lists}
    stack = [start]; circuit = []
    while stack:
        v = stack[-1]; options = adjacency_lists[v]; i = pointer[v]
        while i < len(options) and options[i][1] in used: i += 1
        pointer[v] = i
        if i == len(options): circuit.append(stack.pop())
        else:
            n, e = options[i]; used.add(e); stack.append(n)
    return circuit[::-1]


def _component_walk(adjacency, component, root):
    """The editor's original per-part tracing, rooted at root (see custom_shape.compiled)."""
    if all(len(adjacency[v]) % 2 == 0 for v in component):
        return _euler({v: adjacency[v] for v in component}, root)
    visited = set(); walk = [root]; stack = [(root, iter(adjacency[root]))]
    while stack:
        v, neighbours = stack[-1]
        option = next(((n, k) for n, k in neighbours if k not in visited), None)
        if option is None:
            stack.pop()
            if stack: walk.append(stack[-1][0])
        else:
            n, k = option; visited.add(k); walk.append(n); stack.append((n, iter(adjacency[n])))
    return walk


def _dijkstra(adjacency, lengths, source, targets):
    """Shortest path lengths and predecessor edges from source to the target set."""
    best = {source: 0.}; parent = {}; queue = [(0., source)]; remaining = set(targets)
    while queue and remaining:
        d, v = heapq.heappop(queue)
        if d > best.get(v, math.inf): continue
        remaining.discard(v)
        for n, e in adjacency[v]:
            nd = d+lengths[e]
            if nd < best.get(n, math.inf): best[n] = nd; parent[n] = (v, e); heapq.heappush(queue, (nd, n))
    return best, parent


def _match(odd, cost):
    """Greedy minimum pairing, then pairwise swaps until no swap shortens it."""
    pairs = []; free = set(odd)
    for c, a, b in sorted((cost(a, b), a, b) for i, a in enumerate(odd) for b in odd[i+1:]):
        if a in free and b in free: pairs.append([a, b]); free -= {a, b}
    improved = True; rounds = 0
    while improved and rounds < 20:
        improved = False; rounds += 1
        for i in range(len(pairs)):
            for j in range(i+1, len(pairs)):
                (a, b), (c, d) = pairs[i], pairs[j]
                now = cost(a, b)+cost(c, d)
                for x, y in (((a, c), (b, d)), ((a, d), (b, c))):
                    if cost(*x)+cost(*y) < now-1e-12:
                        pairs[i], pairs[j] = list(x), list(y); now = cost(*x)+cost(*y); improved = True
                        (a, b), (c, d) = pairs[i], pairs[j]
    return pairs


def _shortest_jumps(points, adjacency, components, start):
    order = [c for c in components if start in c]+[c for c in components if start not in c]
    route = _component_walk(adjacency, order[0], start); jumps = []
    position = start; left = order[1:]
    while left:
        # The nearest unvisited part, entered (and left again) at its closest point.
        best = None
        for index, part in enumerate(left):
            distance = np.linalg.norm(points[part]-points[position], axis=1)
            k = int(np.argmin(distance))
            if best is None or distance[k] < best[0]: best = (distance[k], index, part[k])
        _, index, root = best; part = left.pop(index)
        jumps.append((position, root)); route.extend(_component_walk(adjacency, part, root)); position = root
    if position != start: jumps.append((position, start)); route.append(start)
    return route, jumps


def _shortest_path(points, edges, adjacency, components, start):
    lengths = [float(np.linalg.norm(points[a]-points[b])) for a, b in edges]
    part_of = {v: i for i, part in enumerate(components) for v in part}
    odd = sorted(v for part in components for v in part if len(adjacency[v]) % 2)
    if len(odd) > MAX_ODD: return None
    paths = {}
    for v in odd:
        same = [u for u in odd if part_of[u] == part_of[v] and u != v]
        if same: paths[v] = _dijkstra(adjacency, lengths, v, same)
    def cost(a, b):
        jump = float(np.linalg.norm(points[a]-points[b]))
        if part_of[a] == part_of[b]: return min(jump, paths[a][0].get(b, math.inf))
        return jump
    extra = []  # (a, b, edge id or None): retraced original edges, or jumps.
    for a, b in _match(odd, cost):
        jump = float(np.linalg.norm(points[a]-points[b]))
        retrace = paths[a][0].get(b, math.inf) if part_of[a] == part_of[b] else math.inf
        if retrace <= jump:
            parent = paths[a][1]; v = b
            while v != a: u, e = parent[v]; extra.append((u, v, e)); v = u
        else: extra.append((a, b, None))
    # Join the groups that pairing left separate with their shortest jumps, each taken
    # there and back so every degree stays even (Prim's tree over the groups).
    group = list(range(len(components)))
    def root(i):
        while group[i] != i: group[i] = group[group[i]]; i = group[i]
        return i
    for a, b, _ in extra: group[root(part_of[a])] = root(part_of[b])
    groups = {}
    for i, part in enumerate(components): groups.setdefault(root(i), []).extend(part)
    groups = list(groups.values())
    if len(groups) > 1:
        tree = [groups[0]]; rest = groups[1:]
        while rest:
            best = None
            for index, other in enumerate(rest):
                inside = np.vstack([points[g] for g in tree]); members = [v for g in tree for v in g]
                d = np.linalg.norm(inside[:, None]-points[other][None], axis=2)
                i, j = np.unravel_index(np.argmin(d), d.shape)
                if best is None or d[i, j] < best[0]: best = (d[i, j], index, members[i], other[j])
            _, index, a, b = best
            extra.append((a, b, None)); extra.append((a, b, None)); tree.append(rest.pop(index))
    multigraph = {v: list(adjacency[v]) for part in components for v in part}
    jumps = []; next_id = len(edges)
    for a, b, e in extra:
        multigraph[a].append((b, next_id)); multigraph[b].append((a, next_id)); next_id += 1
        if e is None: jumps.append((a, b))
    return _euler(multigraph, start), jumps


def plan_route(vertices, edges, route, mode, connectors=()):
    """(route, connector edges) for mode; the original pair when no shorter plan exists.

    edges are the drawn lines only; connectors are the original's jump edges, returned
    unchanged when the original is kept.
    """
    original = (np.asarray(route, dtype=int), np.asarray(connectors, dtype=int).reshape(-1, 2))
    if mode == ROUTE_OFF: return original
    if mode not in ROUTE_MODES: raise ValueError('Unknown route mode.')
    points = np.asarray(vertices, dtype=float)
    edges = [(int(a), int(b)) for a, b in edges if a != b]
    if not edges or len(points) > MAX_PLAN_VERTICES or len(original[0]) < 2: return original
    adjacency = [[] for _ in points]
    for k, (a, b) in enumerate(edges): adjacency[a].append((b, k)); adjacency[b].append((a, k))
    used = {v for e in edges for v in e}
    components = _components(len(points), adjacency, used)
    start = int(original[0][0]) if int(original[0][0]) in used else min(used)
    planned = (_shortest_jumps(points, adjacency, components, start) if mode == SHORTEST_JUMPS
               else _shortest_path(points, edges, adjacency, components, start))
    if planned is None: return original
    new_route, jumps = planned
    new_route = np.asarray(new_route, dtype=int)
    if new_route[0] != new_route[-1]: new_route = np.r_[new_route, new_route[0]]
    if route_length(points, new_route) >= route_length(points, original[0])-1e-12: return original
    return new_route, np.asarray(jumps, dtype=int).reshape(-1, 2)
