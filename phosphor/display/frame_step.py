"""One phosphor deposit/decay step shared by live and offline displays."""
import numpy as np
from phosphor.display.crt_theme import PALETTES
from phosphor.display.phosphor import (BEAM_GAUSSIAN, PHOSPHOR_OFF, PHOSPHOR_PALETTE,
    beam_uniforms, bloom_uniforms, deposit_vertices, gaussian_beam, new_sample_count,
    phosphor_profile, phosphor_uniforms, view_scale)
from phosphor.display.qt_geometry import BEAM_VERTEX
from phosphor.display.trace_interpolation import INTERPOLATION_FACTORS


def step_display(host, preferences, signal, new_frames, elapsed, *, traces=None,
                 video=False, video_gain=0., scan_lines=480, line_width=.32, video_monitor=False):
    layer, scope, p = host.phosphor, host.scope, preferences
    scope.signal = signal
    active = layer is not None and (p['phosphor'] != PHOSPHOR_OFF or video)
    scope.draw_trace = not active and not video_monitor
    if layer is None: return
    layer.set_active(active)
    layer.set_video(video, video_gain, scan_lines, line_width)
    if not active: return
    count = 0 if video else new_sample_count(new_frames, INTERPOLATION_FACTORS[p['trace_interpolation']], len(signal))
    view = host.view; s = view_scale(scope.width(), scope.height())
    thickness = p['trace_thickness'] * s
    ratio = view.devicePixelRatio()
    core = gaussian_beam(thickness, ratio, p['beam_brightness'], view_scale=s)[0] if p['beam_profile'] == BEAM_GAUSSIAN else max(thickness, 1/ratio)
    if traces is not None:
        parts = [deposit_vertices(points, core, p['beam_brightness'], (), thickness, thickness, drawn, view_scale=s) for points, drawn in traces]
        vertices = np.concatenate(parts) if parts else np.empty(0, BEAM_VERTEX)
    else:
        vertices = deposit_vertices(scope.screen_points()[-count-1:], core, p['beam_brightness'], (), thickness, thickness, view_scale=s) if count else np.empty(0, BEAM_VERTEX)
    name = PHOSPHOR_PALETTE if p['phosphor'] == PHOSPHOR_OFF else p['phosphor']
    profile = phosphor_profile(name, PALETTES[p['palette']]['trace'], p['phosphor_custom'])
    layer.step(vertices, phosphor_uniforms(profile, elapsed, p['beam_intensity'])
        | beam_uniforms(p['beam_profile'], thickness, ratio, scope.width(), scope.height(), p['beam_brightness'], view_scale=s)
        | bloom_uniforms(p['crt_glow'], thickness, p['glow_size'] * s, p['glow_intensity'], ratio, scope.width(), scope.height(), view_scale=s)
        | dict(grain=p['phosphor_grain'], shimmer=p['phosphor_shimmer'], viewScale=s))
