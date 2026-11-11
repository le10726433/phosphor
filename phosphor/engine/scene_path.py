"""Travel strategies for a continuous stereo-only scene trajectory."""
import heapq
import numpy as np

TRAVEL_MODES = ('No changes', 'Faster travel', 'Nearest-shape ordering', 'Retrace existing edges')


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
    return np.isin(key(route[:-1], route[1:]), key(connectors[:, 0], connectors[:, 1]))


def assemble(parts, order, routes):
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
    return local, centers, (owners[:-1] != owners[1:]) | joins


def travel_length(plan):
    local, centers, travel = plan
    return float(np.linalg.norm(np.diff(local + centers, axis=0), axis=1)[travel].sum())


def plan_scene(parts, mode):
    """Parts: (local vertices, center, closed route, graph edges[, connector edges])."""
    order = list(range(len(parts)))
    routes = [part[2] for part in parts]
    baseline = assemble(parts, order, routes)
    if len(parts) < 2 or mode in ('No changes', 'Faster travel'):
        return baseline
    points = [part[0] + part[1] for part in parts]
    if mode == 'Nearest-shape ordering':
        order, remaining = [0], set(range(1, len(parts)))
        current = points[0][routes[0][0]]
        while remaining:
            options = []
            for index in sorted(remaining):
                distances = np.linalg.norm(points[index] - current, axis=1)
                vertex = int(np.argmin(distances))
                options.append((float(distances[vertex]), index, vertex))
            _, index, vertex = min(options)
            routes[index] = rotate_route(routes[index], vertex)
            order.append(index)
            remaining.remove(index)
            current = points[index][vertex]
        candidate = assemble(parts, order, routes)
        # A greedy cycle can have a bad return leg; keep the baseline if worse.
        return candidate if travel_length(candidate) <= travel_length(baseline) else baseline
    if mode == 'Retrace existing edges':
        entries, exits = [0] * len(parts), [0] * len(parts)
        for index in order:
            next_index = (index + 1) % len(parts)
            distances = np.linalg.norm(points[index][:, None] - points[next_index][None, :], axis=2)
            source, target = np.unravel_index(int(np.argmin(distances)), distances.shape)
            exits[index], entries[next_index] = int(source), int(target)
        for index, part in enumerate(parts):
            route = rotate_route(part[2], entries[index])
            retrace = edge_path(part[0], part[3], entries[index], exits[index])
            routes[index] = np.concatenate((route, retrace[1:]))
        return assemble(parts, order, routes)
    raise ValueError('Unknown travel mode.')
