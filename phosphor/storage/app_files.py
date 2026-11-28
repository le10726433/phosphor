"""Versioned scene files and separately persisted appearance preferences."""
from dataclasses import asdict, replace
import json
from pathlib import Path
import re
import math
import tempfile
import os
from phosphor.display.crt_theme import MACOS_GRAPHITE, PALETTES
from phosphor.engine.signal_engine import Settings, Shape, validate, scene_shapes
from phosphor.display.trace_interpolation import INTERPOLATION_FACTORS
from phosphor.display.phosphor import (PHOSPHOR_NAMES, PHOSPHOR_P31, BEAM_GAUSSIAN, BEAM_PROFILES, DEFAULT_CUSTOM_PROFILE,
    DEFAULT_GRAPHICS_QUALITY, GRAPHICS_QUALITIES, DEFAULT_GRID_STYLE, GRID_STYLES, validated_profile)
from phosphor.display.glow_trail import VISUAL_DEFAULTS, VISUAL_RANGES
from phosphor.engine.custom_shape import Drawing, CUSTOM_OBJECT, compiled
from phosphor.engine.model_object import MODEL_OBJECT, Model, model_route
from phosphor.engine.motion_path import Motion
from phosphor.engine.function_plot import FunctionPlot, PARAMETRIC_OBJECT, migrate_tempo_variable
from phosphor.engine.recurrence import RecurrencePlot, RECURRENCE_OBJECT
from phosphor.engine.matrix_rain import MatrixConfig, MATRIX_OBJECT
from phosphor.engine.solids import ShapeParams, PARAMETRIC_SOLIDS, default_shape_params
from phosphor.engine.transforms import Transform, Param, migrated_pose, resting_tilt_step, used_resting_tilt, read_object_variable, SCENE_25_TRANSFORMS
from phosphor.engine.automation import points_document, read_points, parked_lanes
from phosphor.engine.object_replace import TYPE_FIELDS, parked_fields, type_field, kept_parked_lanes
from phosphor.engine.scene_path import INSTANT_TRAVEL
from phosphor.audio.score_source import MIDI_FILE_SOURCE

