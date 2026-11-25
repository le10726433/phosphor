"""One drawing cycle of a scene at a frozen pose, for the cycle inspector (audit I1, D13).

Display only. The plan comes from signal_engine.inspect_plan, the same planning pass the
audio uses but never cached, and the samples from the same _beam, so what is shown is what
is heard at that pose and pitch. Nothing here changes audio or the plan cache. Qt-free.

A cycle is the samples whose source phase k·f/rate (k = 0, 1, …) lies in [0, 1): phase 0
starts the route, so the cursor is stable whatever the playhead's own phase was. Sample
times run on from the frozen pose (they only matter to time-varying equations and noise).
"""
from collections import namedtuple
from dataclasses import dataclass
import math

import numpy as np

from phosphor.engine import signal_engine as se
from phosphor.engine.corner_dwell import corner_positions, dwell_phase, MAX_CYCLE_FRACTION

PITCH_RANGE = (5., 20000.)  # Hz: at 192 kHz the lowest pitch is 38,400 samples per cycle.
MAX_CYCLE_SAMPLES = 65536

ObjectShare = namedtuple('ObjectShare', 'shape share samples')  # Scene index, phase fraction, sample count.
REASONS = {'muted': 'muted', 'off': 'switched Off at this pose', 'trace': 'Trace is 0 at this pose'}


@dataclass(frozen=True, eq=False)
class CycleInspection:
    """One cycle: the planned route, its timing and provenance, and the samples on it."""
    pose_seconds: float  # The frozen pose time the caller gave.
    frame: int  # Its pose frame (floor(seconds · pose updates)).
    frequency: float
    sample_rate: float
    points: np.ndarray  # (P, 2) path points in scene units (local + centre).
    timing: np.ndarray  # (P,) route phase of each point before corner dwell, 0 … 1.
    travel: np.ndarray  # (P-1,) travel segments: between objects, strokes or letters.
    point_shape: np.ndarray  # (P,) scene index of each point's object; -1 when nothing is drawn.
    point_part: np.ndarray  # (P,) the plan part (an object, or a builder component) of each point.
    point_vertex: np.ndarray  # (P,) that part's vertex index.
    edge: np.ndarray  # (P-1,) id of the undirected edge each segment runs along.
    visit: np.ndarray  # (P-1,) 1 for an edge's first traversal in the cycle, 2 for its second, …
    corners: np.ndarray  # Route phases of corner-dwell holds (empty without dwell).
    hold: float  # Phase fraction held at each corner.
    samples: np.ndarray  # (N, 2) beam positions in scene units (before envelope and output level).
    sample_phase: np.ndarray  # (N,) source phase k·f/rate.
    sample_segment: np.ndarray  # (N,) the segment each sample lies on.
    sample_held: np.ndarray  # (N,) True while the beam holds at a corner.
    shares: tuple  # ObjectShare per drawn object, in scene order.
    travel_share: float
    travel_samples: int
    dwell_share: float
    dwell_samples: int
    absent: tuple  # (scene index, reason) for objects not drawn at this pose.

    @property
    def count(self):
        return len(self.samples)

    @property
    def empty(self):
        return not self.shares


def _visits(part, vertex, travel):
    """(edge id, visit number) per segment: edges are undirected (part, vertex) pairs."""
    a = np.column_stack((part[:-1], vertex[:-1])); b = np.column_stack((part[1:], vertex[1:]))
    swap = (a[:, 0] > b[:, 0]) | ((a[:, 0] == b[:, 0]) & (a[:, 1] > b[:, 1]))
    low, high = np.where(swap[:, None], b, a), np.where(swap[:, None], a, b)
    keys = np.column_stack((low, high, travel.astype(np.int64)))
    if not len(keys): return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
    _, edge = np.unique(keys, axis=0, return_inverse=True)
    edge = edge.reshape(-1)
    order = np.argsort(edge, kind='stable'); grouped = edge[order]
    starts = np.r_[0, np.flatnonzero(np.diff(grouped)) + 1]
    rank = np.arange(len(order)) - np.repeat(starts, np.diff(np.r_[starts, len(order)]))
    visit = np.empty(len(order), dtype=np.int64); visit[order] = rank + 1
    return edge.astype(np.int64), visit


