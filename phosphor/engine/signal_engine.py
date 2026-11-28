"""Audio phase -> distance along a rotating wireframe. Left=X, right=Y."""
from collections import OrderedDict, namedtuple
from dataclasses import dataclass, fields, replace
from phosphor.engine.identity_cache import Identity
from functools import lru_cache, wraps
import threading
from phosphor.storage.audio_writer import AudioWriter
import math
import numpy as np
from phosphor.engine.solids import (
    SOLIDS, POLYGONS, ANIMATED_SOLIDS, ShapeParams, validate_shape_params,
    geometry, route_for, solid_geometry, solid_route, dynamic_solid_route_at,
)
from phosphor.engine.automation import gain_at, validate_lanes, lane_points, SHAPE_LANE_FIELDS, live_lanes, parked_lanes, parked_lane
from phosphor.engine.switch_lane import ON, switch_on, validate_switch
from phosphor.engine import switch_sweep
from phosphor.engine.vector_text import TEXT_OBJECT, DEFAULT_TEXT, text_geometry, text_route, validate_text
from phosphor.audio.score_source import (SOURCES, SCALE_NAME, MIDI_FILE_SOURCE, TONE_SOURCE, TONE_RANGE, TONE_SECONDS,
    scale_notes, validate_scale, score_components, source_duration as score_duration)
from phosphor.audio.midi_synth import MIDI_SOURCE, NOTE_DECAYS
from phosphor.engine.scene_path import TRAVEL_MODES, MAX_TRAVEL_SPEED, INSTANT_TRAVEL, plan_scene
from phosphor.engine.function_plot import PARAMETRIC_OBJECT, FunctionPlot, compiled_plot, validate_variable_name
from phosphor.engine.recurrence import (RECURRENCE_OBJECT, RecurrencePlot, compiled_recurrence,
    recurrence_points, recurrence_points_at)
from phosphor.engine.corner_dwell import corner_positions, dwell_phase, MAX_DWELL_US
from phosphor.engine.custom_shape import Drawing, CUSTOM_OBJECT, compiled
from phosphor.engine.model_object import MODEL_OBJECT, Model, model_route
from phosphor.engine.model_animation import Playback
from phosphor.engine.motion_path import Motion,guide_route,sample_route
from phosphor.engine.drawing_motion import render_components,segment_indices,validate_routes
from phosphor.engine.route_plan import ROUTE_OFF, ROUTE_MODES, plan_route
from phosphor.engine import noise_loops
from phosphor.engine.matrix_rain import MATRIX_OBJECT, MatrixConfig, matrix_route_at, validate_matrix
from phosphor.engine.object_replace import equation_variables
from phosphor.engine.transforms import (apply_stack, active, has_noise, uses_position, route_positions, noise_curve,
    validate_stack, Param, path_window, trim_route, validate_path, variable_values, validate_object_variables,
    has_topology, apply_topology_stack, pointify_params, pointify_phase)


MAX_SPIN = 10.0
# scene_plan's cache (audit P5): an entry count alone let a dense spinning model hold ~250 MiB.
# Hits only matter within a pose frame (chord voices, consecutive blocks, sweep key plans)
# and for scrubbing, so the oldest plans go once the arrays exceed this many bytes.
PLAN_CACHE_ENTRIES = 256
PLAN_CACHE_BYTES = 32 * 2**20
PLAN_CACHE_KEEP = 4  # Always keep the newest few, however large (a frame's key plans).
CacheInfo = namedtuple('CacheInfo', 'hits misses maxsize currsize')


def _nbytes(value):
    if isinstance(value, np.ndarray): return value.nbytes
    if isinstance(value, tuple): return sum(_nbytes(item) for item in value)
    return 0


def plan_cache(function):
    """lru_cache-like memo bounded by entries and array bytes (cache_info, cache_clear).

    A result computed while a realtime noise loop was missing (noise_loops.misses) is
    returned but never stored: it is a playback fallback, not the plan.
    """
    entries, sizes, lock = OrderedDict(), {}, threading.Lock()
    stats = [0, 0, 0]  # hits, misses, bytes

    @wraps(function)
    def cached(*args, **kwargs):
        key = (args, tuple(kwargs.items())) if kwargs else args
        with lock:
            if key in entries:
                entries.move_to_end(key); stats[0] += 1
                return entries[key]
            stats[1] += 1
        missed = noise_loops.misses()
        result = function(*args, **kwargs)
        if noise_loops.misses() != missed: return result
        size = _nbytes(result)
        with lock:
            if key not in entries:
                entries[key] = result; sizes[key] = size; stats[2] += size
                while len(entries) > PLAN_CACHE_ENTRIES or (stats[2] > PLAN_CACHE_BYTES and len(entries) > PLAN_CACHE_KEEP):
                    old, _ = entries.popitem(last=False); stats[2] -= sizes.pop(old)
        return result

    def cache_info():
        with lock: return CacheInfo(stats[0], stats[1], PLAN_CACHE_ENTRIES, len(entries))

    def cache_bytes():
        with lock: return stats[2]

    def cache_clear():
        with lock:
            entries.clear(); sizes.clear(); stats[:] = [0, 0, 0]

    cached.cache_info, cached.cache_bytes, cached.cache_clear = cache_info, cache_bytes, cache_clear
    return cached


# Route positions per static route (audit P4): read-only cached geometry is the same object
# every pose frame, so its positions are computed once. Strong references keep ids valid.
_positions, _positions_lock = OrderedDict(), threading.Lock()
POSITIONS_ENTRIES = 16


def cached_route_positions(vertices, route):
    """transforms.route_positions, memoized by identity for read-only arrays (else computed)."""
    if not (isinstance(vertices, np.ndarray) and isinstance(route, np.ndarray)
            and not vertices.flags.writeable and not route.flags.writeable):
        return route_positions(vertices, route)
    key = (id(vertices), id(route))
    with _positions_lock:
        hit = _positions.get(key)
        if hit is not None and hit[0] is vertices and hit[1] is route:
            _positions.move_to_end(key); return hit[2]
    positions = route_positions(vertices, route); positions.setflags(write=False)
    with _positions_lock:
        _positions[key] = (vertices, route, positions)
        while len(_positions) > POSITIONS_ENTRIES: _positions.popitem(last=False)
    return positions


@dataclass(frozen=True)
class Shape:
    solid: str = 'Cube'
    position_x: float = 0.0
    position_y: float = 0.0
    shape_size: float = 1.0
    rotation_x: float = 0.0
    rotation_y: float = 0.0
    rotation_z: float = 0.0
    text: str = DEFAULT_TEXT
    custom: Drawing | None = None
    uid: str = ''
    motion: Motion | None = None
    model: Model | None = None
    spin_x: float = 0.0  # Revolutions per second around each viewport axis.
    spin_y: float = 0.0
    spin_z: float = 0.0
    plot: FunctionPlot | None = None  # Equations of a Parametric equation object.
    recurrence: RecurrencePlot | None = None
    # The object's transform stack (engine/transforms.py), applied before the pose fields above.
    # rotation_*/spin_* remain as a final pose: motion-path follow turns rotation_z, and
    # version 11 scenes move their values into the stack when read.
    transforms: tuple = ()
    # Path window (transforms.path_window): where along its route the object starts, and how
    # much of it is drawn (negative: backwards; 0: hidden). Defaults draw the whole route.
    path_start: Param = Param(0.)
    path_trace: Param = Param(1.)
    lanes: tuple = ()
    lane_clock: tuple = ()  # Runtime only, baked by Composer.
    animation: Playback = Playback()
    muted: bool = False
    stack_bypassed: bool = False
    # Object variables (T5, engine/transforms.py): function_plot.Variables shared by the stack
    # and by Path Start and Trace, evaluated once per pose frame before the stack.
    transform_variables: tuple = ()
    matrix: MatrixConfig | None = None
    shape_params: ShapeParams | None = None

    def _fields(self):
        return tuple(getattr(self, field.name) for field in fields(self))

    def __hash__(self):
        value = self.__dict__.get('_hash')
        if value is None:
            value = hash(self._fields())
            object.__setattr__(self, '_hash', value)
        return value

    def __reduce__(self):
        return type(self), self._fields()


