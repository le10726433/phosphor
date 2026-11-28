"""Smoke test: P0 display optimizations keep rendering identical while skipping redundant work."""
import argparse
import sys
from pathlib import Path
from types import SimpleNamespace
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from phosphor.editor.qt_platform import configure_graphics
configure_graphics()

from PySide6.QtWidgets import QApplication
from phosphor.editor.qt_app import Window
from phosphor.display.frame_step import step_display
from phosphor.display.trace_interpolation import interpolate_trace
from phosphor.display.phosphor import PHOSPHOR_OFF, PHOSPHOR_P31
from phosphor.storage.app_files import DEFAULT_PREFERENCES
from phosphor.engine.composition import SANDBOX


def pump(app, n=10):
    for _ in range(n):
        app.processEvents()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scale', type=float, default=1.0)
    parser.parse_args()

    app = QApplication.instance() or QApplication([])
    opts = SimpleNamespace(scene=None, workspace=SANDBOX, ready_file=None, skip_recovery=True)
    window = Window(opts)
    try:
        window.show()
        pump(app, 20)

        host = window.host
        layer = host.phosphor
        assert layer is not None, 'PhosphorLayer unavailable on this platform'

        # 1. Verify raw_signal tail interpolation path produces identical deposit vertices
        # to the full pre-interpolated signal path.
        rng = np.random.default_rng(123)
        raw_sig = rng.uniform(-0.8, 0.8, (1920, 2)).astype(np.float32)
        prefs = dict(DEFAULT_PREFERENCES, phosphor=PHOSPHOR_P31, trace_interpolation='Cubic 4x')

        captured = []
        orig_step = layer.step

        def record_step(vertices, uniforms):
            captured.append((vertices.copy(), dict(uniforms)))
            orig_step(vertices, uniforms)

        layer.step = record_step
        try:
            full_trace = interpolate_trace(raw_sig, 'Cubic 4x') * 1.5
            step_display(host, prefs, full_trace, 800, 1 / 60)
            step_display(host, prefs, raw_sig, 800, 1 / 60, raw_signal=raw_sig, gain=1.5)
        finally:
            layer.step = orig_step

        assert len(captured) == 2, f'Expected 2 step calls, got {len(captured)}'
        v_full, u_full = captured[0]
        v_tail, u_tail = captured[1]
        assert len(v_full) > 0, 'Expected non-empty deposit vertices'
        assert v_full.shape == v_tail.shape, f'Vertex shape mismatch: {v_full.shape} vs {v_tail.shape}'
        np.testing.assert_allclose(v_tail['x'], v_full['x'], atol=1e-4)
        np.testing.assert_allclose(v_tail['y'], v_full['y'], atol=1e-4)
        assert u_full == u_tail, 'Uniform dicts differed between full and tail paths'

        # 2. Verify PhosphorLayer uniform cache populates and skips unchanged uniforms.
        assert len(layer._uniforms) > 0, 'PhosphorLayer._uniforms was not populated'
        before_cache = dict(layer._uniforms)
        layer.step(v_tail, u_tail)
        assert layer._uniforms == before_cache, 'Uniform cache mutated on identical uniforms'

        # 3. Verify GpuScope phosphor mode caches empty trace/beam state and overlay state.
        window.apply_preferences(dict(window.preferences, phosphor=PHOSPHOR_P31))
        window.display_frame()
        pump(app, 10)
        scope = host.scope
        assert not scope.draw_trace, 'Expected draw_trace=False in phosphor mode'
        # Verify switching phosphor off restores draw_trace=True cleanly.
        window.apply_preferences(dict(window.preferences, phosphor=PHOSPHOR_OFF))
        window.display_frame()
        pump(app, 10)
        assert scope.draw_trace, 'Expected draw_trace=True when phosphor is Off'

        # 4. Verify Shape Builder skips hidden main scope updates while updating builder preview.
        window.apply_preferences(dict(window.preferences, phosphor=PHOSPHOR_P31))
        window.open_builder(new=True)
        pump(app, 10)
        assert window.building, 'Expected window.building to be True'
        main_calls = 0
        orig_update_phosphor = window.update_phosphor

        def count_main_phosphor(*args, **kwargs):
            nonlocal main_calls
            main_calls += 1
            return orig_update_phosphor(*args, **kwargs)

        window.update_phosphor = count_main_phosphor
        try:
            window.display_frame()
            pump(app, 5)
        finally:
            window.update_phosphor = orig_update_phosphor

        assert main_calls == 0, f'Hidden main phosphor stepped {main_calls} times in Shape Builder'
        assert len(window.builder.preview.scope.signal) > 0, 'Builder preview scope did not receive signal'
        window.leave_builder()
        pump(app, 5)

        print('smoke_p0_display_perf OK')
    finally:
        window.force_close = True
        window.close()
        pump(app, 5)


if __name__ == '__main__':
    main()
