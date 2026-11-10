"""Audio phase -> distance along a rotating wireframe. Left=X, right=Y."""
from dataclasses import dataclass,replace
from pathlib import Path
from functools import lru_cache
import wave
import numpy as np
from solids import SOLIDS, POLYGONS, geometry, route_for
from vector_text import TEXT_OBJECT, DEFAULT_TEXT, text_geometry, text_route, validate_text
from score_source import SOURCES, SCALE_NAME, MIDI_FILE_SOURCE, score_components, source_duration as score_duration
from midi_synth import MIDI_SOURCE, PRESETS
from scene_path import TRAVEL_MODES, plan_scene
from corner_dwell import corner_positions, dwell_phase, MAX_DWELL_US
from custom_shape import Drawing, CUSTOM_OBJECT, compiled
from model_object import MODEL_OBJECT, Model, model_route
from motion_path import Motion,guide_route,sample_route
from drawing_motion import render_components,segment_indices,validate_routes


MAX_SPIN = 10.0


@dataclass(frozen=True)
class Shape:
    solid: str = 'Cube'
    position_x: float = 0.0
    position_y: float = 0.0
    shape_size: float = 1.0
    rotation_seconds: float | None = 5.0
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


@dataclass
class Settings:
    sample_rate: int = 48000
    drawing_hz: float = 60.0  # pose updates, not drawing repetition frequency
    rotation_seconds: float | None = 5.0
    bpm: float = 120.0
    drawing_gain: float = 0.55  # output level
    music_gain: float = 0.0  # original-audio blend, 0 = fully shaped
    solid: str = 'Cube'
    position_x: float = 0.0
    position_y: float = 0.0
    shape_size: float = 1.0
    source: str = SCALE_NAME
    midi_file: str = ''  # path of the MIDI file played by the MIDI file source
    shapes: tuple = ()
    midi_preset: str = 'Sine (long)'
    midi_channel: int = -1
    midi_gain: float = 1.0
    travel_mode: str = 'No changes'
    travel_speed: float = 8.0
    corner_dwell_us: float = 0.0
    rotation_x: float = 0.0
    rotation_y: float = 0.0
    rotation_z: float = 0.0
    motion_time_offset: float = 0.0 # runtime pose clock only, not musical time
    shape_redraw_count: int = 1


def scene_shapes(settings):
    return settings.shapes or (Shape(settings.solid, settings.position_x, settings.position_y,
                                     settings.shape_size, settings.rotation_seconds,
                                     settings.rotation_x, settings.rotation_y, settings.rotation_z),)


ASCENDING = np.array([48 + 12 * octave + degree for octave in range(3)
                      for degree in (0, 2, 4, 5, 7, 9, 11)] + [84])
NOTES = np.concatenate((ASCENDING, ASCENDING[-2:0:-1]))


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
    beat_seconds = 60 / settings.bpm
    beats = times / beat_seconds
    note_index = np.floor(beats).astype(np.int64)
    local_seconds = (beats - note_index) * beat_seconds
    frequency = 440 * 2.0 ** ((NOTES[note_index % len(NOTES)] - 69) / 12)
    ramp = min(.01, beat_seconds / 4)
    envelope = np.minimum(1, np.minimum(local_seconds / ramp,
                                      (beat_seconds - local_seconds) / ramp))
    # Known phase avoids estimating pitch for this synthetic test source.
    phase = frequency * local_seconds
    mono = np.sin(2 * np.pi * phase) * envelope
    return np.column_stack((mono, mono)), phase, envelope


def music_samples(times, settings):
    return source_components(times, settings)[0]


def source_components(times, settings):
    if settings.source == MIDI_SOURCE:
        return np.zeros((len(times), 2)), np.zeros(len(times)), np.zeros(len(times))
    return score_components(times, settings.bpm, settings.midi_file) if settings.source == MIDI_FILE_SOURCE else scale_components(times, settings)


def source_frequency(times,settings):
    if settings.source == MIDI_FILE_SOURCE:
        from score_source import score_frequency
        return score_frequency(times,settings.bpm,settings.midi_file)
    if settings.source == MIDI_SOURCE: return np.zeros(len(times))
    notes=np.floor(times/(60/settings.bpm)).astype(np.int64)
    return 440*2.**((NOTES[notes%len(NOTES)]-69)/12)


def source_duration(settings):
    if settings.source == MIDI_SOURCE:
        return 0.0
    return score_duration(settings.bpm, settings.midi_file) if settings.source == MIDI_FILE_SOURCE else len(NOTES) * 60 / settings.bpm


def shape_geometry(shape):
    if shape.solid == CUSTOM_OBJECT: return compiled(shape.custom)[:3]
    if shape.solid == TEXT_OBJECT: return text_geometry(getattr(shape,'text',DEFAULT_TEXT))
    if shape.solid == MODEL_OBJECT: return model_route(shape.model)[:3]
    vertices,edges=geometry(shape.solid)
    return vertices,edges,route_for(shape.solid)


