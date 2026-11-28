"""One phosphor deposit/decay step shared by live and offline displays."""
import numpy as np
from phosphor.display.crt_theme import PALETTES
from phosphor.display.phosphor import (BEAM_GAUSSIAN, BEAM_HARD, DEFAULT_GRAPHICS_QUALITY, DEFAULT_GRID_STYLE,
    PHOSPHOR_OFF, PHOSPHOR_PALETTE, QUALITY_HIGH, QUALITY_LOW, QUALITY_ULTRA,
    beam_uniforms, bloom_uniforms, deposit_vertices, gaussian_beam, halo_uniforms, new_sample_count,
    phosphor_profile, phosphor_uniforms, quality_uniforms, view_scale)
from phosphor.display.qt_geometry import BEAM_VERTEX
from phosphor.display.trace_interpolation import INTERPOLATION_FACTORS, interpolate_tail


def step_display(host, preferences, signal, new_frames, elapsed, *, traces=None,
                 video=False, video_gain=0., scan_lines=480, line_width=.32, video_monitor=False,
                 raw_signal=None, gain=1., decay_elapsed=None):
    """Live playback can cap the sample window while decaying through the whole stall."""
    layer, scope, p = host.phosphor, host.scope, preferences
    scope.signal = signal
    active = layer is not None and (p['phosphor'] != PHOSPHOR_OFF or video)
    scope.draw_trace = not active and not video_monitor
    if layer is None: return
    layer.set_active(active)
    layer.set_video(video, video_gain, scan_lines, line_width)
    if not active: return
    factor = INTERPOLATION_FACTORS[p['trace_interpolation']]
    if raw_signal is not None:
        available = (len(raw_signal) - 1) * factor + 1 if len(raw_signal) >= 2 else len(raw_signal)
    else:
        available = len(signal)
    count = 0 if video else new_sample_count(new_frames, factor, available)
    view = host.view; w, h = scope.width(), scope.height(); s = view_scale(w, h)
    thickness = p['trace_thickness'] * s
    ratio = view.devicePixelRatio()
    quality = p.get('graphics_quality', DEFAULT_GRAPHICS_QUALITY)
    eff_beam = BEAM_HARD if quality == QUALITY_LOW else p['beam_profile']
    eff_glow = False if quality == QUALITY_LOW else p['crt_glow']
    core = gaussian_beam(thickness, ratio, p['beam_brightness'], view_scale=s)[0] if eff_beam == BEAM_GAUSSIAN else max(thickness, 1/ratio)
    if traces is not None:
        parts = [deposit_vertices(points, core, p['beam_brightness'], (), thickness, thickness, drawn, view_scale=s) for points, drawn in traces]
        vertices = np.concatenate(parts) if parts else np.empty(0, BEAM_VERTEX)
    elif count:
        if raw_signal is not None:
            tail = interpolate_tail(raw_signal, p['trace_interpolation'], count)
            if gain != 1.:
                tail = tail * gain
            half = .45 * min(w, h)
            points = tail * np.array([half, -half], dtype=np.float32) + np.array([w / 2, h / 2], dtype=np.float32)
        else:
            points = scope.screen_points()[-count-1:]
        vertices = deposit_vertices(points, core, p['beam_brightness'], (), thickness, thickness, view_scale=s)
    else:
        vertices = np.empty(0, BEAM_VERTEX)
    name = PHOSPHOR_PALETTE if p['phosphor'] == PHOSPHOR_OFF else p['phosphor']
    profile = phosphor_profile(name, PALETTES[p['palette']]['trace'], p['phosphor_custom'])
    bloom_u = bloom_uniforms(eff_glow, thickness, p['glow_size'] * s, p['glow_intensity'], ratio, w, h, view_scale=s)
    halo_u = halo_uniforms(quality in (QUALITY_HIGH, QUALITY_ULTRA) and bloom_u['bloomOn'], w, h, ratio)
    qual_u = quality_uniforms(quality, name, p.get('glass_vignette', .15),
                              p['show_grid'] and not video_monitor,
                              p.get('grid_style', DEFAULT_GRID_STYLE),
                              p['grid_divisions'], bloom_u['bloomOn'], bloom_u['bloomGain'])
    layer.step(vertices, phosphor_uniforms(profile, elapsed, p['beam_intensity'], decay_elapsed=decay_elapsed)
        | beam_uniforms(eff_beam, thickness, ratio, w, h, p['beam_brightness'], view_scale=s)
        | bloom_u | halo_u | qual_u
        | dict(grain=p['phosphor_grain'], shimmer=p['phosphor_shimmer'], viewScale=s))
