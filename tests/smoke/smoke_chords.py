"""Native Composer chords: action history, full-note import and cycle rendering."""
import _path  # noqa: F401
import os
import sys
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from phosphor.editor import qt_app as q
from phosphor.editor.composer_view import NOTE_ROLL, TRANSFORM_ROLL, GUTTER
from phosphor.engine.composition import COMPOSER, SANDBOX, Note, song_cycle
from tests.example_midi import example_midi

q.configure_graphics()
app = QApplication([])
with patch.object(q, 'write_workspace', lambda *args: None):
    w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=COMPOSER))
    try:
        w.show(); app.processEvents(); w.timer.stop()
        assert w.project.chords and w.chords_action.isChecked()
        assert w.chords_action.isVisible() and w.chords_action.isEnabled()
        assert any(w.chords_action in action.menu().actions() for action in w.menuBar().actions()
                   if action.text() == 'Transport')
        before = w.project
        w.composer_panel.timeline.setFocus()
        w.chords_action.trigger()
        assert not w.project.chords and not w.chords_action.isChecked()
        assert w.history.undo_label == 'Chords off'
        w.undo(); assert w.project == before and w.chords_action.isChecked()
        w.redo(); assert not w.project.chords and not w.chords_action.isChecked()
        w.undo()
        score = example_midi()
        assert w.import_midi_notes(score, 'replace', False, chords=True)
        assert any(b.start < a.end for a, b in zip(w.project.notes, w.project.notes[1:]))
        p = replace(w.project, notes=tuple(Note(0., 8., pitch) for pitch in (60, 64, 67)))
        w.set_project(p)
        samples, period, reason = song_cycle(w.project, .5, w.output_settings())
        assert reason == '' and len(samples) > 0 and period > 0
        timeline = w.composer_panel.timeline
        # Separate groups exercise triads, stacked notes, a selection and voice stealing.
        notes = (tuple(Note(0., 2., pitch) for pitch in (60, 64, 67))
                 + (Note(3., 2., 60), Note(4., 2., 60))
                 + tuple(Note(6., 2., pitch) for pitch in (57, 60, 64, 67))
                 + tuple(Note(9., 2., pitch) for pitch in range(60, 69)))
        w.set_project(replace(w.project, notes=notes, chords=True))
        timeline.set_roll(NOTE_ROLL); timeline.set_playhead(.5)
        timeline.scale, timeline.scroll, timeline.top_pitch = 40., 0., 76
        timeline.resize(800, 344)  # 300 before the 44 px Audio track.
        assert timeline.playhead_chord() == 'C'
        timeline.note_selection = set(range(5, 9))
        assert timeline.selection_chord() == 'Am7'
        assert timeline.dropped_voices() == ((9, 9.),)
        assert timeline.note_overlaps(timeline.visible_notes()) == ((60, 4., 5.),)
        assert timeline.roll_chords.isChecked() and not timeline.roll_chords.isHidden()
        assert timeline.roll_chords.objectName() == 'roll_chords'
        assert timeline.roll_chords.width() < GUTTER
        assert timeline.dropped_voice_tooltip(timeline.x_of(9.5), timeline.y_of(60) + 3) == (
            'Voice dropped: more than 8 notes sound here')
        assert not timeline.grab().isNull()
        before = w.project
        timeline.roll_chords.click()
        assert not w.project.chords and not w.chords_action.isChecked()
        assert not timeline.roll_chords.isChecked() and timeline.dropped_voices() == ()
        assert timeline.playhead_chord() == '' and timeline.selection_chord() == 'Am7'
        assert timeline.dropped_voice_tooltip(timeline.x_of(9.5), timeline.y_of(60) + 3) == ''
        assert w.history.undo_label == 'Chords off'
        assert not timeline.grab().isNull()
        w.undo()
        assert w.project == before and w.chords_action.isChecked() and timeline.roll_chords.isChecked()
        timeline.set_roll(TRANSFORM_ROLL)
        assert timeline.roll_chords.isHidden()
        assert not timeline.grab().isNull()
        timeline.set_roll(NOTE_ROLL)
        assert not timeline.roll_chords.isHidden()
        w.set_workspace(SANDBOX)
        assert not w.chords_action.isVisible() and not w.chords_action.isEnabled()
        w.set_workspace(COMPOSER)
        assert w.chords_action.isChecked()
    finally:
        w.history_timer.stop(); w.timer.stop(); w.force_close = True
        w.shutdown_audio(); w.release_views(); w.close()
print('Chords passed: defaults, Transport/roll switches, Undo/Redo, all-note import, chord cycle and roll feedback.')
