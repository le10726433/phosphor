"""User-saved x(t), y(t) function presets, kept per user beside the preferences."""
import json
from pathlib import Path

from phosphor.engine.function_plot import FunctionPlot, PRESETS
from phosphor.storage.app_files import atomic_json
from phosphor.storage.app_paths import USER_DIR

FUNCTION_PRESETS_PATH = USER_DIR/'function-presets.json'
MAX_PRESET_NAME = 40
MAX_SAVED_PRESETS = 200


def preset_name(name):
    """Validated, trimmed preset name; built-in names are reserved."""
    name = name.strip() if isinstance(name, str) else ''
    if not name: raise ValueError('Enter a preset name.')
    if len(name) > MAX_PRESET_NAME: raise ValueError(f'Keep preset names under {MAX_PRESET_NAME} characters.')
    if name in PRESETS: raise ValueError(f'"{name}" is a built-in preset; choose another name.')
    return name


def read_function_presets(path=FUNCTION_PRESETS_PATH):
    """Saved presets in saved order. A missing file is empty; damaged files raise."""
    path = Path(path)
    if not path.exists(): return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('format') != 'phosphor-function-presets' or not isinstance(data.get('presets'), list):
        raise ValueError('Unsupported function presets file.')
    presets = {}
    for item in data['presets']:
        if not isinstance(item, dict) or set(item) != {'name', 'function'}:
            raise ValueError('Function presets file has an invalid entry.')
        presets[preset_name(item['name'])] = FunctionPlot.read(item['function'])
    return presets


def write_function_presets(presets, path=FUNCTION_PRESETS_PATH):
    if len(presets) > MAX_SAVED_PRESETS: raise ValueError(f'Keep at most {MAX_SAVED_PRESETS} saved presets.')
    atomic_json(path, dict(format='phosphor-function-presets', version=1,
                           presets=[dict(name=preset_name(name), function=plot.document()) for name, plot in presets.items()]))


def save_function_preset(name, plot, path=FUNCTION_PRESETS_PATH):
    """Add or replace one preset, re-reading the file so other saves are kept."""
    name = preset_name(name)
    presets = read_function_presets(path)
    presets[name] = plot
    write_function_presets(presets, path)
    return presets


def delete_function_preset(name, path=FUNCTION_PRESETS_PATH):
    presets = read_function_presets(path)
    presets.pop(name, None)
    write_function_presets(presets, path)
    return presets
