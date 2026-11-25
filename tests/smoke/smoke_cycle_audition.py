import _path  # noqa: F401
"""Native Composer cycle audition; only device-dependent checks may skip."""
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from phosphor.editor import qt_app as q
from phosphor.engine.composition import COMPOSER, SANDBOX, Clip, Note, Project, song_cycle
from phosphor.engine.function_plot import FunctionPlot, PARAMETRIC_OBJECT
from phosphor.engine.signal_engine import Settings, Shape, TONE_SOURCE


q.configure_graphics()
app = QApplication([])
w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=COMPOSER))
w.force_close = True
failures = []
native_start = w.audio.start


def start(settings, mode, **kwargs):
    try:
        return native_start(settings, mode, **kwargs)
    except Exception as error:
        failures.append(error)
        raise


def events(milliseconds=180):
    # A real event loop releases the GIL to PortAudio; QTest.qWait does not.
    loop = QEventLoop()
    QTimer.singleShot(milliseconds, loop.quit)
    loop.exec()
    w.tick()


def expected_length(seconds):
    machine = replace(w.output_settings(), source=TONE_SOURCE, motion_time_offset=0.)
    samples, period, reason = song_cycle(w.project, seconds, machine)
    assert reason == '' and samples is not None
    assert len(samples) == round(period * w.audio.sample_rate)
    np.testing.assert_array_equal(w.cycle_buffer, samples)
    return len(samples)


try:
    shape = Shape(solid=PARAMETRIC_OBJECT, plot=FunctionPlot('cos(tau*s)', 'sin(tau*s)'))
    scene = Settings(shapes=(shape,), travel_speed=float('inf'))
    project = Project(notes=(Note(0., 4., 57), Note(4., 4., 64)),
                      clips=(Clip(0., 8., scene),))
    w.set_project(project)
    w.resize(1200, 840); w.show()
    with patch.object(w.audio, 'start', start):
        w.seek_song(.7)
        w.composer_panel.cycle.setChecked(True)
        events()
        first = expected_length(.7)
        assert w.composer_panel.play.text() == 'Play'
        if not failures:
            assert w.audio.cycle_running and len(w.audio.cycle_samples) == first
        w.seek_song(2.7)
        events()
        second = expected_length(2.7)
        assert second != first
        assert w.composer_panel.play.text() == 'Play'
        assert w.play_button.text() == 'Play'
        if failures:
            try:
                import sounddevice as sd
            except ImportError:
                allowed = all(isinstance(error, RuntimeError) and 'Install playback support' in str(error)
                              for error in failures)
            else:
                allowed = all(isinstance(error, sd.PortAudioError) for error in failures)
            assert allowed, failures
            print('SKIP device-dependent cycle checks: ' + str(failures[-1]))
            # Engine, seek wiring and stopped Play labels were still checked above.
            w.composer_panel.cycle.setChecked(False)
            assert not w.audio.running and not w.audio.cycle
        else:
            assert w.audio.cycle_running and w.audio.composer is None
            assert len(w.audio.cycle_samples) == second
            assert w.audio.cycle_samples.flags.writeable is False
            assert not w.audio.error, w.audio.error
            w.start_song(count_in=0)
            events()
            assert w.audio.running and w.audio.composer is not None and not w.audio.cycle
            w.stop_song()
            events()
            assert w.audio.cycle_running and w.audio.composer is None
            w.composer_panel.cycle.setChecked(False)
            events()
            assert not w.audio.running and not w.audio.cycle
            # Native stream reuse after a complete checkbox stop.
            w.composer_panel.cycle.setChecked(True)
            events()
            assert w.audio.cycle_running
            w.set_workspace(SANDBOX)
            events()
            assert not w.audio.running and not w.audio.cycle
finally:
    w.composer_panel.cycle.setChecked(False)
    w.audio.stop()
    w.release_views()
    w.close()

print('Cycle audition passed: engine/seek wiring, Play labels and available native audio lifecycle.')