def shape_connectors(shape):
    """Part-tuple suffix naming edges that only join separate strokes or components."""
    if shape.solid == TEXT_OBJECT: return (text_route(getattr(shape,'text',DEFAULT_TEXT))[3],)
    if shape.solid == MODEL_OBJECT: return (model_route(shape.model)[3],)
    if shape.solid == CUSTOM_OBJECT: return (np.array(compiled(shape.custom)[3],dtype=int).reshape(-1,2),)
    return ()


def projected_solid(seconds, settings, vertices=None, include_depth=False):
    angle = 0. if settings.rotation_seconds is None else 2 * np.pi * seconds / settings.rotation_seconds
    c, s = np.cos(angle), np.sin(angle)
    vertices = shape_geometry(settings)[0] if vertices is None else vertices
    yaw = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    if settings.solid == 'Tilting circle':
        # Keep the depth component so manual X/Y rotations can reveal it again.
        posed = vertices @ yaw.T
    elif settings.solid in POLYGONS or settings.solid in (TEXT_OBJECT,CUSTOM_OBJECT):
        spin = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
        posed = vertices @ spin.T
    else:
        tilt = .40
        pitch = np.array([[1, 0, 0], [0, np.cos(tilt), -np.sin(tilt)],
                          [0, np.sin(tilt), np.cos(tilt)]])
        posed = vertices @ yaw.T @ pitch.T
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
    return posed if include_depth else posed[:, :2]


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

@lru_cache(maxsize=256)
def planned_path(frame, drawing_hz, shapes, mode, speed,motion_time_offset=0.,shape_redraw_count=1):
    parts = []
    for shape in posed_shapes(shapes,max(0.,frame/drawing_hz+motion_time_offset)):
        if shape.solid==CUSTOM_OBJECT and (shape.custom.guides or shape.custom.attachments or (shape_redraw_count>1 and len(render_components(shape.custom))>1)):
            parts.extend(builder_motion_parts(shape,frame/drawing_hz,max(0.,frame/drawing_hz+motion_time_offset)))
            continue
        vertices = projected_solid(frame / drawing_hz, shape) * shape.shape_size
        connectors = shape_connectors(shape)
        parts.append((vertices, np.array([shape.position_x, shape.position_y]),
                      shape_geometry(shape)[2], shape_geometry(shape)[1], *connectors))
    if not parts:return np.zeros((2,2)),np.zeros((2,2)),np.array([0.,1.])
    if shape_redraw_count>1 and len(parts)>1:
        if sum((len(route)-1)*shape_redraw_count+1 for _,_,route,*_ in parts)>32768:
            raise ValueError('Shape redraw exceeds the path detail budget. Reduce the total draws or simplify the shapes.')
        # Close each pass locally, then travel to the next shape only once.
        # Keeping source phase unchanged preserves the overall note cycle.
        parts=[(vertices,center,np.r_[np.tile(route[:-1],shape_redraw_count),route[-1]],*rest) for vertices,center,route,*rest in parts]
    local, centers, travel = plan_scene(parts, mode)
    lengths = np.linalg.norm(np.diff(local + centers, axis=0), axis=1)
    if mode == 'Faster travel':
        lengths = lengths / np.where(travel, speed, 1.)
    cumulative = np.concatenate(([0.0], np.cumsum(lengths)))
    return local, centers, cumulative

@lru_cache(maxsize=256)
def builder_guides(drawing):
    return {g.id:drawing.subset(segment_indices(drawing,g.nodes)) for g in drawing.guides if g.enabled}

@lru_cache(maxsize=256)
def builder_local_tangents(drawing):
    return world_motion_tangents(Shape(solid=CUSTOM_OBJECT,custom=drawing,rotation_seconds=None))

def builder_motion_parts(shape,pose_seconds,motion_seconds):
    parts=[];parent_center=np.array([shape.position_x,shape.position_y]);guides=builder_guides(shape.custom);frames={};evaluated={}
    for vertices,edges,route,binding in render_components(shape.custom):
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
    frames = np.floor(times * settings.drawing_hz).astype(np.int64)
    result = np.empty((len(times), 2))
    shapes = scene_shapes(settings)
    if settings.corner_dwell_us>0 and frequency is None: frequency=source_frequency(times,settings)
    for frame in np.unique(frames):
        mask = frames == frame
        local, centers, cumulative = planned_path(int(frame), settings.drawing_hz, shapes,
                                                 settings.travel_mode, settings.travel_speed,settings.motion_time_offset,settings.shape_redraw_count)
        position=np.mod(phase[mask],1)
        if settings.corner_dwell_us>0:
            corners=corner_positions(local,centers,cumulative)
            position=dwell_phase(position,np.broadcast_to(frequency,np.shape(phase))[mask],corners,settings.corner_dwell_us)
        distance = position * cumulative[-1]
        segment = np.minimum(np.searchsorted(cumulative, distance, side='right') - 1,
                             len(cumulative) - 2)
        fraction = (distance - cumulative[segment]) / np.maximum(cumulative[segment + 1] - cumulative[segment], 1e-15)
        selected_local = local[segment] + fraction[:, None] * (local[segment + 1] - local[segment])
        selected_center = centers[segment] + fraction[:, None] * (centers[segment + 1] - centers[segment])
        result[mask] = selected_local * envelope[mask, None] + selected_center
    return result


