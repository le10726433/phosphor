"""SVG outlines converted into bounded, editable line drawings. Fills and colours are ignored."""
from dataclasses import dataclass
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ElementTree
import numpy as np
from phosphor.engine.custom_shape import Drawing, MAX_EDGES, MAX_NODES

MAX_SVG_BYTES = 16*1024*1024
MAX_SVG_POINTS = 500000
CUBIC_STEPS, QUADRATIC_STEPS = 32, 24
BASE_TOLERANCE = 5e-4  # Of the half-size of the normalized drawing.
VISIBLE_TOLERANCE = 3e-3  # Simplification above this is reported.
IGNORED = {'defs', 'clipPath', 'mask', 'symbol', 'marker', 'pattern', 'title', 'desc', 'metadata', 'style', 'script',
           'linearGradient', 'radialGradient', 'filter'}
SHAPES = {'path', 'line', 'polyline', 'polygon', 'rect', 'circle', 'ellipse'}
UNSUPPORTED = {'text', 'image', 'use', 'foreignObject'}
_NUMBER = re.compile(r'[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?')


@dataclass(frozen=True)
class SvgReport:
    subpaths: int
    dropped: int  # Parts too small to survive within the node budget.
    nodes: int
    edges: int
    simplified: bool
    skipped: tuple  # Unsupported element names found in the file.


class _Scanner:
    """Reads SVG path data, including compact forms such as `a1 1 0 00.5.5`."""

    def __init__(self, text):
        self.text, self.at = text, 0

    def skip(self):
        while self.at < len(self.text) and self.text[self.at] in ' \t\r\n,':
            self.at += 1

    def command(self):
        self.skip()
        if self.at < len(self.text) and self.text[self.at].isalpha() and self.text[self.at] not in 'eE':
            self.at += 1
            return self.text[self.at-1]
        return None

    def more(self):
        self.skip()
        return self.at < len(self.text) and not (self.text[self.at].isalpha() and self.text[self.at] not in 'eE')

    def number(self):
        self.skip()
        match = _NUMBER.match(self.text, self.at)
        if match is None:
            raise ValueError('Invalid SVG path data.')
        self.at = match.end()
        return float(match.group())

    def flag(self):
        self.skip()
        if self.at >= len(self.text) or self.text[self.at] not in '01':
            raise ValueError('Invalid SVG arc flag.')
        self.at += 1
        return self.text[self.at-1] == '1'


def _bezier(controls, steps):
    controls = np.asarray(controls, dtype=float)
    t = (np.arange(steps)+1)[:, None]/steps
    if len(controls) == 3:
        return (1-t)**2*controls[0]+2*(1-t)*t*controls[1]+t**2*controls[2]
    return (1-t)**3*controls[0]+3*(1-t)**2*t*controls[1]+3*(1-t)*t**2*controls[2]+t**3*controls[3]


def _arc(start, rx, ry, degrees, large, sweep, end):
    """SVG endpoint arc as points after the start, following the SVG implementation notes."""
    if np.allclose(start, end):
        return np.empty((0, 2))
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        return np.array([end], dtype=float)
    angle = math.radians(degrees)
    c, s = math.cos(angle), math.sin(angle)
    dx, dy = (start[0]-end[0])/2, (start[1]-end[1])/2
    x, y = c*dx+s*dy, -s*dx+c*dy
    scale = (x/rx)**2+(y/ry)**2
    if scale > 1:
        rx, ry = rx*math.sqrt(scale), ry*math.sqrt(scale)
    numerator = max(0., (rx*ry)**2-(rx*y)**2-(ry*x)**2)
    factor = math.sqrt(numerator/((rx*y)**2+(ry*x)**2))*(-1 if large == sweep else 1)
    cx, cy = factor*rx*y/ry, -factor*ry*x/rx
    first = math.atan2((y-cy)/ry, (x-cx)/rx)
    delta = math.atan2((-y-cy)/ry, (-x-cx)/rx)-first
    if sweep and delta < 0:
        delta += 2*math.pi
    elif not sweep and delta > 0:
        delta -= 2*math.pi
    count = max(2, math.ceil(abs(delta)/(math.pi/24)))
    theta = first+delta*(np.arange(count)+1)/count
    px, py = rx*np.cos(theta), ry*np.sin(theta)
    center = ((start[0]+end[0])/2, (start[1]+end[1])/2)
    points = np.column_stack((c*(px+cx)-s*(py+cy)+center[0], s*(px+cx)+c*(py+cy)+center[1]))
    points[-1] = end
    return points


