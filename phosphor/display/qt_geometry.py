"""Vectorized triangle strokes; no raster drawing or audio processing here."""
import numpy as np


def stroke_triangles(points, width):
    points = np.asarray(points, dtype=np.float32)
    if len(points) < 2:
        return np.empty((0, 2), dtype=np.float32)
    a, b = points[:-1], points[1:]
    delta = b - a
    length = np.linalg.norm(delta, axis=1)
    keep = length > 1e-6
    a, b, delta, length = a[keep], b[keep], delta[keep], length[keep]
    normal = np.column_stack((-delta[:, 1], delta[:, 0])) * (width/2 / length[:, None])
    return np.stack((a+normal, a-normal, b+normal, b+normal, a-normal, b-normal), axis=1).reshape(-1, 2).copy()


EDGE_ARRAYS_MAX = 16
_edge_arrays = {}  # id(edges) -> (edges, its index array); holding edges keeps the id valid.


def edge_array(edges):
    """`edges` as an (n, 2) index array, cached for the same edges object (P7c, hot spot H3).

    The muted outline converts its model's edge list every display frame: 2.95 ms at 18,796
    edges. Route edges come from lru caches (model_route), so the object repeats frame to frame.
    """
    if isinstance(edges, np.ndarray): return edges.reshape(-1, 2)
    entry = _edge_arrays.get(id(edges))
    if entry is not None and entry[0] is edges: return entry[1]
    array = np.asarray(edges, dtype=np.intp).reshape(-1, 2)
    array.setflags(write=False)
    if len(_edge_arrays) >= EDGE_ARRAYS_MAX: _edge_arrays.pop(next(iter(_edge_arrays)))
    _edge_arrays[id(edges)] = (edges, array)
    return array


def stroke_edges(points, edges, width):
    """Triangles for separate segments points[a]–points[b], one pair (a, b) per edge.

    The same vertices, in the same order, as concatenating stroke_triangles(points[[a, b]], width)
    for each edge (audit P3: one NumPy pass instead of one call per edge). The segments stay
    separate, so no connectors are drawn between them.
    """
    edges = edge_array(edges)
    if not len(edges):
        return np.empty((0, 2), dtype=np.float32)
    points = np.asarray(points, dtype=np.float32)
    a, b = points[edges[:, 0]], points[edges[:, 1]]
    delta = b - a
    length = np.linalg.norm(delta, axis=1)
    keep = length > 1e-6
    a, b, delta, length = a[keep], b[keep], delta[keep], length[keep]
    normal = np.column_stack((-delta[:, 1], delta[:, 0])) * (width/2 / length[:, None])
    return np.stack((a+normal, a-normal, b+normal, b+normal, a-normal, b-normal), axis=1).reshape(-1, 2).copy()


BEAM_VERTEX =np.dtype([('x','<f4'),('y','<f4'),('r','u1'),('g','u1'),('b','u1'),('a','u1')])
BEAM_KNEE, BEAM_EXPONENT, BEAM_FLOOR = 1.1, 1., .08


def beam_energy(lengths):
    """Relative brightness of equal-time segments: a faster beam deposits less per pixel.

    Scaled by the slower quarter of the trace so window size and interpolation do
    not change it, and uniform shapes stay even. Eight-bit alpha cannot go above
    full, so true over-bright slow regions need a floating-point energy buffer.
    """
    lengths = np.asarray(lengths, dtype=np.float32)
    if not len(lengths):
        return lengths
    reference = BEAM_KNEE*max(float(np.percentile(lengths, 25)), 1e-6)
    return np.clip((reference/lengths)**BEAM_EXPONENT, BEAM_FLOOR, 1).astype(np.float32)


def beam_triangles(points, width, rgb, opacity, strength):
    """Stroke with per-segment premultiplied colour for Qt's ColoredPoint2D layout."""
    points = np.asarray(points, dtype=np.float32)
    vertices = stroke_triangles(points, width)
    result = np.empty(len(vertices), dtype=BEAM_VERTEX)
    if not len(vertices):
        return result
    lengths = np.linalg.norm(points[1:]-points[:-1], axis=1)
    alpha = opacity*((1-strength)+strength*beam_energy(lengths[lengths > 1e-6]))
    alpha = np.repeat(alpha, 6)
    result['x'], result['y'] = vertices[:, 0], vertices[:, 1]
    for name, channel in zip('rgb', rgb):
        result[name] = np.rint(channel*alpha)
    result['a'] = np.rint(255*alpha)
    return result


def grid_lines(width, height, divisions):
    """Centered square cells, extended across rectangular immersive viewports."""
    spacing = min(width, height)/divisions
    if spacing <= 0:
        return []
    # Match the square editor's subdivision phase, including odd divisions.
    phase = (divisions % 2)/2
    lines = []
    for extent, other, vertical in ((width,height,True),(height,width,False)):
        count = int(np.ceil(extent/(2*spacing)))+1
        positions = extent/2+(np.arange(-count,count+1)+phase)*spacing
        for position in positions[(positions>1e-7)&(positions<extent-1e-7)]:
            lines.append([(position,0),(position,other)] if vertical else [(0,position),(other,position)])
    return lines