def inspect_cycle(settings, seconds, frequency):
    """The cycle at pose time `seconds` and pitch `frequency` (Hz), at settings.sample_rate.

    settings is what the audio renders with: the output settings (signal preferences, audition
    copies), or a Composer clip's baked settings. Switch sweeps are not shown (the frame's own
    plan is used throughout).
    """
    frequency = float(frequency); rate = float(settings.sample_rate)
    if not (math.isfinite(frequency) and PITCH_RANGE[0] <= frequency <= PITCH_RANGE[1]):
        raise ValueError(f'Pitch must be {PITCH_RANGE[0]:g}–{PITCH_RANGE[1]:g} Hz.')
    hz = settings.drawing_hz
    seconds = max(0., float(seconds))
    frame = int(np.floor(np.float64(seconds) * hz))  # As _shape_audio picks a sample's frame.
    plan, provenance = se.inspect_plan(frame, hz, se.scene_shapes(settings), settings.travel_mode,
                                       settings.travel_speed, settings.motion_time_offset,
                                       settings.shape_redraw_count, settings.route_mode, settings.tempo)
    local, centers, cumulative = plan[:3]
    points = local + centers
    total = float(cumulative[-1])
    timing = cumulative / total if total > 0 else np.zeros(len(cumulative))
    travel = np.asarray(provenance.travel, dtype=bool)
    part = np.asarray(provenance.part_of); vertex = np.asarray(provenance.vertex_of)
    drawn = len(provenance.part_shape) > 0
    point_shape = provenance.part_shape[part] if drawn else np.full(len(points), -1, dtype=np.int64)
    edge, visit = _visits(part, vertex, travel)

    count = min(MAX_CYCLE_SAMPLES, max(1, math.ceil(rate / frequency - 1e-9)))
    k = np.arange(count)
    phase = k * (frequency / rate)
    times = seconds + k / rate
    pitch = np.full(count, frequency)
    samples = np.add(*se._beam(plan, times, phase, pitch, settings))
    # The segment lookup _beam makes, repeated for provenance (it returns positions only).
    position = np.mod(phase, 1)
    corners, hold = np.empty(0), 0.
    held = np.zeros(count, dtype=bool)
    if settings.corner_dwell_us > 0:
        corners = corner_positions(local, centers, cumulative)
        position = dwell_phase(position, pitch, corners, settings.corner_dwell_us)
        if len(corners):
            hold = min(max(frequency, 0.) * settings.corner_dwell_us * 1e-6, MAX_CYCLE_FRACTION / len(corners))
            held = np.isin(position, corners)
    distance = position * cumulative[-1]
    segment = np.minimum(np.searchsorted(cumulative, distance, side='right') - 1, len(cumulative) - 2)

    moving = 1. - len(corners) * hold
    weights = np.diff(cumulative) / total if total > 0 else np.zeros(len(cumulative) - 1)
    segment_shape = point_shape[:-1]
    sample_shape = segment_shape[segment]
    shares = []
    if drawn:
        for shape in sorted(set(int(s) for s in provenance.part_shape)):
            mine = (segment_shape == shape) & ~travel
            shares.append(ObjectShare(shape, float(moving * weights[mine].sum()),
                                      int(np.count_nonzero((sample_shape == shape) & ~travel[segment] & ~held))))
    absent = tuple((index, 'off' if reason == 'muted' and not settings.shapes[index].muted else reason)
                   for index, reason in provenance.absent)
    arrays = dict(points=points, timing=timing, travel=travel, point_shape=np.asarray(point_shape),
                  point_part=part, point_vertex=vertex, edge=edge, visit=visit, corners=np.asarray(corners),
                  samples=samples, sample_phase=phase, sample_segment=segment, sample_held=held)
    for array in arrays.values():
        if array.flags.writeable: array.setflags(write=False)
    return CycleInspection(
        pose_seconds=seconds, frame=frame, frequency=frequency, sample_rate=rate, hold=float(hold),
        shares=tuple(shares), travel_share=float(moving * weights[travel].sum()) if drawn else 0.,
        travel_samples=int(np.count_nonzero(travel[segment] & ~held)) if drawn else 0,
        dwell_share=float(len(corners) * hold), dwell_samples=int(np.count_nonzero(held)),
        absent=absent, **arrays)


SampleInfo = namedtuple('SampleInfo', 'index seconds phase segment shape target travel held visit visits')


def sample_info(inspection, index):
    """What the beam does at sample `index`: shape (-1 for none), target (travel's next object)."""
    index = int(np.clip(index, 0, inspection.count - 1))
    segment = int(inspection.sample_segment[index])
    shape = int(inspection.point_shape[segment]); target = int(inspection.point_shape[segment + 1])
    edge = inspection.edge[segment] if len(inspection.edge) else -1
    visits = int(np.count_nonzero(inspection.edge == edge)) if len(inspection.edge) else 0
    return SampleInfo(index, index / inspection.sample_rate, float(inspection.sample_phase[index]), segment, shape,
                      target, bool(inspection.travel[segment]), bool(inspection.sample_held[index]),
                      int(inspection.visit[segment]) if len(inspection.visit) else 0, visits)


def next_visit(inspection, index):
    """The first sample on the next traversal of the edge under sample `index`, or None.

    Traversals the beam crosses between two samples (no sample lands on them) are skipped.
    """
    if not len(inspection.edge): return None
    segment = int(inspection.sample_segment[min(max(int(index), 0), inspection.count - 1)])
    same = np.flatnonzero(inspection.edge == inspection.edge[segment])
    order = np.r_[same[same > segment], same[same < segment]]
    for candidate in order:
        hits = np.flatnonzero(inspection.sample_segment == candidate)
        if len(hits): return int(hits[0])
    return None


def first_sample_of(inspection, shape):
    """The first sample drawing object `shape` (not travel or a hold), or None."""
    on = (inspection.point_shape[inspection.sample_segment] == shape) & ~inspection.travel[inspection.sample_segment]
    hits = np.flatnonzero(on & ~inspection.sample_held)
    return int(hits[0]) if len(hits) else None


Difference = namedtuple('Difference', 'max rms')


def difference(reference, current):
    """Sample-by-sample distance (scene units) between two cycles, or None without a shared time base.

    Only cycles of the same pitch, rate and sample count compare point by point; otherwise the
    overlay is the comparison.
    """
    if reference is None or current is None: return None
    if (reference.count != current.count or reference.frequency != current.frequency
            or reference.sample_rate != current.sample_rate):
        return None
    distance = np.linalg.norm(np.asarray(current.samples) - np.asarray(reference.samples), axis=1)
    return Difference(float(distance.max()), float(np.sqrt(np.mean(distance**2))))
