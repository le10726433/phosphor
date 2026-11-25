"""Machine-specific output calibration; separate from appearance and scene files."""
import math
import json
from pathlib import Path
from phosphor.engine.corner_dwell import MAX_DWELL_US
from phosphor.engine.route_plan import ROUTE_MODES, SHORTEST_PATH
from phosphor.engine.switch_sweep import DEFAULT_SWEEP_MS, MAX_SWEEP_MS

# Playback output rate (P7c): 0 follows the device (the old behaviour), else one of these.
# Kept here, not imported from audio_preview, so check_core's Qt-free storage stays light.
FOLLOW_DEVICE = 0
OUTPUT_RATES = (44100, 48000, 88200, 96000, 176400, 192000)
# Playback output device (S2, 0.91.1): '' is the system default, else a device's name.
SYSTEM_DEFAULT_DEVICE = ''
MAX_DEVICE_NAME = 256

from phosphor.storage.app_paths import USER_DIR
SIGNAL_PREFERENCES_PATH = USER_DIR/'signal-preferences.json'
MAX_REDRAWS=32
DEFAULT_SIGNAL_PREFERENCES = dict(corner_dwell_us=0.,shape_redraw=False,shape_redraw_count=1,route_mode=SHORTEST_PATH,
                                  switch_sweep_ms=DEFAULT_SWEEP_MS,output_rate=FOLLOW_DEVICE,
                                  output_device=SYSTEM_DEFAULT_DEVICE)


def validated_signal_preferences(value):
    if isinstance(value,dict) and set(value)=={'corner_dwell_us'}:value=dict(DEFAULT_SIGNAL_PREFERENCES,**value)
    # Files saved before route minimizing get its default (on).
    if isinstance(value,dict) and set(value)=={'corner_dwell_us','shape_redraw','shape_redraw_count'}:value=dict(value,route_mode=SHORTEST_PATH)
    # Files saved before the switch sweep (0.69.0) get its default.
    if isinstance(value,dict) and set(value)=={'corner_dwell_us','shape_redraw','shape_redraw_count','route_mode'}:value=dict(value,switch_sweep_ms=DEFAULT_SWEEP_MS)
    # Files saved before the output rate (0.77.0) follow the device, as before.
    if isinstance(value,dict) and set(value) in (set(DEFAULT_SIGNAL_PREFERENCES)-{'output_rate','output_device'},set(DEFAULT_SIGNAL_PREFERENCES)-{'output_rate'}):value=dict(value,output_rate=FOLLOW_DEVICE)
    # Files saved before the output device (0.91.1) use the system default, as before.
    if isinstance(value,dict) and set(value)==set(DEFAULT_SIGNAL_PREFERENCES)-{'output_device'}:value=dict(value,output_device=SYSTEM_DEFAULT_DEVICE)
    if not isinstance(value,dict) or set(value)!=set(DEFAULT_SIGNAL_PREFERENCES):
        raise ValueError('Unsupported signal-output preferences.')
    dwell=value['corner_dwell_us']
    if type(dwell) not in (int,float) or not math.isfinite(dwell) or not 0<=dwell<=MAX_DWELL_US:
        raise ValueError(f'Corner dwell must be between 0 and {MAX_DWELL_US:g} microseconds.')
    if type(value['shape_redraw']) is not bool or type(value['shape_redraw_count']) is not int or not 1<=value['shape_redraw_count']<=MAX_REDRAWS:
        raise ValueError(f'Shape redraw needs an enabled/disabled checkbox and an integer from 1 to {MAX_REDRAWS}.')
    if value['route_mode'] not in ROUTE_MODES:raise ValueError('Select a route minimizing mode.')
    sweep=value['switch_sweep_ms']
    if type(sweep) not in (int,float) or not math.isfinite(sweep) or not 0<=sweep<=MAX_SWEEP_MS:
        raise ValueError(f'Switch sweep must be between 0 and {MAX_SWEEP_MS:g} milliseconds.')
    rate=value['output_rate']
    if type(rate) is not int or rate not in (FOLLOW_DEVICE,*OUTPUT_RATES):
        raise ValueError('Select Follow device or an output rate from 44.1 to 192 kHz.')
    device=value['output_device']
    if type(device) is not str or len(device)>MAX_DEVICE_NAME:
        raise ValueError('Select System default or an output device.')
    return dict(corner_dwell_us=float(dwell),shape_redraw=value['shape_redraw'],shape_redraw_count=value['shape_redraw_count'],route_mode=value['route_mode'],
                switch_sweep_ms=float(sweep),output_rate=rate,output_device=device)


def read_signal_preferences(path=SIGNAL_PREFERENCES_PATH):
    path=Path(path)
    return validated_signal_preferences(json.loads(path.read_text())) if path.exists() else dict(DEFAULT_SIGNAL_PREFERENCES)
