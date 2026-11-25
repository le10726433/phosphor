"""Travel strategies for a continuous stereo-only scene trajectory."""
import heapq
import numpy as np

TRAVEL_MODES = ('No changes', 'Faster travel', 'Nearest-shape ordering', 'Retrace existing edges')
MAX_TRAVEL_SPEED = 128.  # Faster travel multiplier; anything above is instant.
INSTANT_TRAVEL = float('inf')  # Travel takes no time: the beam steps between shapes.


def rotate_route(route, vertex):
    body = route[:-1]
    index = int(np.flatnonzero(body == vertex)[0])
    return np.concatenate((body[index:], body[:index], [vertex]))


def edge_path(vertices, edges, start, end):
    """Shortest projected path along existing edges, with deterministic tie breaks."""
    adjacency = [[] for _ in vertices]
    for a, b in edges:
        length = float(np.linalg.norm(vertices[a] - vertices[b]))
        adjacency[a].append((b, length))
        adjacency[b].append((a, length))
    pending, best = [(0., start, [start])], {start: 0.}
    while pending:
        distance, vertex, path = heapq.heappop(pending)
        if distance > best[vertex]:
            continue
        if vertex == end:
            return np.array(path)
        for neighbor, length in adjacency[vertex]:
            candidate = distance + length
            if candidate < best.get(neighbor, float('inf')):
                best[neighbor] = candidate
                heapq.heappush(pending, (candidate, neighbor, path + [neighbor]))
    raise ValueError('Shape graph is disconnected.')


def connector_segments(route, connectors):
    """True for each route step along an edge that only joins separate strokes."""
    if not len(connectors):
        return np.zeros(len(route)-1, dtype=bool)
    span = int(max(route.max(), connectors.max()))+1
    key = lambda a, b: np.minimum(a, b)*span+np.maximum(a, b)
    # Same membership as np.isin, without sorting the route's keys every pose frame.
    steps, known = key(route[:-1], route[1:]), np.sort(key(connectors[:, 0], connectors[:, 1]))
    found = np.minimum(np.searchsorted(known, steps), len(known)-1)
    return known[found] == steps


def assemble(parts, order, routes, with_index=False):
    local = np.concatenate([parts[i][0][routes[i]] for i in order])
    centers = np.concatenate([np.tile(parts[i][1], (len(routes[i]), 1)) for i in order])
    owners = np.concatenate([np.full(len(routes[i]), i) for i in order])
    local = np.concatenate((local, local[:1]))
    centers = np.concatenate((centers, centers[:1]))
    owners = np.concatenate((owners, owners[:1]))
    # Joins inside a part, such as between letters, are travel too; a part's last step
    # is the junction to the next part, which the owner change already marks.
    joins = np.concatenate([np.r_[connector_segments(routes[i], parts[i][4]), False] if len(parts[i]) > 4
                            else np.zeros(len(routes[i]), dtype=bool) for i in order])
    travel = (owners[:-1] != owners[1:]) | joins
    if not with_index:
        return local, centers, travel
    # Which part and which of its vertices each path point is.
    vertices = np.concatenate([routes[i] for i in order])
    return local, centers, travel, owners, np.concatenate((vertices, vertices[:1]))


def travel_length(plan):
    local, centers, travel = plan[:3]
    return float(np.linalg.norm(np.diff(local + centers, axis=0), axis=1)[travel].sum())


def plan_scene(parts, mode, with_index=False):
    """Parts: (local vertices, center, closed route, graph edges[, connector edges]).

    with_index adds, for every path point, its part and that part's vertex index.
    """
    order = list(range(len(parts)))
    routes = [part[2] for part in parts]
    baseline = assemble(parts, order, routes, with_index)
    if len(parts) < 2 or mode in ('No changes', 'Faster travel'):
        return baseline
    points = [part[0] + part[1] for part in parts]
    # Only vertices a route visits can be entered or left (a trimmed path window has extra ones).
    off_route = [~np.isin(np.arange(len(part[0])), part[2]) for part in parts]
    if mode == 'Nearest-shape ordering':
        order, remaining = [0], set(range(1, len(parts)))
        current = points[0][routes[0][0]]
        while remaining:
            options = []
            for index in sorted(remaining):
                distances = np.where(off_route[index], np.inf, np.linalg.norm(points[index] - current, axis=1))
                vertex = int(np.argmin(distances))
                options.append((float(distances[vertex]), index, vertex))
            _, index, vertex = min(options)
            routes[index] = rotate_route(routes[index], vertex)
            order.append(index)
            remaining.remove(index)
            current = points[index][vertex]
        candidate = assemble(parts, order, routes, with_index)
        # A greedy cycle can have a bad return leg; keep the baseline if worse.
        return candidate if travel_length(candidate) <= travel_length(baseline) else baseline
    if mode == 'Retrace existing edges':
        entries, exits = [0] * len(parts), [0] * len(parts)
        for index in order:
            next_index = (index + 1) % len(parts)
            distances = np.linalg.norm(points[index][:, None] - points[next_index][None, :], axis=2)
            distances[off_route[index], :] = np.inf; distances[:, off_route[next_index]] = np.inf
            source, target = np.unravel_index(int(np.argmin(distances)), distances.shape)
            exits[index], entries[next_index] = int(source), int(target)
        for index, part in enumerate(parts):
            route = rotate_route(part[2], entries[index])
            retrace = edge_path(part[0], part[3], entries[index], exits[index])
            routes[index] = np.concatenate((route, retrace[1:]))
        return assemble(parts, order, routes, with_index)
    raise ValueError('Unknown travel mode.')
