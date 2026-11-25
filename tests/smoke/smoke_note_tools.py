"""N4 gestures and tools round-trip through Composer history; native or --headless."""
import _path  # noqa: F401
import os
import sys
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import Qt, QPoint, QPointF, QEvent, QEventLoop, QTimer
from PySide6.QtGui import QMouseEvent, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from phosphor.editor import qt_app as q, composer_view as cv
from phosphor.engine.composition import COMPOSER, Note, MAX_NOTES
from phosphor.engine.note_tools import in_scale
from phosphor.storage import project_files
from phosphor.audio.audio_preview import AudioPreview


class SilentPreview(AudioPreview):
    """Exercise the real cycle buffer without opening a device."""
    active = False

    @property
    def running(self): return self.active

    def start(self, settings, mode, **kwargs):
        self.active = True; self.parameters = (settings, mode)

    def stop(self):
        self.active = self.cycle = False

q.configure_graphics(); app = QApplication([])
clock = [0.]


def wait(ms=30):
    loop = QEventLoop(); QTimer.singleShot(ms, loop.quit); loop.exec()


def flush():
    w.commit_history(now=clock[0]); app.processEvents()


def fixture(notes):
    clock[0] += 1.; w.set_project(replace(w.project, notes=tuple(notes))); flush()
    view.focus_kind = 'notes'; view.note_selection = set(range(len(notes))); view.selected_note = None
    view.setFocus(); wait()


def round_trip(label, edit):
    clock[0] += 1.; before = w.project.notes
    view.setFocus(); wait(); edit(); flush()
    assert w.project.notes != before, label
    assert view.note_selection, label + ' selection'
    w.undo(); assert w.project.notes == before, label + ' undo'
    wait()


def point(beat, pitch):
    y = round(view.y_of(pitch) + view.row / 2); assert view.roll_top <= y < view.note_bottom(), 'note-roll click outside the visible roll'
    return QPoint(round(view.x_of(beat)), y)


def copy_drag():
    start, end = point(1.3, 60), point(2.3, 62)
    original = w.project.notes
    QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.AltModifier, start)
    for target in (point(1.8, 61), end):
        app.sendEvent(view, QMouseEvent(QEvent.Type.MouseMove, QPointF(target), QPointF(view.mapToGlobal(target)),
                      Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.AltModifier))
    QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.AltModifier, end)
    assert w.project.notes[:len(original)] == original
    assert len(w.project.notes) == len(original) * 2


