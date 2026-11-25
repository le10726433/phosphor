"""User-saved Recurrence equation presets (USER_DIR/recurrence-presets.json)."""
import json
from pathlib import Path

from phosphor.engine.recurrence import RecurrencePlot, PRESETS
from phosphor.storage.app_files import atomic_json
from phosphor.storage.app_paths import USER_DIR
from phosphor.storage.function_presets import MAX_SAVED_PRESETS, PresetStore, preset_name

RECURRENCE_PRESETS_PATH = USER_DIR/'recurrence-presets.json'
FORMAT, VERSION = 'phosphor-recurrence-presets', 1


def read_recurrence_presets(path=RECURRENCE_PRESETS_PATH, notices=None):
    """Saved presets in saved order. A missing file is empty; damaged files raise."""
    path = Path(path)
    if not path.exists(): return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(data, dict) or data.get('format') != FORMAT or data.get('version') != VERSION
            or not isinstance(data.get('presets'), list)):
        raise ValueError('Unsupported recurrence presets file.')
    presets = {}
    for item in data['presets']:
        if not isinstance(item, dict) or set(item) != {'name', 'recurrence'}:
            raise ValueError('Recurrence presets file has an invalid entry.')
        presets[preset_name(item['name'], PRESETS)] = RecurrencePlot.read(item['recurrence'])
    return presets


def write_recurrence_presets(presets, path=RECURRENCE_PRESETS_PATH):
    if len(presets) > MAX_SAVED_PRESETS: raise ValueError(f'Keep at most {MAX_SAVED_PRESETS} saved presets.')
    atomic_json(path, dict(format=FORMAT, version=VERSION, presets=[
        dict(name=preset_name(name, PRESETS), recurrence=plot.document()) for name, plot in presets.items()]))


def save_recurrence_preset(name, plot, path=RECURRENCE_PRESETS_PATH):
    """Add or replace one preset, re-reading the file so other saves are kept."""
    name = preset_name(name, PRESETS)
    presets = read_recurrence_presets(path)
    presets[name] = plot
    write_recurrence_presets(presets, path)
    return presets


def delete_recurrence_preset(name, path=RECURRENCE_PRESETS_PATH):
    presets = read_recurrence_presets(path)
    presets.pop(name, None)
    write_recurrence_presets(presets, path)
    return presets


RECURRENCE_STORE = PresetStore(RECURRENCE_PRESETS_PATH, PRESETS, read_recurrence_presets,
                               save_recurrence_preset, delete_recurrence_preset)
