"""Versioned scene files and separately persisted appearance preferences."""
from dataclasses import asdict
import json
from pathlib import Path
import re
import math
import tempfile
import os
from crt_theme import PALETTES
from signal_engine import Settings, Shape, validate, scene_shapes
from score_source import SCALE_NAME, MIDI_FILE_SOURCE
from trace_interpolation import INTERPOLATION_FACTORS
from phosphor import PHOSPHORS, PHOSPHOR_P31, BEAM_GAUSSIAN, BEAM_PROFILES
from glow_trail import VISUAL_DEFAULTS, VISUAL_RANGES
from custom_shape import Drawing, CUSTOM_OBJECT, compiled
from model_object import MODEL_OBJECT, Model, model_route
from motion_path import Motion

from app_paths import APP_DIR, USER_DIR
PREFERENCES_PATH = USER_DIR / 'preferences.json'
DEFAULT_PREFERENCES = dict(palette='Green monochrome', grid_divisions=8, crt_scanlines=True, crt_glow=True, trace_interpolation='Off', phosphor=PHOSPHOR_P31, beam_profile=BEAM_GAUSSIAN, **VISUAL_DEFAULTS)
GLOBAL_FIELDS = ('sample_rate', 'source', 'drawing_hz', 'bpm', 'drawing_gain', 'music_gain',
                 'midi_preset', 'midi_channel', 'midi_gain', 'travel_mode', 'travel_speed')
# Earlier private builds bundled songs that the public edition does not include.
REMOVED_SOURCES = ("Howel's Moving Castle", "Howl's Moving Castle", "A Cruel Angel's Thesis")


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
    if listening_mode not in ('mixed', 'drawing', 'music'):
        raise ValueError('Invalid listening mode.')
    shapes=scene_shapes(settings)
    def shape_document(shape):
        value=asdict(shape)
        if shape.solid!='Text':value.pop('text')
        if shape.solid==CUSTOM_OBJECT:value['custom']=shape.custom.document()
        else:value.pop('custom')
        if shape.solid==MODEL_OBJECT:value['model']=shape.model.document()
        else:value.pop('model')
        if not shape.uid:value.pop('uid')
        for key in ('spin_x','spin_y','spin_z'):
            if value[key]==0:value.pop(key)
        if shape.motion is None:value.pop('motion')
        return value
    values = {key: getattr(settings, key) for key in GLOBAL_FIELDS}
    if settings.source == MIDI_FILE_SOURCE: values['midi_file'] = settings.midi_file
    return dict(format='oscilloscope-music-scene', version=7 if settings.source == MIDI_FILE_SOURCE else 6 if any(s.solid==MODEL_OBJECT or s.spin_x or s.spin_y or s.spin_z for s in shapes) else 5 if any(s.solid==CUSTOM_OBJECT and (s.custom.guides or s.custom.attachments) for s in shapes) else 4 if any(s.uid or s.motion or (s.solid==CUSTOM_OBJECT and (s.custom.motion_path or any(c.kind=='Circle' for c in s.custom.curves))) for s in shapes) else 3 if any(s.solid==CUSTOM_OBJECT and s.custom.curves for s in shapes) else 2 if any(s.solid==CUSTOM_OBJECT for s in shapes) else 1,
                settings=values,
                shapes=[shape_document(shape) for shape in shapes],
                listening_mode=listening_mode, midi_device=midi_device)


def read_scene(path):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('format') != 'oscilloscope-music-scene' or type(data.get('version')) is not int or data.get('version') not in (1,2,3,4,5,6,7):
        raise ValueError('Unsupported scene format/version.')
    if not isinstance(data.get('shapes'), list) or not data['shapes']:
        raise ValueError('Scene must contain at least one shape.')
    values = data.get('settings')
    if not isinstance(values, dict) or set(values)-{'midi_file'} != set(GLOBAL_FIELDS):
        raise ValueError('Scene settings are missing or unsupported.')
    try:
        if values['source'] in REMOVED_SOURCES:
            values = dict(values, source=SCALE_NAME)
        if values.get('midi_file'):
            # A scene moved with its MIDI file still finds it beside the scene.
            midi = Path(values['midi_file'])
            if not midi.is_absolute() or not midi.exists():
                nearby = Path(path).resolve().parent/midi.name
                if nearby.exists(): values = dict(values, midi_file=str(nearby))
        def shape_read(shape):
            value=dict(shape)
            if 'custom' in value:value['custom']=Drawing.read(value['custom'])
            if 'model' in value:value['model']=Model.read(value['model'])
            if 'motion' in value:value['motion']=Motion.read(value['motion'])
            return Shape(**value)
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
        nearest = min(PALETTES, key=lambda name: sum((a-b)**2 for a, b in zip(rgb(trace), rgb(PALETTES[name]['trace']))))
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
    if value['palette'] not in PALETTES or any(type(value[key]) is not bool for key in ('crt_scanlines', 'crt_glow')):
        raise ValueError('Select a CRT palette and enable/disable viewport effects.')
    if type(value['grid_divisions']) is not int or not 2 <= value['grid_divisions'] <= 64:
        raise ValueError('Grid divisions must be an integer from 2 to 64.')
    if not isinstance(value['trace_interpolation'],str) or value['trace_interpolation'] not in INTERPOLATION_FACTORS:
        raise ValueError('Select Off, Cubic 4x or Cubic 8x interpolation.')
    if not isinstance(value['phosphor'],str) or value['phosphor'] not in PHOSPHORS:
        raise ValueError('Select a phosphor type.')
    if not isinstance(value['beam_profile'],str) or value['beam_profile'] not in BEAM_PROFILES:
        raise ValueError('Select a beam profile.')
    for key,(low,high) in VISUAL_RANGES.items():
        if type(value[key]) not in (int,float) or not math.isfinite(value[key]) or not low <= value[key] <= high:
            raise ValueError(f'{key.replace("_"," ")} must be between {low} and {high}.')
    return dict(value)


def read_preferences(path=PREFERENCES_PATH):
    path = Path(path)
    return validated_preferences(json.loads(path.read_text(encoding='utf-8'))) if path.exists() else dict(DEFAULT_PREFERENCES)