from phosphor.storage.app_paths import APP_DIR, USER_DIR
from phosphor.editor.wheel_mapping import defaults as wheel_defaults
from phosphor.editor.layout_metrics import PANEL_DEFAULT, panel_width, BROWSER_DEFAULT, browser_width
from phosphor.editor.scroll_mapping import DEFAULT_WHEEL_SENSITIVITY, DEFAULT_TRACKPAD_SENSITIVITY, validated_sensitivity
PREFERENCES_PATH = USER_DIR / 'preferences.json'
DEFAULT_PREFERENCES = dict(palette='Green monochrome', show_grid=True, grid_style=DEFAULT_GRID_STYLE, grid_divisions=8, crt_scanlines=True, crt_glow=True, trace_interpolation='Off', phosphor=PHOSPHOR_P31, beam_profile=BEAM_GAUSSIAN, graphics_quality=DEFAULT_GRAPHICS_QUALITY, phosphor_custom=DEFAULT_CUSTOM_PROFILE, export_format='WAV', export_depth=16, export_rate=48000, export_range='whole', ffmpeg_path='', **VISUAL_DEFAULTS)
DEFAULT_PREFERENCES.update(wheel_defaults())
DEFAULT_PREFERENCES['display_rate'] = 'Match monitor'
DEFAULT_PREFERENCES['metronome_level'] = .3
DEFAULT_PREFERENCES.update(roll_scale='Off', roll_scale_root=0, roll_scale_snap=False, roll_hear_notes=True, roll_velocity_lane=True, roll_audio_gain_lane=False, roll_audio_lane='gain')
DEFAULT_PREFERENCES['roll_velocity_collapsed'] = False  # Velocity lane › Collapse (UI1).
DEFAULT_PREFERENCES['manual_geometry'] = []  # Help › Manual window [x, y, width, height], [] until moved (UI1; JSON-native list).
DEFAULT_PREFERENCES['diagnostic_logging'] = False  # Help menu; read by crash_log at launch.
DEFAULT_PREFERENCES['panel_width'] = PANEL_DEFAULT  # The side panel's width (UI2), dragged on its dividing line.
# The browser panel (AW4): shown or hidden, its width, and the Files tab's folder ('' = Music or Home).
DEFAULT_PREFERENCES['style'] = 'Classic'  # Display › Style (ST1): the interface's look; the scope keeps its own settings.
DEFAULT_PREFERENCES['style_mode'] = 'System'
DEFAULT_PREFERENCES.update(browser_shown=False, browser_width=BROWSER_DEFAULT, browser_folder='', left_page='browser')
# Preferences › Controls › Scroll & trackpad (UI2): separate mouse wheel and trackpad sensitivity.
DEFAULT_PREFERENCES.update(wheel_sensitivity=DEFAULT_WHEEL_SENSITIVITY, trackpad_sensitivity=DEFAULT_TRACKPAD_SENSITIVITY)
# Version 11: parametric equation objects, empty scenes, scale/single tone/MIDI file
# sources, note decay and instant travel.
# Version 12: per-object transform stacks. Version 11 still opens: its rotation and
# spin become an equivalent stack (transforms.migrated_pose).
# Version 13: beat-based gain lanes on transform/path parameters and variables.
# Version 14: p is reserved for beats elapsed; older plot variables p are renamed.
# Version 15: automation curve points carry two absolute handle gains.
# Version 16: recurrence equation objects.
# Version 17: untilted objects; older 3D objects migrate to an explicit Rotate X.
# Version 19: saved object mute and stack bypass; solo remains window-only.
# Version 20: Noise (colour, rate, Speed, Detail) replaces White noise; its Seed is dropped.
# Version 21: On/Off lanes (an object's 'on' lane, a transform's 'on' points); older scenes
# have none, so everything stays on.
# Version 22: the Map transform (X′, Y′, Z′ formulas); older scenes have none.
# Version 23: object Replace (R1) keeps the type data an object's type does not use under
# 'parked' (text, custom, model, plot, recurrence) and parked variable lanes in its lanes
# ('variable:<name>'); older scenes have none.
# Version 24: object variables (T5) in 'transform_variables', read by the transform stack and
# Path Start/Trace; older scenes have none.
# Version 25: Matrix rain object ('matrix'), expanded built-in solids/curves, and new transforms
# (Quantize, Twist, Bend, Wave, Spherize, Glitch, Attractor, Subdivide, Gridify, Radial array,
# Kaleidoscope, Echo, Pointify).
SCENE_VERSION = 25
READABLE_SCENE_VERSIONS = (11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25)
POSE_FIELDS = ('rotation_x', 'rotation_y', 'rotation_z', 'spin_x', 'spin_y', 'spin_z')
GLOBAL_FIELDS = ('sample_rate', 'source', 'drawing_hz', 'bpm', 'drawing_gain', 'music_gain',
                 'scale_type', 'scale_root', 'scale_end', 'tone_frequency', 'midi_file',
                 'note_decay', 'midi_channel', 'midi_gain', 'travel_mode', 'travel_speed')
INSTANT = 'instant'  # travel_speed in scene files when travel takes no time.


def atomic_json(path, value):
    text = json.dumps(value, indent=2, allow_nan=False) + '\n'
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.saving-', suffix='.json', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as target:
            target.write(text)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


def scene_document(settings, listening_mode, midi_device):
    validate(settings)
    return _scene_value(settings, listening_mode, midi_device, Model.document)


def scene_state(settings, listening_mode, midi_device):
    """Equal for two scenes exactly when their scene_document()s are equal, but cheap:
    3D models stay Model objects (field equality, which Model.document preserves), so an
    edit does not serialize every vertex. Scene.dirty compares these (0.55.0-dev)."""
    return _scene_value(settings, listening_mode, midi_device, lambda model: model)


