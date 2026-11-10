"""Machine-specific output calibration; separate from appearance and scene files."""
import math
import json
from pathlib import Path
from corner_dwell import MAX_DWELL_US

from app_paths import USER_DIR
SIGNAL_PREFERENCES_PATH = USER_DIR/'signal-preferences.json'
MAX_REDRAWS=32
DEFAULT_SIGNAL_PREFERENCES = dict(corner_dwell_us=0.,shape_redraw=False,shape_redraw_count=1)


def validated_signal_preferences(value):
    if isinstance(value,dict) and set(value)=={'corner_dwell_us'}:value=dict(DEFAULT_SIGNAL_PREFERENCES,**value)
    if not isinstance(value,dict) or set(value)!=set(DEFAULT_SIGNAL_PREFERENCES):
        raise ValueError('Unsupported signal-output preferences.')
    dwell=value['corner_dwell_us']
    if type(dwell) not in (int,float) or not math.isfinite(dwell) or not 0<=dwell<=MAX_DWELL_US:
        raise ValueError(f'Corner dwell must be between 0 and {MAX_DWELL_US:g} microseconds.')
    if type(value['shape_redraw']) is not bool or type(value['shape_redraw_count']) is not int or not 1<=value['shape_redraw_count']<=MAX_REDRAWS:
        raise ValueError(f'Shape redraw needs an enabled/disabled checkbox and an integer from 1 to {MAX_REDRAWS}.')
    return dict(corner_dwell_us=float(dwell),shape_redraw=value['shape_redraw'],shape_redraw_count=value['shape_redraw_count'])


def read_signal_preferences(path=SIGNAL_PREFERENCES_PATH):
    path=Path(path)
    return validated_signal_preferences(json.loads(path.read_text())) if path.exists() else dict(DEFAULT_SIGNAL_PREFERENCES)
