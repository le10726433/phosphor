"""User-saved transforms (T6): USER_DIR/transform-presets.json.

One file of named step and stack presets (engine/transform_presets.py), in saved order. A
missing file is empty; a damaged file, or one written by a newer phosphor, raises ValueError
and is never overwritten (every save, rename and delete re-reads it first). Scenes keep their
own copy of whatever they inserted, so nothing here changes a scene. The editor writes this
file only once main() has enabled user files (Window.remember_recent).
"""
import json
from pathlib import Path

from phosphor.engine.transform_presets import TransformPreset, validate_preset
from phosphor.storage.app_files import atomic_json
from phosphor.storage.app_paths import USER_DIR
from phosphor.storage.function_presets import MAX_SAVED_PRESETS, preset_name

TRANSFORM_PRESETS_PATH = USER_DIR/'transform-presets.json'
FORMAT = 'phosphor-transform-presets'
VERSION = 1


def read_transform_presets(path=TRANSFORM_PRESETS_PATH):
    """{name: TransformPreset} in saved order."""
    path = Path(path)
    if not path.exists(): return {}
    try: data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError, RecursionError) as error: raise ValueError(f'Cannot read saved transforms: {error}') from None
    if not isinstance(data, dict) or data.get('format') != FORMAT: raise ValueError('Unsupported saved transforms file.')
    version = data.get('version')
    if type(version) is int and version > VERSION:
        raise ValueError(f'The saved transforms file is from a newer phosphor (version {version}); update phosphor to use it.')
    if (type(version) is not int or version != VERSION or set(data) != {'format', 'version', 'presets'} or not isinstance(data['presets'], list)
            or len(data['presets']) > MAX_SAVED_PRESETS):
        raise ValueError('Unsupported saved transforms file.')
    presets = {}
    for item in data['presets']:
        if not isinstance(item, dict) or set(item) != {'name', 'preset'}: raise ValueError('Invalid saved transform entry.')
        if not isinstance(item['name'], str): raise ValueError('Invalid saved transform name.')
        name = preset_name(item['name'], ())
        if name in presets: raise ValueError(f'Saved transforms file has two presets named "{name}".')
        # A hand-damaged entry may fail with any of these (a list as a kind, a 400-digit number…):
        # all are reported as a damaged file, so the app still opens.
        try: presets[name] = TransformPreset.read(item['preset'])
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError) as error:
            raise ValueError(f'Saved transform "{name}": {error}') from None
    return presets


def write_transform_presets(presets, path=TRANSFORM_PRESETS_PATH):
    if len(presets) > MAX_SAVED_PRESETS: raise ValueError(f'Keep at most {MAX_SAVED_PRESETS} saved transforms.')
    for preset in presets.values(): validate_preset(preset)
    atomic_json(path, dict(format=FORMAT, version=VERSION,
                           presets=[dict(name=preset_name(name, ()), preset=preset.document()) for name, preset in presets.items()]))


def save_transform_preset(name, preset, path=TRANSFORM_PRESETS_PATH):
    """Add or replace one preset (re-reading the file so other saves are kept)."""
    name = preset_name(name, ())
    presets = read_transform_presets(path); presets[name] = preset
    write_transform_presets(presets, path)
    return presets


def rename_transform_preset(old, new, path=TRANSFORM_PRESETS_PATH):
    """Rename old to new in place, keeping the order; refuses a name another preset has."""
    new = preset_name(new, ())
    presets = read_transform_presets(path)
    if old not in presets: raise ValueError(f'There is no saved transform "{old}".')
    if new != old and new in presets: raise ValueError(f'A saved transform is already named "{new}".')
    presets = {new if name == old else name: preset for name, preset in presets.items()}
    write_transform_presets(presets, path)
    return presets


def delete_transform_preset(name, path=TRANSFORM_PRESETS_PATH):
    presets = read_transform_presets(path); presets.pop(name, None)
    write_transform_presets(presets, path)
    return presets