def _scene_value(settings, listening_mode, midi_device, model_value):
    if listening_mode not in ('mixed', 'drawing', 'music'):
        raise ValueError('Invalid listening mode.')
    shapes=scene_shapes(settings)
    def shape_document(shape):
        # Geometry fields are replaced or dropped below; asdict would deep-copy them first.
        value=asdict(replace(shape, custom=None, model=None, plot=None, recurrence=None, matrix=None, shape_params=None))
        value.pop('lane_clock')
        value.pop('shape_params')
        if shape.solid in PARAMETRIC_SOLIDS and shape.shape_params is not None:
            # Replace can leave another configurable solid's edits in this shared record, so an
            # explicit record is kept even when it equals this solid's defaults ({} then): an
            # 8-point Star set to 5 points and replaced with Gear must come back with 5 points.
            defaults = default_shape_params(shape.solid)
            value['shape_params'] = {key: item for key, item in shape.shape_params.document().items()
                                     if item != getattr(defaults, key)}
        for key in ('muted', 'stack_bypassed'):
            if not getattr(shape, key): value.pop(key)
        from phosphor.engine.model_animation import Playback
        if shape.animation == Playback(): value.pop('animation')
        if shape.lanes:value['lanes']={name: points_document(points) for name, points in shape.lanes}
        else:value.pop('lanes')
        if shape.solid!='Text':value.pop('text')
        if shape.solid==CUSTOM_OBJECT:value['custom']=shape.custom.document()
        else:value.pop('custom')
        if shape.solid==MODEL_OBJECT:value['model']=model_value(shape.model)
        else:value.pop('model')
        if shape.solid==PARAMETRIC_OBJECT:value['plot']=shape.plot.document()
        else:value.pop('plot')
        if shape.solid==RECURRENCE_OBJECT:value['recurrence']=shape.recurrence.document()
        else:value.pop('recurrence')
        if shape.solid==MATRIX_OBJECT:value['matrix']=shape.matrix.document()
        else:value.pop('matrix')
        if not shape.uid:value.pop('uid')
        for key in POSE_FIELDS:  # Kept only when code set them; the editor uses the stack.
            if value[key]==0:value.pop(key)
        if shape.transforms:value['transforms']=[transform.document() for transform in shape.transforms]
        else:value.pop('transforms')
        for key,default in (('path_start',Param(0.)),('path_trace',Param(1.))):  # Stored only when changed.
            if getattr(shape,key)==default:value.pop(key)
            else:value[key]=getattr(shape,key).document()
        if shape.motion is None:value.pop('motion')
        if shape.transform_variables: value['transform_variables'] = [v.document() for v in shape.transform_variables]
        else: value.pop('transform_variables')
        parked = parked_fields(shape)  # Object Replace: type data kept aside for switching back.
        if parked:
            value['parked'] = {field: (item if field == 'text' else model_value(item) if field == 'model' else item.document())
                               for field, item in parked.items()}
        return value
    values = {key: getattr(settings, key) for key in GLOBAL_FIELDS}
    if values['travel_speed'] == INSTANT_TRAVEL: values['travel_speed'] = INSTANT
    return dict(format='oscilloscope-music-scene', version=SCENE_VERSION,
                settings=values,
                shapes=[shape_document(shape) for shape in shapes],
                listening_mode=listening_mode, midi_device=midi_device)


def read_scene(path, notices=None):
    return scene_from_document(json.loads(Path(path).read_text(encoding='utf-8')), path, notices)