with tempfile.TemporaryDirectory() as folder:
    with patch.object(project_files, 'WORKSPACE_PATH', Path(folder) / 'workspace.json'), \
         patch.object(q, 'write_workspace', lambda name: None), \
         patch('phosphor.editor.history.monotonic', side_effect=lambda: clock[0]):
        w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=COMPOSER))
        try:
            w.resize(1220, 840); w.show(); w.activateWindow(); wait(100); w.timer.stop()
            view = w.composer_panel.timeline
            view.scale, view.scroll, view.top_pitch = 80., 0., 68  # Rows 68–58 stay above the velocity lane.
            view.roll_scale, view.roll_scale_snap, view.roll_hear_notes = 'Off', False, True
            audition_note = w.audition_note
            calls = []; w.audition_note = lambda pitch, beat: calls.append((pitch, beat))
            view.draw_mode = 'Chord'; fixture(())
            def stamp():
                QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(1., 60))
                assert len(w.project.notes) == 3
                assert {n.pitch for n in w.project.notes} == {60, 64, 67}
                assert calls[-1] == ((60, 64, 67), 1.)
            round_trip('chord stamp', stamp)
            # Length drags resize the whole stamped chord, in the same Undo step.
            def stamp_drag():
                start, end = point(1., 60), point(2., 60)
                QTest.mousePress(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
                app.sendEvent(view, QMouseEvent(QEvent.Type.MouseMove, QPointF(end), QPointF(view.mapToGlobal(end)),
                              Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
                QTest.mouseRelease(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
                assert len({n.length for n in w.project.notes}) == 1 and w.project.notes[0].length == 1.
            round_trip('chord length drag', stamp_drag)
            fixture((Note(1., 1., 60), Note(1., 1., 64)))
            round_trip('Ctrl+D', lambda: QTest.keyClick(view, Qt.Key.Key_D, Qt.KeyboardModifier.ControlModifier))
            view.note_selection = {0, 1}; round_trip('Alt-drag', copy_drag)
            chord = (Note(1., 1., 60, .3, .2), Note(1., 1., 64), Note(1., 1., 67))
            for label, notes, edit in (
                ('split', chord, lambda: view.split_selection(1.5)),
                ('join', (Note(1., 1., 60), Note(2., 1., 60)), view.join_selection),
                ('legato', (*chord, Note(3., 1., 72)), view.legato_selection),
                ('quantize', (Note(1.12, .8, 60),), lambda: view.quantize_selection(.25, .5, .3, True)),
                ('humanize', chord, lambda: view.humanize_selection(.02, .05, 1)),
                ('strum', chord, lambda: view.strum_selection(.03125, 'up')),
                ('arpeggiate', chord, lambda: view.arpeggiate_selection('up-down'))):
                fixture(notes); round_trip(label, edit)
            fixture(chord); view.note_selection.clear(); view.playhead = 1.5
            round_trip('Ctrl+E no selection', lambda: QTest.keyClick(view, Qt.Key.Key_E, Qt.KeyboardModifier.ControlModifier))
            fixture(chord)
            menu = view.build_note_tools_menu(); assert any(a.text() == 'Draw' for a in menu.actions()); menu.deleteLater()
            menu = view.build_note_menu(0, 1.5); assert any(a.text() == 'Split here' for a in menu.actions()); menu.deleteLater()
            view.roll_scale, view.roll_scale_root = 'Major', 0
            assert not view.grab().isNull()
            old_row, old_scale = view.row, view.scale
            pos = QPointF(20, view.roll_top + 70)
            app.sendEvent(view, QWheelEvent(pos, QPointF(view.mapToGlobal(pos.toPoint())), QPoint(), QPoint(0, 120),
                          Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier, Qt.ScrollPhase.NoScrollPhase, False))
            assert view.row != old_row and view.scale == old_scale
            fixture(()); view.draw_mode = 'Note'; view.roll_scale_snap = True
            round_trip('scale draw', lambda: QTest.mouseClick(view, Qt.MouseButton.LeftButton,
                       Qt.KeyboardModifier.NoModifier, point(1., 61)))
            w.redo(); assert in_scale(w.project.notes[-1].pitch, 'Major', 0); flush()
            assert calls[-1][0] == 60
            before_calls = len(calls)
            QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, point(1.1, 60))
            assert len(calls) == before_calls
            QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(1.1, 60))
            assert len(calls) == before_calls + 1
            view.roll_hear_notes = False
            QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(1.1, 60))
            assert len(calls) == before_calls + 1
            # A rejected edit emits a notice without touching the project or selection.
            fixture((Note(0., 1., 60),) * MAX_NOTES); view.note_selection = {0}
            notices = []; view.notice.connect(notices.append); before = w.project
            view.duplicate_selection(); assert w.project is before and notices and view.note_selection == {0}
            fixture((Note(1., .1, 60),))
            real_audio, fake = w.audio, SilentPreview()
            w.audio = fake
            try:
                # The render is at most 0.3 seconds; one cycle request plays it
                # once, then the window restores normal stopped-cycle behavior.
                audition_note(60, 1.)
                assert fake.running and w.note_audition[0] == 'pending'
                fake._cycle_block(128, fake.sample_rate)
                buffer = fake.cycle_samples
                assert buffer is not None and len(buffer) <= round(.345 * fake.sample_rate)
                w.update_cycle(w.output_settings()); assert w.note_audition[0] == 'playing'
                fake._cycle_block(len(buffer) + 512, fake.sample_rate)
                assert fake.cycle_samples is None
                w.update_cycle(w.output_settings()); assert w.note_audition is None
                assert not fake.running
                # Suppression gates leave the buffer alone.
                for field, value in (('exporting', True), ('midi_recorder', object())):
                    old = getattr(w, field); setattr(w, field, value)
                    audition_note(60, 1.); assert w.note_audition is None and not fake.running
                    setattr(w, field, old)
                fake.composer = (w.project, 0.)
                audition_note(60, 1.); assert not fake.running
                fake.composer = None
            finally: w.audio = real_audio
            w.resize(1000, 700); wait()
            panel = w.composer_panel
            assert panel.clip_row_wrapped
            assert panel._preferred_width(panel.transport_row, panel.clip_controls) <= panel.width()
            assert panel.note_tools.isVisible()
            print('Note tools passed: gestures, 11 undo round-trips, menus, scale, audition and limits.')
        finally:
            w.history_timer.stop(); w.timer.stop(); w.force_close = True
            w.shutdown_audio(); w.release_views(); w.close()