def parse_path(data):
    """Subpaths as (points, closed)."""
    scan, subpaths, points = _Scanner(data), [], []
    current = start = np.zeros(2)
    previous_control, previous_kind = None, ''

    def finish(closed=False):
        nonlocal points
        if len(points) > 1:
            subpaths.append((np.array(points, dtype=float), closed))
        points = []

    command = scan.command()
    while command is not None:
        kind, relative = command.upper(), command.islower()
        if kind == 'Z':
            finish(True)
            current = start
            previous_kind = 'Z'
            command = scan.command()
            continue
        if kind not in 'MLHVCSQTA':
            raise ValueError(f'Unsupported SVG path command: {command}')
        first = True
        while first or scan.more():
            base = current if relative else np.zeros(2)
            def pair():
                x = scan.number()
                return np.array([x, scan.number()])+base
            added = None
            if kind == 'M':
                target = pair()
                if first:
                    finish()
                    start = target
                    points = [target]
                else:
                    added = [target]
            elif kind == 'L':
                added = [pair()]
            elif kind == 'H':
                x = scan.number()
                added = [np.array([x+(current[0] if relative else 0), current[1]])]
            elif kind == 'V':
                y = scan.number()
                added = [np.array([current[0], y+(current[1] if relative else 0)])]
            elif kind == 'C':
                a, b, target = pair(), pair(), pair()
                added = _bezier((current, a, b, target), CUBIC_STEPS)
                previous_control = b
            elif kind == 'S':
                a = 2*current-previous_control if previous_kind in 'CS' and previous_kind else current
                b, target = pair(), pair()
                added = _bezier((current, a, b, target), CUBIC_STEPS)
                previous_control = b
            elif kind == 'Q':
                a, target = pair(), pair()
                added = _bezier((current, a, target), QUADRATIC_STEPS)
                previous_control = a
            elif kind == 'T':
                a = 2*current-previous_control if previous_kind in 'QT' and previous_kind else current
                target = pair()
                added = _bezier((current, a, target), QUADRATIC_STEPS)
                previous_control = a
            else:
                rx, ry, degrees = scan.number(), scan.number(), scan.number()
                large, sweep = scan.flag(), scan.flag()
                target = pair()
                added = _arc(current, rx, ry, degrees, large, sweep, target)
            if kind == 'M':
                current = target
            elif added is not None and len(added):
                if not points:
                    points = [current]
                    start = current
                points.extend(np.asarray(added, dtype=float))
                current = np.asarray(added[-1], dtype=float)
            previous_kind = 'L' if kind == 'M' else kind
            first = False
            if kind == 'M' and scan.more():
                kind = 'L'
        command = scan.command()
    finish()
    return subpaths


def _transform(text):
    matrix = np.eye(3)
    for name, body in re.findall(r'([a-zA-Z]+)\s*\(([^)]*)\)', text or ''):
        values = [float(x) for x in _NUMBER.findall(body)]
        step = np.eye(3)
        try:
            if name == 'matrix':
                a, b, c, d, e, f = values
                step[:2] = [[a, c, e], [b, d, f]]
            elif name == 'translate':
                step[:2, 2] = values[0], values[1] if len(values) > 1 else 0
            elif name == 'scale':
                step[0, 0], step[1, 1] = values[0], values[1] if len(values) > 1 else values[0]
            elif name == 'rotate':
                a = math.radians(values[0])
                step[:2, :2] = [[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]]
                if len(values) == 3:
                    cx, cy = values[1:]
                    before, after = np.eye(3), np.eye(3)
                    before[:2, 2], after[:2, 2] = (cx, cy), (-cx, -cy)
                    step = before@step@after
            elif name == 'skewX':
                step[0, 1] = math.tan(math.radians(values[0]))
            elif name == 'skewY':
                step[1, 0] = math.tan(math.radians(values[0]))
            else:
                raise ValueError(f'Unsupported SVG transform: {name}')
        except (ValueError, IndexError) as error:
            raise ValueError(f'Invalid SVG transform: {name}') from error
        matrix = matrix@step
    return matrix


