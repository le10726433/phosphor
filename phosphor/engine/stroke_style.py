"""Per-shape bold outlines and Z extrusion for builder drawings.

A Style belongs to whole connected shapes, tracked by node IDs like motion
attachments, so it survives edits that shift segment indices.

Bold replaces the shape's centre lines with the outline of the shape fattened to
``bold`` width: each stroke's offset outline (round caps and joins), with the parts
inside any other stroke of the same shape removed, so joined strokes share one
outline. Extrude makes a front copy at z = +depth/2 and a back copy at -depth/2,
joined at corners. Everything here is pure geometry; compiled() wires it into the
playable route.
"""
from dataclasses import dataclass, replace
import math

import numpy as np

MAX_BOLD = 2.
MAX_DEPTH = 4.
MAX_OUTLINE_POINTS = 6000
ARC_STEP = math.pi/16      # Arc chord angle; sagitta about 0.5% of the radius.
INSIDE_TOLERANCE = .02     # A point closer than (1 - this) * radius to a stroke is inside.
CORNER_ANGLE = math.radians(25)


@dataclass(frozen=True)
class Style:
    nodes: tuple            # Node IDs of the connected shape(s) it applies to.
    bold: float = 0.        # Outline width; 0 keeps plain centre lines.
    depth: float = 0.       # Extrusion depth along Z; 0 keeps the shape flat.

    def document(self): return dict(nodes=list(self.nodes), bold=self.bold, depth=self.depth)

    @classmethod
    def read(cls, value):
        if not isinstance(value, dict) or set(value) != {'nodes', 'bold', 'depth'} or not isinstance(value['nodes'], list):
            raise ValueError('Unsupported stroke style data.')
        return cls(tuple(value['nodes']), value['bold'], value['depth'])