def scene_from_document(data, path=None, notices=None):
    """(settings, listening mode, MIDI device) from a scene document; path finds a moved MIDI file."""
    if not isinstance(data, dict) or data.get('format') != 'oscilloscope-music-scene' or type(data.get('version')) is not int:
        raise ValueError('Unsupported scene format/version.')
    if data['version'] not in READABLE_SCENE_VERSIONS:
        raise ValueError(f'This scene is version {data["version"]}; this build opens version {" and ".join(map(str, READABLE_SCENE_VERSIONS))} scenes.')
    if set(data) != {'format', 'version', 'settings', 'shapes', 'listening_mode', 'midi_device'}:
        raise ValueError('Scene fields are missing or unsupported.')
    if not isinstance(data.get('shapes'), list):
        raise ValueError('Scene objects are missing.')
    values = data.get('settings')
    if not isinstance(values, dict) or set(values) != set(GLOBAL_FIELDS):
        raise ValueError('Scene settings are missing or unsupported.')
    if values['travel_speed'] == INSTANT: values = dict(values, travel_speed=INSTANT_TRAVEL)
    if path is not None and values['source'] == MIDI_FILE_SOURCE and isinstance(values['midi_file'], str) and values['midi_file']:
        # A scene moved with its MIDI file still finds it beside the scene.
        midi = Path(values['midi_file'])
        if not midi.is_absolute() or not midi.exists():
            nearby = Path(path).resolve().parent/midi.name
            if nearby.exists(): values = dict(values, midi_file=str(nearby))
    try:
        def shape_read(shape):
            value=dict(shape)
            for key in ('muted', 'stack_bypassed'):
                if key in value:
                    if data['version'] < 19 or type(value[key]) is not bool:
                        raise ValueError('Object mute and bypass require scene 19 boolean flags.')
            if 'animation' in value:
                from phosphor.engine.model_animation import Playback
                if data['version'] < 18: raise ValueError('Model animation requires scene version 18.')
                value['animation'] = Playback(**value['animation'])
            if 'lane_clock' in value: raise ValueError('Object lane clocks are runtime only.')
            if 'transform_variables' in value:
                if data['version'] < 24: raise ValueError('Object variables require scene version 24.')
                if not isinstance(value['transform_variables'], list) or not value['transform_variables']:
                    raise ValueError('Invalid object variables.')
                value['transform_variables'] = tuple(read_object_variable(item) for item in value['transform_variables'])
            if 'parked' in value:
                parked = value.pop('parked')
                if data['version'] < 23: raise ValueError('Parked object data requires scene version 23.')
                if not isinstance(parked, dict) or not parked or not set(parked) <= set(TYPE_FIELDS):
                    raise ValueError('Invalid parked object data.')
                if 'matrix' in parked and data['version'] < 25:
                    raise ValueError('Matrix objects require scene version 25.')
                if type_field(value.get('solid', Shape().solid)) in parked or any(field in value for field in parked):
                    raise ValueError("Parked object data repeats the object's own type data.")
                value.update(parked)
            if 'lanes' in value:
                if data['version'] < 13: raise ValueError('Shape lanes require scene version 13.')
                if not isinstance(value['lanes'], dict): raise ValueError('Invalid shape lanes.')
                if 'on' in value['lanes'] and data['version'] < 21: raise ValueError('On/Off lanes require scene version 21.')
                value['lanes'] = tuple(sorted((name, read_points(points, 'Shape lanes')) for name, points in value['lanes'].items()))
                if parked_lanes(value['lanes']) and data['version'] < 23: raise ValueError('Parked variable lanes require scene version 23.')
            def has_points(item):
                if isinstance(item, dict): return 'points' in item or any(has_points(v) for v in item.values())
                if isinstance(item, list): return any(has_points(v) for v in item)
                return False
            if data['version'] < 13:
                # Limit the version gate to automation-bearing fields; drawings also have points.
                for key in ('transforms', 'plot', 'path_start', 'path_trace'):
                    if has_points(value.get(key)): raise ValueError('Gain points require scene version 13.')
            if 'custom' in value:value['custom']=Drawing.read(value['custom'])
            if 'model' in value:
                if data['version'] < 18 and 'animation' in value['model']:
                    raise ValueError('Model animation requires scene version 18.')
                value['model']=Model.read(value['model'])
            if 'motion' in value:value['motion']=Motion.read(value['motion'])
            if 'plot' in value:
                plot = migrate_tempo_variable(value['plot'], notices) if data['version'] < 14 else value['plot']
                value['plot'] = FunctionPlot.read(plot)
            if 'recurrence' in value:
                if data['version'] < 16: raise ValueError('Recurrences require scene version 16.')
                value['recurrence'] = RecurrencePlot.read(value['recurrence'])
            if 'matrix' in value:
                if data['version'] < 25: raise ValueError('Matrix objects require scene version 25.')
                names = tuple(v.name for v in value.get('transform_variables', ()))
                own = value.get('solid', Shape().solid) == MATRIX_OBJECT  # Else parked (object Replace).
                value['matrix'] = MatrixConfig.read(value['matrix'], names if own else None)
            if 'shape_params' in value:
                if data['version'] < 25: raise ValueError('Shape parameters require scene version 25.')
                value['shape_params'] = ShapeParams.read(value['shape_params'], value.get('solid'))
            if 'transforms' in value:
                if data['version']<12 or not isinstance(value['transforms'],list):raise ValueError('Invalid object transforms.')
                def read_transform(item):
                    from phosphor.engine.transforms import NOISE, OLD_NOISE, MAP
                    if isinstance(item, dict) and item.get('kind') in SCENE_25_TRANSFORMS and data['version'] < 25:
                        raise ValueError(f'{item.get("kind")} requires scene version 25.')
                    if isinstance(item, dict) and item.get('kind') == MAP and data['version'] < 22:
                        raise ValueError('Map requires scene version 22.')
                    if isinstance(item, dict) and 'on' in item and data['version'] < 21:
                        raise ValueError('On/Off lanes require scene version 21.')
                    if isinstance(item, dict) and data['version'] < 20:
                        if item.get('kind') == NOISE: raise ValueError('Noise requires scene version 20.')
                        if item.get('kind') == OLD_NOISE:
                            params = item.get('params')
                            if not isinstance(params, dict) or set(params) != {'intensity', 'seed'}:
                                raise ValueError('Invalid legacy White noise values.')
                            item = dict(item, kind=NOISE, params=dict(intensity=params['intensity'], speed=4., detail=2.),
                                        colour='White', rate='Sample')
                    return Transform.read(item, tuple(v.name for v in value.get('transform_variables', ())))
                value['transforms']=tuple(read_transform(item) for item in value['transforms'])
            for key in ('path_start','path_trace'):
                if key in value:
                    item=value[key]
                    if data['version']<12:raise ValueError('Invalid object path window.')
                    value[key]=Param.read(item, f'Path {key}')
            if data['version']==11:
                pose={key:value.pop(key) for key in POSE_FIELDS if key in value}
                if any(type(v) not in (int,float) for v in pose.values()):raise ValueError('Invalid object rotation.')
                value['transforms']=migrated_pose(pose)
            if data['version'] < 17 and used_resting_tilt(value.get('solid', Shape().solid)):
                value['transforms'] = (resting_tilt_step(),) + value.get('transforms', ())
            return kept_parked_lanes(Shape(**value), notices)
        shapes=tuple(shape_read(shape) for shape in data['shapes'])
        settings = Settings(shapes=shapes, **values)
        if settings.sample_rate != 48000:
            raise ValueError('This build uses 48 kHz scenes.')
        validate(settings)
        # Reject excessive curve detail before replacing the scene or starting
        # playback, and warm the immutable render-plan cache on the caller.
        for shape in shapes:
            if shape.solid==CUSTOM_OBJECT:compiled(shape.custom)
            if shape.solid==MODEL_OBJECT:model_route(shape.model)
    except (TypeError, KeyError, OverflowError) as error:
        raise ValueError(f'Invalid scene settings: {error}') from error
    if data.get('listening_mode') not in ('mixed', 'drawing', 'music') or not isinstance(data.get('midi_device'), str):
        raise ValueError('Invalid listening mode or MIDI input name.')
    return settings, data['listening_mode'], data['midi_device']