@dataclass(frozen=True)
class Settings:
    """A scene snapshot. Edits use replace so audio can reuse a successful validation."""
    sample_rate: int = 48000
    drawing_hz: float = 60.0  # pose updates, not drawing repetition frequency
    bpm: float = 120.0
    tempo: object = 120.0  # Runtime BPM or BeatClock; never stored in a scene.
    drawing_gain: float = 0.55  # output level
    music_gain: float = 0.0  # original-audio blend, 0 = fully shaped
    source: str = SCALE_NAME
    shapes: tuple = ()  # The scene's objects; empty is allowed (the beam rests at the centre).
    scale_type: str = 'Major'
    scale_root: int = 48  # MIDI note of the fundamental, C3.
    scale_end: int = 84  # Highest note of the scale, C6.
    tone_frequency: float = 110.0  # Single tone source, Hz.
    midi_file: str = ''  # Path of the MIDI file played by the MIDI file source.
    note_decay: str = NOTE_DECAYS[0]  # Live MIDI voice envelope.
    midi_channel: int = -1
    midi_gain: float = 1.0
    travel_mode: str = 'No changes'
    travel_speed: float = 8.0  # INSTANT_TRAVEL (infinity) makes travel take no time.
    corner_dwell_us: float = 0.0
    motion_time_offset: float = 0.0 # runtime pose clock only, not musical time
    shape_redraw_count: int = 1
    route_mode: str = ROUTE_OFF  # Machine signal preference, like corner dwell; not saved in scenes.
    # Machine signal preference (engine/switch_sweep.py): sweep length around On/Off switches;
    # 0 is the hard switch. Not saved in scenes.
    switch_sweep_ms: float = 0.0
    # Runtime only: (pose time, rising) where every object sweeps out or in (a muted clip's edges).
    switch_fades: tuple = ()


def scene_shapes(settings):
    return settings.shapes


def object_id(shape, index):
    return shape.uid or f'@object:{index}'


def audition_shapes(shapes, solo_ids=(), transform_solos=()):
    """Apply window-only audition state to immutable render copies, never the document."""
    if not solo_ids and not transform_solos: return shapes
    solos = dict(transform_solos)
    result = []
    for index, shape in enumerate(shapes):
        uid = object_id(shape, index)
        values = {}
        if solo_ids and uid not in solo_ids: values['muted'] = True
        if uid in solos:
            values['transforms'] = tuple(replace(t, enabled=i == solos[uid])
                                         for i, t in enumerate(shape.transforms))
        result.append(replace(shape, **values) if values else shape)
    return tuple(result)


def object_stack(shape):
    return () if shape.stack_bypassed else active(shape.transforms)


PARAMETRIC_STEPS = 256  # Path segments per parametric object; samples are evaluated exactly.


def scale_sequence(settings):
    return scale_notes(settings.scale_type, settings.scale_root, settings.scale_end)


@dataclass
class PreparedAudio:
    """Offline analysis for arbitrary finite stereo PCM, for future file input."""
    samples: np.ndarray
    phase: np.ndarray
    envelope: np.ndarray
    sample_rate: int
    frequency: np.ndarray | None = None

    def frequency_at(self,times):
        if self.frequency is None:
            self.frequency=np.abs(np.gradient(np.unwrap(self.phase*2*np.pi)/(2*np.pi))*self.sample_rate)
        indices=np.floor(times*self.sample_rate+1e-7).astype(np.int64)%len(self.samples)
        return self.frequency[indices]

    def at(self, times):
        indices = np.floor(times * self.sample_rate + 1e-7).astype(np.int64) % len(self.samples)
        return self.samples[indices], self.phase[indices], self.envelope[indices]


