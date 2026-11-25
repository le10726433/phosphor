"""User spectrum presets; scenes always retain their own curve."""
import json
from pathlib import Path
from phosphor.engine.transforms import COLOURS, validate_curve
from phosphor.storage.app_files import atomic_json
from phosphor.storage.app_paths import USER_DIR
from phosphor.storage.function_presets import preset_name, MAX_SAVED_PRESETS

NOISE_PRESETS_PATH = USER_DIR/'noise-presets.json'


def _curve(value):
    if not isinstance(value, (list, tuple)) or any(not isinstance(node, (list, tuple)) for node in value):
        raise ValueError('Invalid Noise preset curve.')
    curve = tuple(tuple(node) for node in value)
    validate_curve(curve)
    return tuple((float(hz), float(db)) for hz, db in curve)


def read_noise_presets(path=NOISE_PRESETS_PATH):
    """Missing files are empty; malformed files are never overwritten silently."""
    path = Path(path)
    if not path.exists(): return {}
    try: data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError) as error: raise ValueError(f'Cannot read Noise presets: {error}') from None
    if (not isinstance(data, dict) or set(data) != {'format', 'version', 'presets'}
            or data['format'] != 'phosphor-noise-presets' or type(data['version']) is not int or data['version'] != 1
            or not isinstance(data['presets'], list) or len(data['presets']) > MAX_SAVED_PRESETS):
        raise ValueError('Unsupported Noise presets file.')
    presets = {}
    for item in data['presets']:
        if not isinstance(item, dict) or set(item) != {'name', 'curve'}: raise ValueError('Invalid Noise preset entry.')
        name = preset_name(item['name'], COLOURS)
        if name in presets: raise ValueError('Duplicate Noise preset name.')
        presets[name] = _curve(item['curve'])
    return presets


def _write(presets, path):
    if len(presets) > MAX_SAVED_PRESETS: raise ValueError(f'Keep at most {MAX_SAVED_PRESETS} saved presets.')
    atomic_json(path, dict(format='phosphor-noise-presets', version=1,
                           presets=[dict(name=name, curve=[list(node) for node in curve]) for name, curve in presets.items()]))


def save_noise_preset(name, curve, path=NOISE_PRESETS_PATH):
    name, curve = preset_name(name, COLOURS), _curve(curve)
    presets = read_noise_presets(path); presets[name] = curve
    _write(presets, path)
    return presets


def delete_noise_preset(name, path=NOISE_PRESETS_PATH):
    name = preset_name(name, COLOURS)
    presets = read_noise_presets(path); presets.pop(name, None)
    _write(presets, path)
    return presets