def _length(element, name):
    match = _NUMBER.match((element.get(name) or '0').strip())
    return float(match.group()) if match else 0.


def _ellipse(cx, cy, rx, ry, count=96):
    theta = 2*np.pi*np.arange(count)/count
    return np.column_stack((cx+rx*np.cos(theta), cy+ry*np.sin(theta)))


def _element_paths(element, kind):
    if kind == 'path':
        return parse_path(element.get('d') or '')
    if kind == 'line':
        return [(np.array([[_length(element, 'x1'), _length(element, 'y1')], [_length(element, 'x2'), _length(element, 'y2')]]), False)]
    if kind in ('polyline', 'polygon'):
        values = [float(x) for x in _NUMBER.findall(element.get('points') or '')]
        points = np.array(values[:len(values)//2*2]).reshape(-1, 2)
        return [(points, kind == 'polygon')] if len(points) > 1 else []
    if kind == 'circle':
        r = _length(element, 'r')
        return [(_ellipse(_length(element, 'cx'), _length(element, 'cy'), r, r), True)] if r > 0 else []
    if kind == 'ellipse':
        rx, ry = _length(element, 'rx'), _length(element, 'ry')
        return [(_ellipse(_length(element, 'cx'), _length(element, 'cy'), rx, ry), True)] if rx > 0 and ry > 0 else []
    x, y, w, h = _length(element, 'x'), _length(element, 'y'), _length(element, 'width'), _length(element, 'height')
    if w <= 0 or h <= 0:
        return []
    rx, ry = min(_length(element, 'rx') or _length(element, 'ry'), w/2), min(_length(element, 'ry') or _length(element, 'rx'), h/2)
    if rx <= 0 or ry <= 0:
        return [(np.array([[x, y], [x+w, y], [x+w, y+h], [x, y+h]]), True)]
    data = f'M{x+rx} {y}H{x+w-rx}A{rx} {ry} 0 0 1 {x+w} {y+ry}V{y+h-ry}A{rx} {ry} 0 0 1 {x+w-rx} {y+h}H{x+rx}A{rx} {ry} 0 0 1 {x} {y+h-ry}V{y+ry}A{rx} {ry} 0 0 1 {x+rx} {y}Z'
    return parse_path(data)


def _hidden(element):
    style = (element.get('style') or '').replace(' ', '').lower()
    return element.get('display') == 'none' or element.get('visibility') == 'hidden' or 'display:none' in style or 'visibility:hidden' in style


def parse_svg(text):
    """Outlines in SVG user units after transforms, and the unsupported element names found."""
    if re.search(r'<!ENTITY', text, re.IGNORECASE):
        raise ValueError('SVG files that define XML entities are not supported.')
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError as error:
        raise ValueError(f'This is not a valid SVG file: {error}') from error
    if root.tag.rsplit('}', 1)[-1] != 'svg':
        raise ValueError('This is not an SVG file.')
    found, skipped = [], set()

    def visit(element, parent):
        kind = element.tag.rsplit('}', 1)[-1] if isinstance(element.tag, str) else ''
        if kind in IGNORED or _hidden(element):
            return
        if kind in UNSUPPORTED:
            skipped.add(kind)
            return
        matrix = parent@_transform(element.get('transform'))
        if kind in SHAPES:
            for points, closed in _element_paths(element, kind):
                placed = (matrix[:2, :2]@points.T).T+matrix[:2, 2]
                found.append((placed, closed))
        for child in element:
            visit(child, matrix)

    visit(root, np.eye(3))
    if sum(len(points) for points, _ in found) > MAX_SVG_POINTS:
        raise ValueError('This SVG is too detailed to import. Simplify it first.')
    return found, tuple(sorted(skipped))


def _simplify_open(points, tolerance):
    keep = np.zeros(len(points), dtype=bool)
    keep[[0, -1]] = True
    stack = [(0, len(points)-1)]
    while stack:
        first, last = stack.pop()
        if last-first < 2:
            continue
        chord, inner = points[last]-points[first], points[first+1:last]-points[first]
        length = float(np.hypot(*chord))
        distance = np.abs(chord[0]*inner[:, 1]-chord[1]*inner[:, 0])/length if length > 1e-12 else np.hypot(*inner.T)
        index = int(np.argmax(distance))
        if distance[index] > tolerance:
            middle = first+1+index
            keep[middle] = True
            stack += [(first, middle), (middle, last)]
    return points[keep]


def simplify(points, closed, tolerance):
    """Ramer-Douglas-Peucker; a closed path is split at its farthest point."""
    if not closed:
        return _simplify_open(points, tolerance)
    if np.allclose(points[0], points[-1]):
        points = points[:-1]
    if len(points) < 3:
        return points
    far = int(np.argmax(np.hypot(*(points-points[0]).T)))
    if far == 0:
        return points[:1]
    first = _simplify_open(points[:far+1], tolerance)
    second = _simplify_open(np.concatenate((points[far:], points[:1])), tolerance)
    return np.concatenate((first, second[1:-1]))


def _graph(paths):
    """Shared-node line graph from (points, closed) paths."""
    nodes, edges = {}, set()
    def node(point):
        key = (round(float(point[0]), 5), round(float(point[1]), 5))
        return nodes.setdefault(key, len(nodes))
    for points, closed in paths:
        ids = [node(point) for point in points]
        if closed and len(ids) > 2:
            ids.append(ids[0])
        for a, b in zip(ids, ids[1:]):
            if a != b:
                edges.add((min(a, b), max(a, b)))
    return nodes, edges


def _at_tolerance(paths, tolerance):
    """Simplified parts; those smaller than the tolerance cannot be represented."""
    return [(simplify(points, closed, tolerance), closed) for points, closed, extent in paths if extent >= tolerance]


def drawing_from_svg(text, name='SVG'):
    found, skipped = parse_svg(text)
    found = [(points, closed) for points, closed in found if len(points) > 1]
    if not found:
        raise ValueError('This SVG has no paths or shapes to draw.')
    every = np.concatenate([points for points, _ in found])
    if not np.all(np.isfinite(every)):
        raise ValueError('This SVG has invalid coordinates.')
    center = (every.min(axis=0)+every.max(axis=0))/2
    half = float((every.max(axis=0)-every.min(axis=0)).max())/2
    if half <= 0:
        raise ValueError('This SVG has no size.')
    # SVG's Y axis points down; the display's points up.
    normalized = [((points-center)/half*[1, -1], closed) for points, closed in found]
    base = []
    for points, closed in normalized:
        extent = float(np.hypot(*(points.max(axis=0)-points.min(axis=0))))
        base.append((simplify(points, closed, BASE_TOLERANCE), closed, extent))
    base = [item for item in base if len(item[0]) > 1 and item[2] > 0]

    def fits(paths):
        nodes, edges = _graph(paths)
        return bool(edges) and len(nodes) <= MAX_NODES and len(edges) <= MAX_EDGES

    tolerance = BASE_TOLERANCE
    result = _at_tolerance(base, tolerance)
    if not fits(result):
        low, high = BASE_TOLERANCE, 4.
        for _ in range(28):
            middle = math.sqrt(low*high)
            candidate = _at_tolerance(base, middle)
            if fits(candidate):
                high, result, tolerance = middle, candidate, middle
            else:
                low = middle
        if not fits(result):
            raise ValueError('This SVG could not be simplified to fit a custom shape.')
    nodes, edges = _graph(result)
    drawing = Drawing((name.strip() or 'SVG')[:64], tuple((index, key[0], key[1]) for key, index in nodes.items()), tuple(sorted(edges)))
    drawing.validate(playable=True)
    return drawing, SvgReport(len(found), len(base)-len(result), len(nodes), len(edges), tolerance > VISIBLE_TOLERANCE, skipped)


def load_svg(path):
    path = Path(path)
    if path.stat().st_size > MAX_SVG_BYTES:
        raise ValueError(f'SVG files are limited to {MAX_SVG_BYTES//(1024*1024)} MB.')
    return drawing_from_svg(path.read_text(encoding='utf-8', errors='replace'), path.stem)