def prepare_audio(stereo, sample_rate=48000):
    """Analyze phase/envelope of the dominant stereo component with an FFT.

    This is offline analytic-signal analysis, not output high/low-pass filtering.
    Polyphonic signals have no unique pitch: not every voice can be retained.
    """
    stereo = np.asarray(stereo, dtype=float)
    if stereo.ndim != 2 or stereo.shape[1] != 2 or len(stereo) < 2:
        raise ValueError('Input must have shape (samples, 2), with at least two samples.')
    if not np.isfinite(stereo).all() or np.max(np.abs(stereo)) > 1 or sample_rate <= 0:
        raise ValueError('Input must be finite stereo PCM in [-1, 1] with a positive sample rate.')
    stereo = stereo.copy()
    centered = stereo - stereo.mean(axis=0)
    _, vectors = np.linalg.eigh(centered.T @ centered)
    direction = vectors[:, -1]
    if direction[np.argmax(np.abs(direction))] < 0:
        direction = -direction
    mono = centered @ direction
    n = len(mono)
    multiplier = np.zeros(n)
    multiplier[0] = 1
    multiplier[1:(n + 1) // 2] = 2
    if n % 2 == 0:
        multiplier[n // 2] = 1
    analytic = np.fft.ifft(np.fft.fft(mono) * multiplier)
    envelope = np.abs(analytic)
    peak = envelope.max()
    if peak > 1e-12:
        envelope /= peak
    else:
        envelope[:] = 0
    return PreparedAudio(stereo, np.angle(analytic) / (2 * np.pi), envelope, sample_rate)


def scale_components(times, settings):
    notes = scale_sequence(settings)
    beat_seconds = 60 / settings.bpm
    beats = times / beat_seconds
    note_index = np.floor(beats).astype(np.int64)
    local_seconds = (beats - note_index) * beat_seconds
    frequency = 440 * 2.0 ** ((notes[note_index % len(notes)] - 69) / 12)
    ramp = min(.01, beat_seconds / 4)
    envelope = np.minimum(1, np.minimum(local_seconds / ramp,
                                      (beat_seconds - local_seconds) / ramp))
    # Known phase avoids estimating pitch for this synthetic test source.
    phase = frequency * local_seconds
    mono = np.sin(2 * np.pi * phase) * envelope
    return np.column_stack((mono, mono)), phase, envelope


def tone_components(times, settings):
    """A steady sine: one scene traversal per cycle at the set frequency."""
    phase = settings.tone_frequency * times
    mono = np.sin(2 * np.pi * phase)
    return np.column_stack((mono, mono)), phase, np.ones(len(times))


def music_samples(times, settings):
    return source_components(times, settings)[0]


def source_components(times, settings):
    if settings.source == MIDI_SOURCE:
        return np.zeros((len(times), 2)), np.zeros(len(times)), np.zeros(len(times))
    if settings.source == MIDI_FILE_SOURCE:
        return score_components(times, settings.bpm, settings.midi_file)
    if settings.source == TONE_SOURCE:
        return tone_components(times, settings)
    return scale_components(times, settings)


def source_frequency(times,settings):
    if settings.source == MIDI_FILE_SOURCE:
        from phosphor.audio.score_source import score_frequency
        return score_frequency(times,settings.bpm,settings.midi_file)
    if settings.source == MIDI_SOURCE: return np.zeros(len(times))
    if settings.source == TONE_SOURCE: return np.full(len(times),float(settings.tone_frequency))
    notes=scale_sequence(settings)
    index=np.floor(times/(60/settings.bpm)).astype(np.int64)
    return 440*2.**((notes[index%len(notes)]-69)/12)


def source_duration(settings):
    """Length of one pass of the source; WAV exports use it. The single tone is endless."""
    if settings.source == MIDI_SOURCE:
        return 0.0
    if settings.source == TONE_SOURCE:
        return TONE_SECONDS
    if settings.source == MIDI_FILE_SOURCE:
        return score_duration(settings.bpm, settings.midi_file)
    return len(scale_sequence(settings)) * 60 / settings.bpm


def _original_route(kind, payload):
    """(vertices, drawn edges, route, connector edges) as each shape type builds it."""
    if kind == CUSTOM_OBJECT:
        vertices, edges, route, travel = compiled(payload); joins = {tuple(t) for t in travel}
        return vertices, [tuple(e) for e in edges if tuple(e) not in joins], route, np.array(travel, dtype=int).reshape(-1, 2)
    if kind == TEXT_OBJECT:
        vertices, edges, route, connectors = text_route(payload); joins = {tuple(c) for c in connectors}
        return vertices, [tuple(e) for e in edges if tuple(e) not in joins], route, connectors
    if kind == MODEL_OBJECT:
        vertices, edges, route, connectors = model_route(payload); joins = {tuple(c) for c in np.asarray(connectors).reshape(-1, 2)}
        return vertices, [tuple(e) for e in np.asarray(edges).reshape(-1, 2) if tuple(e) not in joins], route, np.asarray(connectors).reshape(-1, 2)
    if kind == 'solid_param':
        solid_name, params = payload
        vertices, edges = solid_geometry(solid_name, params)
        return vertices, [tuple(e) for e in edges], solid_route(solid_name, params), np.empty((0, 2), dtype=int)
    vertices, edges = geometry(payload)
    return vertices, [tuple(e) for e in edges], route_for(payload), np.empty((0, 2), dtype=int)


@lru_cache(maxsize=512)
def planned_route(kind, payload, mode):
    """(vertices, edges including jumps, route, jump edges) for a route mode."""
    vertices, drawn, route, connectors = _original_route(kind, payload)
    route, jumps = plan_route(vertices, drawn, route, mode, connectors)
    edges = np.array([*drawn, *map(tuple, jumps)], dtype=int).reshape(-1, 2)
    for array in (route, jumps, edges): array.setflags(write=False)
    return vertices, edges, route, jumps


@lru_cache(maxsize=256)
def parametric_points(plot, seconds, tempo=120.):
    """Unposed (x, y, z) vertices at s = k/PARAMETRIC_STEPS for one pose time."""
    positions = np.arange(PARAMETRIC_STEPS+1)/PARAMETRIC_STEPS
    try: points = compiled_plot(plot)(np.full(len(positions), float(seconds)), positions, tempo)
    except ValueError: points = np.zeros((len(positions), 3))
    points.setflags(write=False)
    return points


def parametric_points_at(plot, seconds, positions, tempo=120.):
    """(x, y, z) at arbitrary positions; values beyond 0-1 (a trimmed window) wrap once."""
    wrapped = np.where(positions > 1, positions - 1, np.where(positions < 0, positions + 1, positions))
    try: points = compiled_plot(plot)(np.full(len(positions), float(seconds)), wrapped, tempo)
    except ValueError: points = np.zeros((len(positions), 3))
    return points, wrapped


def windowed(shape):
    return shape.path_start != Param(0.) or shape.path_trace != Param(1.)


def _trimmed(part, noise, start, trace):
    """A part cut to its path window; the object's noise follows the new points."""
    vertices, center, route, edges, *rest = part
    cut = trim_route(vertices, route, rest[0] if rest else (), start, trace)
    if cut is None: return part, noise
    points, piece, drawn, joins, weights = cut
    if noise is not None:
        noise = np.asarray(noise, dtype=float)
        noise = np.r_[noise, [noise[i] + f*(noise[j]-noise[i]) for i, j, f in weights]]
    return (points, center, piece, drawn, joins), noise


def parametric_graph(n=PARAMETRIC_STEPS):
    """Route 0..N then back to 0; the closing step is travel, like a jump between letters."""
    route = np.r_[np.arange(n+1), 0]
    connectors = np.array([[n, 0]])
    edges = np.vstack((np.column_stack((np.arange(n), np.arange(1, n+1))), connectors))
    return edges, route, connectors


def _route_key(shape):
    if shape.solid == CUSTOM_OBJECT: return CUSTOM_OBJECT, shape.custom
    if shape.solid == TEXT_OBJECT: return TEXT_OBJECT, getattr(shape, 'text', DEFAULT_TEXT)
    if shape.solid == MODEL_OBJECT: return MODEL_OBJECT, shape.model
    if shape.shape_params is not None: return 'solid_param', (shape.solid, shape.shape_params)
    return 'solid', shape.solid


def shape_geometry(shape, route_mode=ROUTE_OFF):
    if shape.solid == RECURRENCE_OBJECT:
        edges, route, _ = parametric_graph(shape.recurrence.steps-1)
        return recurrence_points(shape.recurrence, 0.), edges, route
    if shape.solid == PARAMETRIC_OBJECT:
        edges, route, _ = parametric_graph()
        return parametric_points(shape.plot, 0.), edges, route
    if shape.solid == MATRIX_OBJECT:
        return matrix_route_at(shape.matrix or MatrixConfig(), 0.)[:3]
    if shape.solid in ANIMATED_SOLIDS:
        return dynamic_solid_route_at(shape.solid, shape.shape_params, 0.)[:3]
    if route_mode != ROUTE_OFF: return planned_route(*_route_key(shape), route_mode)[:3]
    if shape.solid == CUSTOM_OBJECT: return compiled(shape.custom)[:3]
    if shape.solid == TEXT_OBJECT: return text_geometry(getattr(shape,'text',DEFAULT_TEXT))
    if shape.solid == MODEL_OBJECT: return model_route(shape.model)[:3]
    vertices,edges=solid_geometry(shape.solid, shape.shape_params)
    return vertices,edges,solid_route(shape.solid, shape.shape_params)


def shape_connectors(shape, route_mode=ROUTE_OFF):
    """Part-tuple suffix naming edges that only join separate strokes or components."""
    if shape.solid == PARAMETRIC_OBJECT: return (parametric_graph()[2],)
    if shape.solid == RECURRENCE_OBJECT: return (parametric_graph(shape.recurrence.steps-1)[2],)
    if shape.solid == MATRIX_OBJECT: return (matrix_route_at(shape.matrix or MatrixConfig(), 0.)[3],)
    if shape.solid in ANIMATED_SOLIDS:
        return (dynamic_solid_route_at(shape.solid, shape.shape_params, 0.)[3],)
    if route_mode != ROUTE_OFF: return (planned_route(*_route_key(shape), route_mode)[3],)
    if shape.solid == TEXT_OBJECT: return (text_route(getattr(shape,'text',DEFAULT_TEXT))[3],)
    if shape.solid == MODEL_OBJECT: return (model_route(shape.model)[3],)
    if shape.solid == CUSTOM_OBJECT: return (np.array(compiled(shape.custom)[3],dtype=int).reshape(-1,2),)
    return ()


def projected_solid(seconds, settings, vertices=None, include_depth=False):
    """Linear rotation/spin fields only; also used for directions (no stack)."""
    posed = _final_pose(seconds, settings, _resting(settings, shape_geometry(settings)[0] if vertices is None else vertices))
    return posed if include_depth else posed[:, :2]


def _resting(settings, vertices):
    return np.asarray(vertices, dtype=float)


def _final_pose(seconds, settings, posed):
    # Manual view offsets: X, then Y, then Z, in fixed viewport axes.
    spins = (getattr(settings, 'spin_x', 0.), getattr(settings, 'spin_y', 0.), getattr(settings, 'spin_z', 0.))
    for axis, (degrees, spin) in enumerate(zip((settings.rotation_x, settings.rotation_y, settings.rotation_z), spins)):
        degrees += 360*spin*seconds
        if degrees == 0:
            continue
        a = np.deg2rad(degrees)
        ca, sa = np.cos(a), np.sin(a)
        matrix = (np.array([[1, 0, 0], [0, ca, -sa], [0, sa, ca]]) if axis == 0 else
                  np.array([[ca, 0, sa], [0, 1, 0], [-sa, 0, ca]]) if axis == 1 else
                  np.array([[ca, -sa, 0], [sa, ca, 0], [0, 0, 1]]))
        posed = posed @ matrix.T
    return posed


def posed_object(seconds, shape, vertices=None, s=None, include_depth=False, tempo=120., values=None):
    """Object points posed with the transform stack: (points, noise intensity per point or None).
    values: the object's variables at seconds (transforms.variable_values; computed when None).

    Without active transforms this is exactly projected_solid.
    """
    if shape.solid == MODEL_OBJECT and shape.model.animation is not None:
        vertices = shape.model.animation.at(seconds, shape.animation, tempo)
    stack = object_stack(shape)
    if not stack: return projected_solid(seconds, shape, vertices, include_depth), None
    if values is None: values = variable_values(shape.transform_variables, seconds, tempo)
    if vertices is None:
        vertices, _, route = shape_geometry(shape)
        if s is None and uses_position(stack, tuple(values)): s = cached_route_positions(vertices, route)
    points, noise = apply_stack(_resting(shape, vertices), stack, seconds, s, tempo, values=values)
    posed = _final_pose(seconds, shape, points)
    return (posed if include_depth else posed[:, :2]), noise


def _stacked_parts(parts, shape, seconds, tempo=120., values=None):
    """Apply a builder shape's stack after its guide motion has assembled the parts (z = 0)."""
    stack = object_stack(shape)
    if values is None: values = variable_values(shape.transform_variables, seconds, tempo)
    parent = np.array([shape.position_x, shape.position_y]); size = shape.shape_size
    result, noises = [], []
    for local, center, route, *rest in parts:
        world = (np.asarray(local) + center - parent) / size
        s = route_positions(world, route) if uses_position(stack, tuple(values)) else None
        points, noise = apply_stack(np.column_stack((world, np.zeros(len(world)))), stack, seconds, s, tempo, values=values)
        result.append((points[:, :2]*size + parent - center, center, route, *rest))
        noises.append(noise)
    return result, noises


def is_motion_guide(shape):
    if shape.solid!=CUSTOM_OBJECT or shape.custom is None:return False
    if shape.custom.motion_path:return True
    # Preserve external references when an old standalone guide is converted
    # to the new component role. New assemblies do not need scene-level IDs.
    active=[g for g in shape.custom.guides if g.enabled]
    return bool(shape.uid and len(active)==1 and len(segment_indices(shape.custom,active[0].nodes))==len(shape.custom.segments))

@lru_cache(maxsize=256)
def world_motion_route(guide):
    points,closed=guide_route(guide.custom)
    vertices=np.column_stack((points,np.zeros(len(points))))
    points=projected_solid(0,guide,vertices=vertices)*guide.shape_size+[guide.position_x,guide.position_y]
    if np.linalg.norm(np.diff(points,axis=0),axis=1).sum()<1e-10:raise ValueError('Motion path has no projected length. Adjust its view rotation.')
    points.setflags(write=False);return points,closed

def posed_shapes(shapes,seconds):
    guides={s.uid:s for s in shapes if is_motion_guide(s)};result=[]
    for shape in shapes:
        if is_motion_guide(shape):continue
        lanes = live_lanes(shape.lanes)  # Parked variable lanes (object Replace) have no effect.
        if lanes:
            values = {name: getattr(shape, name) * gain_at(points, shape.lane_clock, seconds)
                      for name, points in lanes if name != ON}
            if 'shape_size' in values:
                size = values['shape_size']
                values['shape_size'] = max(abs(size), .001) * (-1 if size < 0 else 1)
            on = lane_points(lanes, ON)
            if on and not shape.muted and not switch_on(on, shape.lane_clock, seconds): values['muted'] = True
            shape = replace(shape, **values)
        if any(step.on.points for step in shape.transforms):
            # Automated On/Off: a step that is off at this pose frame is dropped like a bypassed one.
            steps = tuple(replace(step, enabled=False) if step.enabled and step.on.points and
                          not switch_on(step.on.points, step.on.clock, seconds) else step for step in shape.transforms)
            if steps != shape.transforms: shape = replace(shape, transforms=steps)
        binding=shape.motion
        if binding is not None and binding.guide_id in guides:
            guide=guides[binding.guide_id];points,closed=world_motion_route(guide)
            tangents=world_motion_tangents(guide) if binding.follow else None
            position,angle=sample_route(points,closed,seconds,binding,tangents)
            delta=np.array([shape.position_x,shape.position_y])-binding.anchor
            if binding.follow:
                c,s=np.cos(angle),np.sin(angle);delta=np.array([[c,-s],[s,c]])@delta
            shape=replace(shape,position_x=float(position[0]+delta[0]),position_y=float(position[1]+delta[1]),
                rotation_z=float((shape.rotation_z+np.degrees(angle)+180)%360-180) if binding.follow else shape.rotation_z)
        result.append(shape)
    return tuple(result)

@lru_cache(maxsize=256)
def world_motion_tangents(guide):
    """Interpolate curve tangents without smoothing intentional line/join corners."""
    points,closed=world_motion_route(guide);local,_=guide_route(guide.custom)
    positions={i:(x,y) for i,x,y in guide.custom.nodes}
    hard={i for e in guide.custom.edges for i in e}
    hard.update(i for c in guide.custom.curves if c.kind!='Circle' for i in (c.nodes[0],c.nodes[-1]))
    hard_points=np.array([positions[i] for i in hard])
    is_hard=(np.linalg.norm(local[:,None]-hard_points[None],axis=2).min(axis=1)<1e-10) if hard else np.zeros(len(local),dtype=bool)
    delta=np.diff(points,axis=0);directions=delta/np.maximum(1e-12,np.linalg.norm(delta,axis=1))[:,None]
    tangents=np.stack((directions,directions),axis=1)
    for side,shift,flags in ((0,1,is_hard[:-1]),(1,-1,is_hard[1:])):
        average=directions+np.roll(directions,shift,axis=0);length=np.linalg.norm(average,axis=1)
        valid=(~flags)&(length>1e-8)
        if not closed:valid[0 if side==0 else -1]=False
        tangents[valid,side]=average[valid]/length[valid,None]
    tangents.setflags(write=False);return tangents

def planned_path(frame, drawing_hz, shapes, mode, speed,motion_time_offset=0.,shape_redraw_count=1,route_mode=ROUTE_OFF,tempo=120.):
    """(local points, centres, cumulative time-weighted length) of one pose's scene loop."""
    return scene_plan(frame, drawing_hz, shapes, mode, speed, motion_time_offset, shape_redraw_count, route_mode, tempo)[:3]


@plan_cache
def scene_plan(frame, drawing_hz, shapes, mode, speed,motion_time_offset=0.,shape_redraw_count=1,route_mode=ROUTE_OFF,tempo=120.,switches=None):
    """planned_path plus exact-evaluation data for parametric objects, or None without them.

    A parametric object is a 0..N polyline at s = k/N for the path's timing and travel.
    Its segments get equal shares of its length, so s is linear in the note phase
    across the object, and shape_audio evaluates its equations for every sample.
    switches (switch_sweep.key_state) overrides the switch state per non-guide shape.
    """
    return _scene_plan(frame, drawing_hz, shapes, mode, speed, motion_time_offset, shape_redraw_count,
                       route_mode, tempo, switches)


# Where each point of a plan's path came from (audit D13, the cycle inspector). Per path point:
# part_of (index into parts), vertex_of (that part's vertex); per segment: travel; per part:
# part_shape (index into the scene's shapes, motion guides included) and part_component (the
# builder component, else 0). absent: shapes that are not on the path at this pose (muted,
# switched Off, Trace 0), as (shape index, reason).
PlanProvenance = namedtuple('PlanProvenance', 'part_of vertex_of travel part_shape part_component absent')


def inspect_plan(frame, drawing_hz, shapes, mode, speed,motion_time_offset=0.,shape_redraw_count=1,route_mode=ROUTE_OFF,tempo=120.,switches=None):
    """(scene_plan's result, PlanProvenance) for one pose frame, computed afresh.

    The same planning pass as scene_plan, so the plan is equal to the cached one, but it is
    never stored: inspection leaves the plan cache, and so audio, exactly as they were.
    """
    provenance = {}
    plan = _scene_plan(frame, drawing_hz, shapes, mode, speed, motion_time_offset, shape_redraw_count,
                       route_mode, tempo, switches, provenance)
    return plan, PlanProvenance(**provenance)


def _scene_plan(frame, drawing_hz, shapes, mode, speed, motion_time_offset, shape_redraw_count, route_mode, tempo,
                switches, provenance=None):
    parts, owners, objects, noises, colours, weights, pointifies = [], [], [], [], [], [], []
    sources, absent = [], []  # Per part: (posed shape index, component); shapes left off the path.
    seconds = frame / drawing_hz
    for index, shape in enumerate(posed_shapes(shapes,max(0.,frame/drawing_hz+motion_time_offset))):
        gain = 1.
        if switches is not None:
            gain, off = switches[index]
            if off: shape = replace(shape, transforms=tuple(replace(step, enabled=False) if flag else step
                                                            for step, flag in zip(shape.transforms, off)))
        stack = object_stack(shape)
        curve = noise_curve(stack)
        # Object variables (T5): once per pose frame, before the path window and the stack.
        values = variable_values(shape.transform_variables, seconds, tempo)
        pt_spec = pointify_params(stack, seconds, tempo, values)
        start, trace = path_window(shape.path_start, shape.path_trace, seconds, tempo, values) if windowed(shape) else (0., 1.)
        if shape.muted or trace == 0 or gain == 0:  # Its share of the cycle goes to the rest.
            if provenance is not None: absent.append((index, 'muted' if shape.muted else 'off' if gain == 0 else 'trace'))
            continue
        # A partial piece keeps the object's whole share of the cycle (drawn more slowly).
        weight = 1. / abs(trace)
        # A switch sweep shortens the piece without that compensation: its share shrinks too.
        if gain != 1.: trace = trace * gain
        full = start in (0., 1.) and trace == 1.
        if shape.solid==CUSTOM_OBJECT and (shape.custom.guides or shape.custom.attachments or (shape_redraw_count>1 and len(render_components(shape.custom))>1)):
            built = builder_motion_parts(shape,frame/drawing_hz,max(0.,frame/drawing_hz+motion_time_offset),route_mode)
            noise = [None]*len(built)
            if stack: built, noise = _stacked_parts(built, shape, seconds, tempo, values)
            for component, (part, intensity) in enumerate(zip(built, noise)):
                if not full: part, intensity = _trimmed(part, intensity, start, trace)
                p_pt = None if pt_spec is None else (max(pt_spec[0], 2), pt_spec[1])
                sources.append((index, component)); parts.append(part); owners.append(-1); noises.append(None if intensity is None else intensity*shape.shape_size); colours.append(curve); weights.append(weight); pointifies.append(p_pt)
            continue
        center = np.array([shape.position_x, shape.position_y])
        if shape.solid in (PARAMETRIC_OBJECT, RECURRENCE_OBJECT):
            recurrence = shape.solid == RECURRENCE_OBJECT
            plot = shape.recurrence if recurrence else shape.plot
            steps = plot.steps-1 if recurrence else PARAMETRIC_STEPS
            points_at = recurrence_points_at if recurrence else parametric_points_at
            points_for = recurrence_points if recurrence else parametric_points
            edges, route, connectors = parametric_graph(steps)
            positions = np.arange(steps+1)/steps
            if full: raw, wrapped = points_for(plot, seconds, tempo), positions
            else:  # The window in s: N equal steps from start, trace long (unwrapped for exact samples).
                positions = start + trace*positions
                raw, wrapped = points_at(plot, seconds, positions, tempo)
            if stack and has_topology(stack):
                posed3d, route, edges, connectors, noise = apply_topology_stack(raw, route, edges, connectors, stack, seconds, s=wrapped, tempo=tempo, values=values)
                points = _final_pose(seconds, shape, posed3d)[:, :2] * shape.shape_size
                sources.append((index, 0)); parts.append((points, center, route, edges, connectors))
                owners.append(-1)
                noises.append(None if noise is None else noise*shape.shape_size); colours.append(curve); weights.append(weight)
                pointifies.append(None if pt_spec is None else (max(pt_spec[0], 2), pt_spec[1]))
                continue
            if stack:
                points, noise = posed_object(seconds, shape, raw, s=wrapped, tempo=tempo, values=values)
                points = points * shape.shape_size; matrix = None
            else:
                matrix = projected_solid(seconds, shape, vertices=np.eye(3)) * shape.shape_size
                points, noise = raw @ matrix, None
            sources.append((index, 0)); parts.append((points, center, route, edges, connectors))
            owners.append(len(objects)); objects.append((plot, matrix, shape, seconds, positions, values))
            noises.append(None if noise is None else noise*shape.shape_size); colours.append(curve); weights.append(weight)
            pointifies.append(None if pt_spec is None else (max(pt_spec[0], 2), pt_spec[1]))
            continue
        if shape.solid == MATRIX_OBJECT:
            raw, edges, route, mat_conn = matrix_route_at(shape.matrix or MatrixConfig(), seconds, tempo, values)
            connectors = (mat_conn,) if len(mat_conn) else ()
        elif shape.solid in ANIMATED_SOLIDS:
            raw, edges, route, dyn_conn = dynamic_solid_route_at(shape.solid, shape.shape_params, seconds)
            connectors = (dyn_conn,) if len(dyn_conn) else ()
        else:
            connectors = shape_connectors(shape, route_mode)
            raw, edges, route = shape_geometry(shape, route_mode)
        s = cached_route_positions(raw, route) if uses_position(shape.transforms, tuple(values)) else None
        if stack and has_topology(stack):
            base_conn = connectors[0] if connectors else np.empty((0, 2), dtype=int)
            posed3d, route, edges, new_conn, noise = apply_topology_stack(_resting(shape, raw), route, edges, base_conn, stack, seconds, s, tempo, values=values)
            vertices = _final_pose(seconds, shape, posed3d)[:, :2] * shape.shape_size
            connectors = (new_conn,) if len(new_conn) else ()
        else:
            vertices, noise = posed_object(seconds, shape, raw, s, tempo=tempo, values=values)
            vertices = vertices * shape.shape_size
        part = (vertices, center, route, edges, *connectors)
        if not full: part, noise = _trimmed(part, noise, start, trace)
        p_pt = None if pt_spec is None else (max(pt_spec[0], len(part[0])), pt_spec[1])
        sources.append((index, 0)); parts.append(part); owners.append(-1)
        noises.append(None if noise is None else noise*shape.shape_size); colours.append(curve); weights.append(weight); pointifies.append(p_pt)
    if not parts:
        if provenance is not None:
            provenance.update(_provenance(shapes, np.zeros(2, dtype=np.int64), np.zeros(2, dtype=np.int64),
                                          np.zeros(1, dtype=bool), sources, absent))
        return np.zeros((2,2)),np.zeros((2,2)),np.array([0.,1.]),None,None,None
    if shape_redraw_count>1 and len(parts)>1:
        if sum((len(route)-1)*shape_redraw_count+1 for _,_,route,*_ in parts)>262144:
            raise ValueError('Shape redraw exceeds the path detail budget. Reduce the total draws or simplify the shapes.')
        # Close each pass locally, then travel to the next shape only once.
        # Keeping source phase unchanged preserves the overall note cycle.
        parts=[(vertices,center,np.r_[np.tile(route[:-1],shape_redraw_count),route[-1]],*rest) for vertices,center,route,*rest in parts]
    local, centers, travel, part_of, vertex_of = plan_scene(parts, mode, with_index=True)
    if provenance is not None: provenance.update(_provenance(shapes, part_of, vertex_of, travel, sources, absent))
    lengths = np.linalg.norm(np.diff(local + centers, axis=0), axis=1)
    if mode == 'Faster travel':
        lengths = lengths / np.where(travel, speed, 1.)
    exact = None
    if objects:
        owner_of = np.array(owners)[part_of]
        # s per path point from its object's positions (k/N, or a trimmed window's).
        s_of = np.zeros(len(owner_of))
        for owner, entry in enumerate(objects):
            here = owner_of == owner; s_of[here] = entry[4][vertex_of[here]]
        segment_owner = np.where((owner_of[:-1] == owner_of[1:]) & (owner_of[:-1] >= 0) & ~travel
                                 & (vertex_of[1:] == vertex_of[:-1] + 1), owner_of[:-1], -1)
        for owner in np.unique(segment_owner[segment_owner >= 0]):
            mine = segment_owner == owner
            # A curve that is one point at this instant (s unused) still owns its share of time.
            lengths[mine] = max(lengths[mine].mean(), 1e-12)
        for array in (segment_owner, s_of): array.setflags(write=False)
        exact = (segment_owner, s_of, tuple(objects))
    if any(weight != 1. for weight in weights):
        same = part_of[:-1] == part_of[1:]
        factor = np.where(same & ~np.asarray(travel, dtype=bool), np.asarray(weights)[part_of[:-1]], 1.)
        lengths = lengths * factor
    cumulative = np.concatenate(([0.0], np.cumsum(lengths)))
    noisy = None
    if any(noise is not None for noise in noises):
        # Per path point: intensity and spectrum; travel segments stay clean.
        offsets = np.concatenate(([0], np.cumsum([len(part[0]) for part in parts])))
        intensity = np.concatenate([np.zeros(len(part[0])) if noise is None else np.asarray(noise, dtype=float) for part, noise in zip(parts, noises)])
        point_intensity = intensity[offsets[part_of] + vertex_of]
        curves = tuple(dict.fromkeys(curve for curve in colours if curve is not None))
        point_curve = np.asarray([curves.index(curve) if curve is not None else -1 for curve in colours], dtype=np.int64)[part_of]
        clean = np.asarray(travel, dtype=bool)
        for array in (point_intensity, point_curve, clean): array.setflags(write=False)
        noisy = (point_intensity, point_curve, curves, clean)
    pointify = None
    if any(spec is not None for spec in pointifies):
        spans = []
        seg_part = part_of[:-1]
        same_part = (seg_part == part_of[1:]) & ~np.asarray(travel, dtype=bool)
        for pidx, spec in enumerate(pointifies):
            if spec is None: continue
            idx = np.flatnonzero((seg_part == pidx) & same_part)
            if not len(idx): continue
            d0, d1 = float(cumulative[idx[0]]), float(cumulative[idx[-1] + 1])
            if d1 > d0 + 1e-12: spans.append((d0, d1, spec[0], spec[1]))
        if spans: pointify = tuple(spans)
    return local, centers, cumulative, exact, noisy, pointify

def _provenance(shapes, part_of, vertex_of, travel, sources, absent):
    """PlanProvenance fields, with posed indices mapped back to the scene's shape indices."""
    scene_index = [i for i, shape in enumerate(shapes) if not is_motion_guide(shape)]
    arrays = {'part_of': np.array(part_of, dtype=np.int64), 'vertex_of': np.array(vertex_of, dtype=np.int64),
              'travel': np.array(travel, dtype=bool),
              'part_shape': np.array([scene_index[i] for i, _ in sources], dtype=np.int64),
              'part_component': np.array([c for _, c in sources], dtype=np.int64)}
    for array in arrays.values(): array.setflags(write=False)
    return {**arrays, 'absent': tuple((scene_index[i], reason) for i, reason in absent)}


@lru_cache(maxsize=256)
def builder_guides(drawing):
    return {g.id:drawing.subset(segment_indices(drawing,g.nodes)) for g in drawing.guides if g.enabled}

@lru_cache(maxsize=256)
def builder_local_tangents(drawing):
    return world_motion_tangents(Shape(solid=CUSTOM_OBJECT,custom=drawing))

@lru_cache(maxsize=256)
def planned_components(drawing,route_mode):
    """Each builder component's minimized route, planned once from its rest geometry.

    Pose, guide motion and transforms never change a component's graph, so the plan is
    shared by every frame (it used to be redone per component per frame).
    """
    routes=[]
    for vertices,edges,route,_ in render_components(drawing):
        if route_mode!=ROUTE_OFF:route=plan_route(vertices,[tuple(e) for e in edges],route,route_mode)[0]
        route=np.array(route);route.setflags(write=False);routes.append(route)
    return tuple(routes)

def builder_motion_parts(shape,pose_seconds,motion_seconds,route_mode=ROUTE_OFF):
    parts=[];parent_center=np.array([shape.position_x,shape.position_y]);guides=builder_guides(shape.custom);frames={};evaluated={}
    for (vertices,edges,_,binding),route in zip(render_components(shape.custom),planned_components(shape.custom,route_mode)):
        local=projected_solid(pose_seconds,shape,vertices=vertices)*shape.shape_size
        component_center=(local.min(axis=0)+local.max(axis=0))/2
        local=local-component_center;center=parent_center+component_center
        if binding is not None:
            if binding.guide_id not in frames:
                raw=guides[binding.guide_id];local_points,closed=guide_route(raw)
                points=projected_solid(pose_seconds,shape,vertices=np.column_stack((local_points,np.zeros(len(local_points)))))*shape.shape_size+parent_center
                tangent=builder_local_tangents(raw);vectors=tangent.reshape(-1,2)
                tangent=projected_solid(pose_seconds,shape,vertices=np.column_stack((vectors,np.zeros(len(vectors))))).reshape(tangent.shape)
                tangent=tangent/np.maximum(1e-12,np.linalg.norm(tangent,axis=2))[:,:,None]
                frames[binding.guide_id]=(points,closed,tangent)
            if binding not in evaluated:
                points,closed,tangent=frames[binding.guide_id]
                reference=projected_solid(pose_seconds,shape,vertices=np.array([[*binding.reference,0.]]))[0]*shape.shape_size+parent_center
                direction=projected_solid(pose_seconds,shape,vertices=np.array([[np.cos(binding.heading),np.sin(binding.heading),0.]]))[0]
                initial=np.arctan2(direction[1],direction[0])
                if np.linalg.norm(np.diff(points,axis=0),axis=1).sum()<1e-10:position,angle=points[0],initial+np.radians(binding.angle_offset)
                else:position,angle=sample_route(points,closed,motion_seconds,binding,tangent)
                evaluated[binding]=(position,angle-initial,reference)
            position,angle,reference=evaluated[binding]
            offset=component_center+parent_center-reference
            if binding.follow:
                c,s=np.cos(angle),np.sin(angle);rotation=np.array([[c,-s],[s,c]])
                local=local@rotation.T;offset=rotation@offset
            center=position+offset
        parts.append((local,center,route,edges))
    return parts


def shape_audio(times, phase, envelope, settings, frequency=None):
    """One complete scene traversal per phase cycle, including inter-shape travel."""
    return _shape_audio(times, phase, envelope, settings, frequency)


def shape_parts(times, phase, envelope, settings, frequency=None):
    """Envelope-scaled local trace and its unscaled centre, for voice mixing."""
    return _shape_audio(times, phase, envelope, settings, frequency, parts=True)


def _shape_audio(times, phase, envelope, settings, frequency=None, parts=False):
    frames = np.floor(times * settings.drawing_hz).astype(np.int64)
    result = np.empty((len(times), 2))
    center_result = np.empty_like(result) if parts else None
    shapes = scene_shapes(settings)
    if settings.corner_dwell_us>0 and frequency is None: frequency=source_frequency(times,settings)
    sweep = settings.switch_sweep_ms / 1000.
    swept = None
    if sweep > 0 and (settings.switch_fades or switch_sweep.switching(shapes)):
        swept = tuple(shape for shape in shapes if not is_motion_guide(shape))
    for frame in np.unique(frames):
        mask = frames == frame
        def plan(switches=None):
            return scene_plan(int(frame), settings.drawing_hz, shapes, settings.travel_mode, settings.travel_speed,
                              settings.motion_time_offset, settings.shape_redraw_count, settings.route_mode, settings.tempo,
                              switches)
        frame_frequency = None if frequency is None else np.broadcast_to(frequency,np.shape(phase))[mask]
        events = () if swept is None else switch_sweep.frame_events(
            swept, int(frame), settings.drawing_hz, settings.motion_time_offset, sweep, settings.switch_fades)
        if not events:
            selected_local, selected_center = _beam(plan(), times[mask], phase[mask], frame_frequency, settings)
        else:
            selected_local, selected_center = _swept_beam(plan, swept, events, sweep, int(frame), times[mask],
                                                          phase[mask], frame_frequency, settings)
        if parts:
            result[mask] = selected_local * envelope[mask, None]
            center_result[mask] = selected_center
        else:
            result[mask] = selected_local * envelope[mask, None] + selected_center
    return (result, center_result) if parts else result


def _beam(planned, times, phase, frequency, settings):
    """(local, centre) beam positions of one scene plan at these samples' phases."""
    local, centers, cumulative, exact, noisy, pointify = planned
    position=np.mod(phase,1)
    if settings.corner_dwell_us>0:
        corners=corner_positions(local,centers,cumulative)
        position=dwell_phase(position,frequency,corners,settings.corner_dwell_us)
    distance = position * cumulative[-1]
    if pointify is not None:
        distance = distance.copy()
        for d0, d1, pt_count, glide in pointify:
            in_part = (distance >= d0) & (distance < d1)
            if in_part.any():
                local_u = (distance[in_part] - d0) / (d1 - d0)
                distance[in_part] = d0 + pointify_phase(local_u, pt_count, glide) * (d1 - d0)
    segment = np.minimum(np.searchsorted(cumulative, distance, side='right') - 1,
                         len(cumulative) - 2)
    fraction = (distance - cumulative[segment]) / np.maximum(cumulative[segment + 1] - cumulative[segment], 1e-15)
    selected_local = local[segment] + fraction[:, None] * (local[segment + 1] - local[segment])
    if exact is not None:
        selected_local = exact_parametric(selected_local, times, segment, fraction, exact, settings.tempo)
    selected_center = centers[segment] + fraction[:, None] * (centers[segment + 1] - centers[segment])
    if noisy is not None:
        point_intensity, point_curve, curves, clean = noisy
        amount = (point_intensity[segment] + fraction*(point_intensity[segment+1]-point_intensity[segment])) * ~clean[segment]
        if amount.any():
            # Absolute device sample indices keep blocks, exports and replays aligned.
            index = np.rint(times*settings.sample_rate).astype(np.int64)
            selected_local = selected_local.copy()
            selected_curves = point_curve[segment]
            for colour in np.unique(selected_curves[amount != 0]):
                if colour < 0: continue
                chosen = (selected_curves == colour) & (amount != 0)
                samples = noise_loops.sample_noise(curves[colour], index[chosen], settings.sample_rate,
                                                   wait=not noise_loops.realtime())
                if samples is not None: selected_local[chosen] += amount[chosen, None]*samples
    return selected_local, selected_center


def _swept_beam(plan, shapes, events, sweep, frame, times, phase, frequency, settings):
    """Beam positions in a frame touched by a switch sweep (engine/switch_sweep.py).

    Samples outside every sweep window use the frame's own plan; inside, each sample blends
    the two key plans around it linearly in time.
    """
    def pick(chosen):
        return times[chosen], phase[chosen], None if frequency is None else frequency[chosen]
    local = np.empty((len(times), 2)); center = np.empty_like(local)
    rest = np.ones(len(times), dtype=bool)
    hz = settings.drawing_hz
    for a, b in switch_sweep.segments(events, sweep, frame / hz, (frame + 1) / hz):
        chosen = (times >= a) & (times < b)
        if not chosen.any(): continue
        rest &= ~chosen
        here = pick(chosen)
        local_a, center_a = _beam(plan(switch_sweep.key_state(shapes, events, sweep, a)), *here, settings)
        local_b, center_b = _beam(plan(switch_sweep.key_state(shapes, events, sweep, b)), *here, settings)
        u = ((here[0] - a) / (b - a))[:, None]
        local[chosen] = local_a + u * (local_b - local_a)
        center[chosen] = center_a + u * (center_b - center_a)
    if rest.any():
        local[rest], center[rest] = _beam(plan(), *pick(rest), settings)
    return local, center


def exact_parametric(selected, times, segment, fraction, exact, tempo=120.):
    """Replace polyline points on parametric segments with the equations at that sample's s and t."""
    segment_owner, s_of, objects = exact
    owner = segment_owner[segment]
    for index in np.unique(owner[owner >= 0]):
        hit = np.flatnonzero(owner == index)
        start = segment[hit]
        positions = s_of[start] + fraction[hit] * (s_of[start + 1] - s_of[start])
        plot, matrix, shape, seconds, _, obj_values = objects[index]
        positions = np.where(positions > 1, positions - 1, np.where(positions < 0, positions + 1, positions))
        try:
            points = (recurrence_points_at(plot, seconds, positions, tempo)[0]
                      if shape.solid == RECURRENCE_OBJECT else compiled_plot(plot)(times[hit], positions, tempo))
        except ValueError: continue  # Keep the polyline; the editor reports the error.
        if matrix is None:  # A transform stack: pose each sample's point at this frame's time.
            try: selected[hit] = posed_object(seconds, shape, points, s=positions, tempo=tempo, values=obj_values)[0] * shape.shape_size
            except ValueError: continue
        else: selected[hit] = points @ matrix
    return selected


@lru_cache(maxsize=256)
def validate_custom(drawing):
    drawing.validate(playable=True)
    if drawing.guides or drawing.attachments:
        validate_routes(drawing);render_components(drawing)
        for raw in builder_guides(drawing).values():builder_local_tangents(raw)

def validate(settings):
    from phosphor.engine.composition import BPM_RANGE
    from phosphor.engine.tempo_map import BeatClock
    if not isinstance(settings.tempo, BeatClock) and (type(settings.tempo) not in (int, float) or not BPM_RANGE[0] <= settings.tempo <= BPM_RANGE[1] or not math.isfinite(settings.tempo)):
        raise ValueError(f'Tempo must be finite and between {BPM_RANGE[0]:g} and {BPM_RANGE[1]:g} BPM.')
    if settings.route_mode not in ROUTE_MODES:raise ValueError('Select a route minimizing mode.')
    if type(settings.shape_redraw_count) is not int or not 1<=settings.shape_redraw_count<=32:raise ValueError('Shape redraw total draws must be an integer from 1 to 32.')
    if not all(np.isfinite(value) for key, value in vars(settings).items()
               if key not in ('source', 'shapes', 'scale_type', 'midi_file', 'note_decay', 'travel_mode', 'travel_speed', 'route_mode', 'tempo', 'switch_fades')):
        raise ValueError('All numeric settings must be finite.')
    if not 0<=settings.corner_dwell_us<=MAX_DWELL_US:
        raise ValueError(f'Corner dwell must be 0–{MAX_DWELL_US:g} microseconds.')
    if type(settings.switch_sweep_ms) not in (int, float) or not 0<=settings.switch_sweep_ms<=switch_sweep.MAX_SWEEP_MS:
        raise ValueError(f'Switch sweep must be 0–{switch_sweep.MAX_SWEEP_MS:g} milliseconds.')
    if not 1 <= settings.drawing_hz <= 240 or settings.bpm <= 0:
        raise ValueError('Pose updates must be 1–240 Hz; BPM must be positive.')
    if not 0 <= settings.drawing_gain <= .95 or not 0 <= settings.music_gain <= 1:
        raise ValueError('Output level must be 0–0.95 and original blend 0–1.')
    if settings.source not in (*SOURCES, MIDI_SOURCE):
        raise ValueError('Select a supported audio source.')
    validate_scale(settings.scale_type, settings.scale_root, settings.scale_end)
    if not TONE_RANGE[0] <= settings.tone_frequency <= TONE_RANGE[1]:
        raise ValueError(f'The single tone must be {TONE_RANGE[0]:g}–{TONE_RANGE[1]:g} Hz.')
    if not isinstance(settings.midi_file, str) or (settings.source == MIDI_FILE_SOURCE and not settings.midi_file):
        raise ValueError('Choose a MIDI file for the MIDI file source.')
    if settings.note_decay not in NOTE_DECAYS or settings.midi_channel not in range(-1, 16):
        raise ValueError('Select a supported note decay and MIDI channel.')
    if not 0 <= settings.midi_gain <= 16:
        raise ValueError('MIDI input gain must be between 0 and 16×.')
    if settings.travel_mode not in TRAVEL_MODES or not (1 <= settings.travel_speed <= MAX_TRAVEL_SPEED or settings.travel_speed == INSTANT_TRAVEL):
        raise ValueError(f'Select a travel mode and a speed multiplier from 1 to {MAX_TRAVEL_SPEED:g}×, or instant.')
    if not isinstance(settings.shapes, tuple):
        raise ValueError('Scene objects must be a tuple of shapes.')
    for shape in scene_shapes(settings):
        if type(shape.muted) is not bool or type(shape.stack_bypassed) is not bool:
            raise ValueError('Object mute and bypass must be boolean flags.')
        validate_text(shape.text)
        parked = parked_lanes(shape.lanes)
        for name in parked:  # Parked variable lanes (object Replace): any valid variable name.
            validate_variable_name(name)
        validate_lanes(shape.lanes, (ON, *SHAPE_LANE_FIELDS, *map(parked_lane, parked)), 'Shape lanes')
        validate_switch(lane_points(shape.lanes, ON), 'Object On/Off')
        if live_lanes(shape.lanes) and is_motion_guide(shape): raise ValueError('Motion guides cannot carry lanes.')
        shape.animation.validate()
        if shape.solid not in (*SOLIDS,TEXT_OBJECT,MATRIX_OBJECT,CUSTOM_OBJECT,MODEL_OBJECT,PARAMETRIC_OBJECT,RECURRENCE_OBJECT) or not all(np.isfinite(getattr(shape, key)) for key in Shape.__dataclass_fields__ if key not in ('solid', 'text', 'custom','uid','motion','model','plot','recurrence','matrix','shape_params','transforms','path_start','path_trace','lanes','lane_clock','animation','transform_variables')):
            raise ValueError('Every shape must have a supported solid and finite parameters.')
        if shape.shape_params is not None:
            validate_shape_params(shape.shape_params)
        if shape.solid == MATRIX_OBJECT:
            validate_matrix(shape.matrix, tuple(v.name for v in shape.transform_variables))
        if shape.solid == PARAMETRIC_OBJECT:
            if not isinstance(shape.plot,FunctionPlot):raise ValueError('Parametric equations are missing.')
            compiled_plot(shape.plot)
        if shape.solid == RECURRENCE_OBJECT:
            if not isinstance(shape.recurrence, RecurrencePlot): raise ValueError('Recurrence equations are missing.')
            compiled_recurrence(shape.recurrence)
        if shape.solid == MODEL_OBJECT:
            if not isinstance(shape.model,Model):raise ValueError('3D model geometry is missing.')
            shape.model.validate()
        if shape.solid == CUSTOM_OBJECT:
            if not isinstance(shape.custom,Drawing):raise ValueError('Custom shape geometry is missing.')
            validate_custom(shape.custom)
        if not isinstance(shape.uid,str) or len(shape.uid)>64:raise ValueError('Invalid object identifier.')
        if shape.motion is not None:
            if not isinstance(shape.motion,Motion):raise ValueError('Invalid motion assignment.')
            shape.motion.validate()
        if is_motion_guide(shape):
            if not shape.uid:raise ValueError('Motion guide identifier is missing.')
            world_motion_route(shape)
        if shape.shape_size < .05:
            raise ValueError('Shape size must be at least 0.05.')
        if any(not -180 <= value <= 180 for value in (shape.rotation_x, shape.rotation_y, shape.rotation_z)):
            raise ValueError('X, Y and Z view rotations must be between -180 and 180 degrees.')
        if any(abs(value) > MAX_SPIN for value in (shape.spin_x, shape.spin_y, shape.spin_z)):
            raise ValueError(f'X, Y and Z spin must be between -{MAX_SPIN} and {MAX_SPIN} revolutions per second.')
        # Object variables (T5): names used by the object's equations, active or parked, are refused.
        validate_object_variables(shape.transform_variables, equation_variables(shape))
        validate_stack(shape.transforms, shape.transform_variables)
        validate_path(shape.path_start, shape.path_trace, shape.transform_variables)
    ids=[s.uid for s in scene_shapes(settings) if s.uid]
    if len(ids)!=len(set(ids)):raise ValueError('Object identifiers must be unique.')
    for shape in scene_shapes(settings):
        if shape.motion is not None and shape.motion.guide_id not in ids:raise ValueError('Motion guide is missing. Reassign or remove its motion binding.')
        if shape.motion is not None and shape.motion.follow:
            guide=next(s for s in scene_shapes(settings) if s.uid==shape.motion.guide_id)
            if is_motion_guide(guide):world_motion_tangents(guide)


@lru_cache(maxsize=32)
def _validate_snapshot(key):
    validate(key.value)


def validate_once(settings):
    _validate_snapshot(Identity(settings))


def render(times, settings, source=None):
    validate_once(settings)
    if settings.source == MIDI_SOURCE and source is None:
        silence = np.zeros((len(times), 2))
        return silence.copy(), silence.copy(), silence
    music, phase, envelope = source_components(times, settings) if source is None else source.at(times)
    frequency = (source_frequency(times,settings) if source is None else source.frequency_at(times)) if settings.corner_dwell_us>0 else None
    drawing = shape_audio(times, phase, envelope, settings,frequency)
    output = settings.drawing_gain * ((1 - settings.music_gain) * drawing + settings.music_gain * music)
    return drawing, music, output


def select_signal(drawing, music, output, settings, mode, clip_output=True):
    signal = {'mixed': output, 'drawing': drawing * settings.drawing_gain,
              'music': music * settings.drawing_gain}[mode]
    return np.clip(signal, -1, 1) if clip_output else signal


def export_wav(path, settings, duration=10.0, source=None, source_only=False,
               format='WAV', depth=16, rate=None):
    settings = replace(settings, sample_rate=settings.sample_rate if rate is None else rate)
    validate(settings)
    count = round(duration * settings.sample_rate)
    with AudioWriter(path, settings.sample_rate, format, depth) as output:
        for start in range(0, count, settings.sample_rate):
            times = np.arange(start, min(start + settings.sample_rate, count)) / settings.sample_rate
            if source_only:
                mixed = source_components(times, settings)[0] if source is None else source.at(times)[0]
            else:
                mixed = render(times, settings, source)[2]
            output.write(mixed)