def _number(value, low, high, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{label} must be a number.')
    try: value = float(value)
    except OverflowError: raise ValueError(f'{label} must be a finite number.') from None
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{label} must be between {low:g} and {high:g}.')
    return value


def validate_styles(drawing):
    ids = {n[0] for n in drawing.nodes}; claimed = set()
    for style in drawing.styles:
        if not isinstance(style, Style) or not isinstance(style.nodes, tuple) or not style.nodes:
            raise ValueError('Invalid stroke style.')
        if any(type(i) is not int or i not in ids for i in style.nodes):
            raise ValueError('A stroke style references a missing node.')
        if claimed.intersection(style.nodes): raise ValueError('A shape has two stroke styles.')
        claimed.update(style.nodes)
        _number(style.bold, 0., MAX_BOLD, 'Bold width'); _number(style.depth, 0., MAX_DEPTH, 'Extrude depth')
        if not style.bold and not style.depth: raise ValueError('An empty stroke style should be removed.')


def _indices(drawing, nodes):
    ids = set(nodes)
    return frozenset(k for k, segment in enumerate(drawing.segments) if ids.intersection(segment))


def reconcile_styles(drawing):
    """Grow each style to its whole connected shape; merge styles that became joined."""
    if not drawing.styles: return drawing
    result = []
    for style in drawing.styles:
        indices = _indices(drawing, style.nodes)
        if not indices: continue
        nodes = tuple(sorted(drawing.node_ids(drawing.connected(indices))))
        if any(set(nodes).intersection(other.nodes) for other in result): continue  # First style wins a join.
        result.append(replace(style, nodes=nodes))
    return replace(drawing, styles=tuple(result))


def style_of(drawing, indices):
    """The style shared by these segments' shapes, or None."""
    nodes = drawing.node_ids(indices)
    return next((s for s in drawing.styles if nodes.intersection(s.nodes)), None)


def set_style(drawing, indices, bold=None, depth=None):
    """Set bold and/or depth on the connected shapes containing these segments."""
    if not indices: raise ValueError('Select a shape first.')
    shapes = drawing.connected(indices); nodes = drawing.node_ids(shapes)
    current = style_of(drawing, shapes) or Style((), 0., 0.)
    bold = current.bold if bold is None else _number(bold, 0., MAX_BOLD, 'Bold width')
    depth = current.depth if depth is None else _number(depth, 0., MAX_DEPTH, 'Extrude depth')
    kept = tuple(replace(s, nodes=tuple(i for i in s.nodes if i not in nodes)) for s in drawing.styles)
    kept = tuple(s for s in kept if s.nodes)
    added = (Style(tuple(sorted(nodes)), bold, depth),) if bold or depth else ()
    return reconcile_styles(replace(drawing, styles=kept+added))


# Outline geometry -----------------------------------------------------------------
def _clean(points):
    points = np.asarray(points, dtype=float)
    keep = np.ones(len(points), bool); keep[1:] = np.linalg.norm(np.diff(points, axis=0), axis=1) > 1e-12
    return points[keep]


def _arc(center, start, sweep, radius):
    steps = max(1, math.ceil(abs(sweep)/ARC_STEP))
    angles = start+sweep*np.arange(1, steps)/steps
    return center+radius*np.column_stack((np.cos(angles), np.sin(angles)))


def _left_offset(points, radius, closed):
    """Left offset with round joins (concave-side loops are removed by clipping)."""
    d = np.diff(points if not closed else np.vstack((points, points[:1])), axis=0)
    normals = np.column_stack((-d[:, 1], d[:, 0]))/np.linalg.norm(d, axis=1)[:, None]
    out = []
    count = len(d)
    for i in range(count):
        a, b = points[i], points[(i+1) % len(points)]
        out.append(a+normals[i]*radius); out.append(b+normals[i]*radius)
        if i+1 < count or closed:
            n1, n2 = normals[i], normals[(i+1) % count]
            start = math.atan2(n1[1], n1[0]); sweep = (math.atan2(n2[1], n2[0])-start+math.pi) % (2*math.pi)-math.pi
            out.extend(_arc(b, start, sweep, radius))
    return np.array(out), normals


def _loops(points, radius):
    """Closed offset loops around one stroke: a capsule, or two rings if it is closed."""
    points = _clean(points)
    if len(points) < 2: return []
    closed = len(points) > 3 and np.linalg.norm(points[0]-points[-1]) < 1e-9
    if closed:
        ring = points[:-1]
        return [_left_offset(ring, radius, True)[0], _left_offset(ring[::-1], radius, True)[0]]
    forward, n_forward = _left_offset(points, radius, False)
    back, n_back = _left_offset(points[::-1], radius, False)
    end = _arc(points[-1], math.atan2(n_forward[-1][1], n_forward[-1][0]), -math.pi, radius)
    start = _arc(points[0], math.atan2(n_back[-1][1], n_back[-1][0]), -math.pi, radius)
    return [np.vstack((forward, end, back, start))]


def _distance(points, starts, ends):
    """Minimum distance from each point to any of the segments."""
    best = np.full(len(points), np.inf)
    d = ends-starts; length = np.maximum((d*d).sum(axis=1), 1e-24)
    for chunk in range(0, len(points), 512):
        p = points[chunk:chunk+512, None, :]
        t = np.clip(((p-starts[None])*d[None]).sum(axis=2)/length[None], 0., 1.)
        nearest = starts[None]+t[..., None]*d[None]
        best[chunk:chunk+512] = np.sqrt(((p-nearest)**2).sum(axis=2)).min(axis=1)
    return best


def _densify(loop, step):
    closed = np.vstack((loop, loop[:1]))
    out = []
    for a, b in zip(closed[:-1], closed[1:]):
        n = max(1, math.ceil(np.linalg.norm(b-a)/step))
        out.append(a+(b-a)*(np.arange(n)/n)[:, None])
    return np.vstack(out)


def _simplify(points, closed, tolerance):
    """Drop points that lie on the straight line between their neighbours."""
    if len(points) < 3: return points
    keep = [0]
    for i in range(1, len(points)-1):
        a, b, c = points[keep[-1]], points[i], points[i+1]
        ab = c-a; length = np.linalg.norm(ab)
        if length < 1e-12 or abs(ab[0]*(b-a)[1]-ab[1]*(b-a)[0])/length > tolerance: keep.append(i)
    keep.append(len(points)-1)
    return points[keep]


def bold_outline(polylines, width):
    """Outline of the union of the strokes fattened to width, as 2D polylines.

    Closed outline loops end where they begin (first point repeated).
    """
    radius = width/2
    if radius <= 0 or not polylines: return []
    strokes = [_clean(p) for p in polylines]
    strokes = [(p[:-1], p[1:]) for p in strokes if len(p) >= 2]
    inside, edge = radius*(1-INSIDE_TOLERANCE), radius*(1+INSIDE_TOLERANCE)

    def kept_by(points, owner):
        """Outline points of stroke `owner` that belong to the union's outline.

        A point is dropped inside any stroke, its own included (concave join loops).
        Where strokes share boundary, such as the round caps at a shared node, the
        lower-numbered stroke owns it, so each stretch is drawn once.
        """
        keep = np.ones(len(points), bool)
        for j, (starts, ends) in enumerate(strokes):
            distance = _distance(points, starts, ends)
            keep &= distance >= (edge if j < owner else inside)
        return keep

    pieces = []
    for owner, polyline in enumerate(_clean(p) for p in polylines):
        if len(polyline) < 2: continue
        for loop in _loops(polyline, radius):
            points = _densify(loop, radius/3)
            if len(points) > 20*MAX_OUTLINE_POINTS: raise ValueError('Bold outline is too detailed. Simplify the shape or use a smaller width.')
            kept = kept_by(points, owner)
            if kept.all():
                pieces.append(np.vstack((points, points[:1]))); continue
            if not kept.any(): continue
            # Walk from a dropped point so every kept run is contiguous.
            shift = int(np.argmin(kept)); points = np.roll(points, -shift, axis=0); kept = np.roll(kept, -shift)
            def boundary(a, b):  # Bisect to the outline edge between a kept and a dropped point.
                for _ in range(12):
                    m = (a+b)/2
                    if kept_by(m[None], owner)[0]: a = m
                    else: b = m
                return a
            run = []
            for i in range(len(points)+1):
                k = i % len(points)
                if kept[k]:
                    if not run: run.append(boundary(points[k], points[k-1]))
                    run.append(points[k])
                elif run:
                    run.append(boundary(points[k-1], points[k])); pieces.append(np.array(run)); run = []
    pieces = [_simplify(p, False, radius*1e-3) for p in pieces if len(p) >= 2 and np.linalg.norm(np.diff(p, axis=0), axis=1).sum() > radius*.05]
    if sum(len(p) for p in pieces) > MAX_OUTLINE_POINTS:
        raise ValueError('Bold outline is too detailed. Simplify the shape or use a smaller width.')
    return pieces


def corner_indices(points):
    """Ends of open polylines and sharp turns: where an extrusion is joined front to back."""
    closed = len(points) > 2 and np.linalg.norm(points[0]-points[-1]) < 1e-9
    body = points[:-1] if closed else points
    corners = set() if closed else {0, len(points)-1}
    count = len(body)
    for i in range(count):
        if not closed and i in (0, count-1): continue
        a, b, c = body[i-1], body[i], body[(i+1) % count]
        u, v = b-a, c-b
        nu, nv = np.linalg.norm(u), np.linalg.norm(v)
        if nu < 1e-12 or nv < 1e-12: continue
        if math.acos(float(np.clip(np.dot(u, v)/(nu*nv), -1, 1))) > CORNER_ANGLE: corners.add(i)
    if closed and not corners: corners.add(0)  # Tie smooth rings front to back once.
    return sorted(corners)