def validated_preferences(value):
    legacy = {'night_mode', 'background', 'trace', 'grid', 'grid_divisions'}
    if isinstance(value, dict) and set(value) == legacy:
        trace = value['trace']
        if not isinstance(trace, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', trace):
            raise ValueError('Invalid legacy trace color.')
        rgb = lambda color: tuple(int(color[i:i+2], 16) for i in (1, 3, 5))
        legacy_presets = (name for name in PALETTES if name != MACOS_GRAPHITE)
        nearest = min(legacy_presets, key=lambda name: sum((a-b)**2 for a, b in zip(rgb(trace), rgb(PALETTES[name]['trace']))))
        value = dict(palette=nearest, grid_divisions=value['grid_divisions'], crt_scanlines=True, crt_glow=True)
    if isinstance(value, dict) and set(value) == {'palette', 'grid_divisions', 'crt_effects'}:
        if type(value['crt_effects']) is not bool:
            raise ValueError('CRT effects must be enabled or disabled.')
        value = dict(palette=value['palette'], grid_divisions=value['grid_divisions'],
                     crt_scanlines=value['crt_effects'], crt_glow=value['crt_effects'])
    if isinstance(value,dict) and set(value) == {'palette','grid_divisions','crt_scanlines','crt_glow'}:
        value = dict(value,trace_interpolation='Off')
    required = {'palette','grid_divisions','crt_scanlines','crt_glow'}
    if isinstance(value,dict) and required <= set(value) <= set(DEFAULT_PREFERENCES):
        value = dict(DEFAULT_PREFERENCES,**value)
    if not isinstance(value, dict) or set(value) != set(DEFAULT_PREFERENCES):
        raise ValueError('Unsupported appearance preferences.')
    from phosphor.engine.note_tools import ROLL_SCALES
    value = dict(value)
    if value['roll_scale'] not in ROLL_SCALES: value['roll_scale'] = 'Off'
    if value['roll_audio_lane'] not in ('gain', 'pan', 'phase'): value['roll_audio_lane'] = 'gain'
    if type(value['roll_scale_root']) is not int or not 0 <= value['roll_scale_root'] <= 11: value['roll_scale_root'] = 0
    for key in ('roll_scale_snap', 'roll_hear_notes', 'roll_velocity_lane', 'roll_velocity_collapsed', 'roll_audio_gain_lane', 'diagnostic_logging'):
        if type(value[key]) is not bool: value[key] = DEFAULT_PREFERENCES[key]
    geometry = value['manual_geometry']  # Saved as a JSON list; anything else falls back to the default place.
    value['manual_geometry'] = (list(geometry) if isinstance(geometry, (list, tuple)) and len(geometry) == 4
                                and all(type(v) is int and abs(v) <= 100000 for v in geometry) else [])
    value['panel_width'] = panel_width(value['panel_width'])
    value['browser_width'] = browser_width(value['browser_width'])
    from phosphor.display.theme import STYLES, DEFAULT_STYLE, MODES, DEFAULT_MODE
    if value['style'] not in STYLES: value['style'] = DEFAULT_STYLE  # A style from a newer version.
    if value['style_mode'] not in MODES: value['style_mode'] = DEFAULT_MODE
    if type(value['browser_shown']) is not bool: value['browser_shown'] = False
    if value['left_page'] not in ('browser', 'effects'): value['left_page'] = 'browser'
    if not isinstance(value['browser_folder'], str) or len(value['browser_folder']) > 4096: value['browser_folder'] = ''
    for key in ('wheel_sensitivity', 'trackpad_sensitivity'): value[key] = validated_sensitivity(value[key], DEFAULT_PREFERENCES[key])
    if value['display_rate'] not in ('Match monitor', '60 Hz'):
        raise ValueError('Select Match monitor or 60 Hz display rate.')
    if value['palette'] not in PALETTES or any(type(value[key]) is not bool for key in ('crt_scanlines', 'crt_glow', 'show_grid')):
        raise ValueError('Select a CRT palette and enable/disable viewport effects.')
    if type(value['grid_divisions']) is not int or not 2 <= value['grid_divisions'] <= 64:
        raise ValueError('Grid divisions must be an integer from 2 to 64.')
    if not isinstance(value['trace_interpolation'],str) or value['trace_interpolation'] not in INTERPOLATION_FACTORS:
        raise ValueError('Select Off, Cubic 4x or Cubic 8x interpolation.')
    if not isinstance(value['phosphor'],str) or value['phosphor'] not in PHOSPHOR_NAMES:
        raise ValueError('Select a phosphor type.')
    value = dict(value, phosphor_custom=validated_profile(value['phosphor_custom']))
    if not isinstance(value['beam_profile'],str) or value['beam_profile'] not in BEAM_PROFILES:
        raise ValueError('Select a beam profile.')
    if not isinstance(value['graphics_quality'], str) or value['graphics_quality'] not in GRAPHICS_QUALITIES:
        value['graphics_quality'] = DEFAULT_GRAPHICS_QUALITY
    if not isinstance(value['grid_style'], str) or value['grid_style'] not in GRID_STYLES:
        value['grid_style'] = DEFAULT_GRID_STYLE
    for key,(low,high) in VISUAL_RANGES.items():
        if type(value[key]) not in (int,float) or not math.isfinite(value[key]) or not low <= value[key] <= high:
            raise ValueError(f'{key.replace("_"," ")} must be between {low} and {high}.')
    from phosphor.storage.audio_writer import validate_options
    level = value['metronome_level']
    if type(level) not in (int, float) or not math.isfinite(level) or not 0 <= level <= 1:
        raise ValueError('Metronome level must be between 0 and 1.')
    validate_options(value['export_format'], value['export_depth'], value['export_rate'])
    if value['export_range'] not in ('whole', 'loop'): raise ValueError('Select whole song or loop range.')
    if not isinstance(value['ffmpeg_path'], str): raise ValueError('ffmpeg location must be a path or empty for PATH.')
    from phosphor.editor.wheel_mapping import validated_mapping
    return dict(value, **validated_mapping(value))


def read_preferences(path=PREFERENCES_PATH):
    path = Path(path)
    return validated_preferences(json.loads(path.read_text(encoding='utf-8'))) if path.exists() else dict(DEFAULT_PREFERENCES)