@lru_cache(maxsize=256)
def validate_custom(drawing):
    drawing.validate(playable=True)
    if drawing.guides or drawing.attachments:
        validate_routes(drawing);render_components(drawing)
        for raw in builder_guides(drawing).values():builder_local_tangents(raw)

def validate(settings):
    if type(settings.shape_redraw_count) is not int or not 1<=settings.shape_redraw_count<=32:raise ValueError('Shape redraw total draws must be an integer from 1 to 32.')
    if not all(np.isfinite(value) for key, value in vars(settings).items() if key not in ('solid', 'source', 'midi_file', 'shapes', 'midi_preset', 'travel_mode', 'rotation_seconds')):
        raise ValueError('All numeric settings must be finite.')
    if settings.rotation_seconds is not None and (not np.isfinite(settings.rotation_seconds) or settings.rotation_seconds < .01):
        raise ValueError('Rotation must be at least 0.01 seconds, or No rotation.')
    if not 0<=settings.corner_dwell_us<=MAX_DWELL_US:
        raise ValueError(f'Corner dwell must be 0–{MAX_DWELL_US:g} microseconds.')
    if not 1 <= settings.drawing_hz <= 240 or settings.bpm <= 0:
        raise ValueError('Pose updates must be 1–240 Hz; BPM must be positive.')
    if not 0 <= settings.drawing_gain <= .95 or not 0 <= settings.music_gain <= 1:
        raise ValueError('Output level must be 0–0.95 and original blend 0–1.')
    if settings.solid not in (*SOLIDS,TEXT_OBJECT):
        raise ValueError('Select a supported solid.')
    if settings.source not in (*SOURCES, MIDI_SOURCE):
        raise ValueError('Select a supported audio source.')
    if not isinstance(settings.midi_file, str) or (settings.source == MIDI_FILE_SOURCE and not settings.midi_file):
        raise ValueError('Choose a MIDI file for the MIDI file source.')
    if settings.midi_preset not in PRESETS or settings.midi_channel not in range(-1, 16):
        raise ValueError('Select a supported MIDI preset/channel.')
    if not 0 <= settings.midi_gain <= 16:
        raise ValueError('MIDI input gain must be between 0 and 16×.')
    if any(not -180 <= value <= 180 for value in (settings.rotation_x, settings.rotation_y, settings.rotation_z)):
        raise ValueError('X, Y and Z view rotations must be between -180 and 180 degrees.')
    if settings.travel_mode not in TRAVEL_MODES or not 1 <= settings.travel_speed <= 64:
        raise ValueError('Select a travel mode and a speed multiplier from 1 to 64×.')
    for shape in scene_shapes(settings):
        validate_text(shape.text)
        if shape.solid not in (*SOLIDS,TEXT_OBJECT,CUSTOM_OBJECT,MODEL_OBJECT) or not all(np.isfinite(value) for key, value in vars(shape).items() if key not in ('solid', 'rotation_seconds', 'text', 'custom','uid','motion','model')):
            raise ValueError('Every shape must have a supported solid and finite parameters.')
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
        if shape.rotation_seconds is not None and (not np.isfinite(shape.rotation_seconds) or shape.rotation_seconds < .01):
            raise ValueError('Shape rotation must be at least 0.01 seconds, or No rotation.')
        if shape.shape_size < .05:
            raise ValueError('Shape size must be at least 0.05.')
        if any(not -180 <= value <= 180 for value in (shape.rotation_x, shape.rotation_y, shape.rotation_z)):
            raise ValueError('X, Y and Z view rotations must be between -180 and 180 degrees.')
        if any(abs(value) > MAX_SPIN for value in (shape.spin_x, shape.spin_y, shape.spin_z)):
            raise ValueError(f'X, Y and Z spin must be between -{MAX_SPIN} and {MAX_SPIN} revolutions per second.')
    ids=[s.uid for s in scene_shapes(settings) if s.uid]
    if len(ids)!=len(set(ids)):raise ValueError('Object identifiers must be unique.')
    for shape in scene_shapes(settings):
        if shape.motion is not None and shape.motion.guide_id not in ids:raise ValueError('Motion guide is missing. Reassign or remove its motion binding.')
        if shape.motion is not None and shape.motion.follow:
            guide=next(s for s in scene_shapes(settings) if s.uid==shape.motion.guide_id)
            if is_motion_guide(guide):world_motion_tangents(guide)


def render(times, settings, source=None):
    validate(settings)
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


def export_wav(path, settings, duration=10.0, source=None, source_only=False):
    validate(settings)
    count = round(duration * settings.sample_rate)
    with wave.open(str(Path(path)), 'wb') as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(settings.sample_rate)
        for start in range(0, count, settings.sample_rate):
            times = np.arange(start, min(start + settings.sample_rate, count)) / settings.sample_rate
            if source_only:
                mixed = source_components(times, settings)[0] if source is None else source.at(times)[0]
            else:
                mixed = render(times, settings, source)[2]
            mixed = np.clip(mixed, -1, 1)
            output.writeframes(np.rint(mixed * 32767).astype('<i2').tobytes())
