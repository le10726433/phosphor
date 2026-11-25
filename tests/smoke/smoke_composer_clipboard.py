"""Composer copy/paste: notes, clips and automation nodes through the real clipboard and keys."""
import _path  # noqa: F401  (makes the phosphor package importable)
import sys
import os
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt, QPoint, QMimeData
from PySide6.QtGui import QKeySequence

from phosphor.editor import qt_app as q
from phosphor.editor import composer_view as cv
from phosphor.engine import clipboard
from phosphor.engine.composition import Note, COMPOSER, validate_project
from phosphor.engine.lane_access import get_points
from phosphor.storage import project_files


def keys(view, standard):
    """Send a standard shortcut (Ctrl or Cmd as the platform uses) to the view."""
    sequence = QKeySequence(standard)[0]
    QTest.keyClick(view, sequence.key(), sequence.keyboardModifiers()); QTest.qWait(20)


folder = tempfile.TemporaryDirectory(); root = Path(folder.name)
q.configure_graphics(); app = QApplication([])
with patch.object(project_files, 'WORKSPACE_PATH', root / 'workspace.json'), patch.object(q, 'write_workspace', lambda name: project_files.write_workspace(name, root / 'workspace.json')):
    w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=COMPOSER))
    w.resize(1220, 840); w.show(); QTest.qWait(150)
    view = w.composer_panel.timeline
    view.scroll, view.scale, view.top_pitch = 0., 40., 68; QTest.qWait(30)  # Rows 68–58 stay above the velocity lane.
    def point(beats, pitch):
        y = round(view.y_of(pitch) + view.row / 2); assert view.roll_top <= y < view.note_bottom(), 'note-roll click outside the visible roll'
        return QPoint(round(view.x_of(beats) + 3), y)

    # R6: box selection adds touched notes, toggles are selection-only, and every
    # group edit (including multiple drag updates) is restored by one Window.undo.
    baseline = (Note(1., 1., 60), Note(2., .5, 64), Note(6., 1., 67))
    w.set_project(replace(w.project, notes=baseline)); w.commit_history()
    view.setFocus()

    def drag(a, b, modifiers=Qt.KeyboardModifier.NoModifier):
        QTest.mousePress(view, Qt.MouseButton.LeftButton, modifiers, a)
        QTest.mouseMove(view, QPoint((a.x() + b.x()) // 2, (a.y() + b.y()) // 2)); QTest.qWait(20)
        QTest.mouseMove(view, b); QTest.qWait(20)
        QTest.mouseRelease(view, Qt.MouseButton.LeftButton, modifiers, b)
        QTest.qWait(70)

    def undo_to(before):
        assert w.project != before
        w.undo(); QTest.qWait(20)
        assert w.project == before, (w.project.notes, before.notes)

    QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(6., 67))
    before = w.project
    drag(point(.5, 65), point(3., 59), Qt.KeyboardModifier.ShiftModifier)
    assert view.note_selection == {0, 1, 2} and w.project == before, (view.note_selection, w.project.notes)
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, point(6., 67))
    assert view.note_selection == {0, 1}
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, point(6., 67))
    assert view.note_selection == {0, 1, 2}
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(1., 60))
    assert view.note_selection == {0}
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, point(2., 64))
    assert view.note_selection == {0, 1}
    for key, modifiers, pitch_delta, beat_delta in (
            (Qt.Key.Key_Up, Qt.KeyboardModifier.NoModifier, 1, 0.),
            (Qt.Key.Key_Down, Qt.KeyboardModifier.NoModifier, -1, 0.),
            (Qt.Key.Key_Up, Qt.KeyboardModifier.ShiftModifier, 12, 0.),
            (Qt.Key.Key_Down, Qt.KeyboardModifier.ShiftModifier, -12, 0.),
            (Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier, 0, .5),
            (Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier, 0, -.5)):
        view.snap = .5; view.note_selection = {0, 1}
        before = w.project
        QTest.keyClick(view, key, modifiers); QTest.qWait(20)
        assert w.project.notes[:2] == tuple(replace(n, pitch=n.pitch + pitch_delta,
                                                   start=n.start + beat_delta) for n in baseline[:2])
        assert w.project.notes[2] == baseline[2]
        undo_to(before)
    view.snap = 0.; view.note_selection = {0, 1}; before = w.project
    # R13: trigger the real menu action without exec; patch the module name rather
    # than a Shiboken class method. The selected pair changes in one undo step.
    menu = view.build_note_menu(0)
    with patch.object(cv, 'QInputDialog', SimpleNamespace(getDouble=lambda *args: (.25, True))):
        next(a for a in menu.actions() if a.text() == 'Glide…').trigger()
    assert [n.glide for n in w.project.notes] == [.25, .25, 0.]
    keys(view, QKeySequence.StandardKey.Copy)
    assert [n[-1] for n in view.clipboard_payload()['notes']] == [.25, .25]
    undo_to(before)
    menu.deleteLater()
    # A cancelled dialog is not an edit; clicking outside the selection scopes it.
    menu = view.build_note_menu(2)
    assert view.note_selection == {2}
    with patch.object(cv, 'QInputDialog', SimpleNamespace(getDouble=lambda *args: (.5, False))):
        next(a for a in menu.actions() if a.text() == 'Glide…').trigger()
    assert w.project == before
    menu.deleteLater()
    view.note_selection = {0, 1}
    QTest.keyClick(view, Qt.Key.Key_Right); QTest.qWait(20)
    assert w.project.notes[0].start == 1.25
    undo_to(before)
    view.snap = .25; view.note_selection = {0, 1}; before = w.project
    QTest.keyClick(view, Qt.Key.Key_Up); QTest.qWait(20)
    once = w.project
    QTest.keyClick(view, Qt.Key.Key_Right); QTest.qWait(20)
    undo_to(once)  # Rapid consecutive operations must not merge.
    w.undo(); QTest.qWait(20); assert w.project == before
    view.note_selection = {0, 1}
    drag(point(1., 60), point(2., 62))
    assert w.project.notes[:2] == tuple(replace(n, start=n.start + 1., pitch=n.pitch + 2) for n in baseline[:2])
    undo_to(before)
    for key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
        view.note_selection = {0, 1}; before = w.project
        QTest.keyClick(view, key); QTest.qWait(20)
        assert w.project.notes == (baseline[2],)
        undo_to(before)
    keys(view, QKeySequence.StandardKey.SelectAll)
    assert view.note_selection == {0, 1, 2}
    before = w.project
    keys(view, QKeySequence.StandardKey.Copy)
    assert w.project == before
    w.seek_song(float(w.project.seconds(8.)))
    keys(view, QKeySequence.StandardKey.Paste)
    assert [(n.start, n.pitch) for n in w.project.notes[3:]] == [(8., 60), (9., 64), (13., 67)]
    assert view.note_selection == {3, 4, 5}
    undo_to(before)
    view.note_selection = {0, 1}; before = w.project
    keys(view, QKeySequence.StandardKey.Cut)
    assert w.project.notes == (baseline[2],)
    undo_to(before)
    # A plain empty-space drag still draws one note and undoes as one gesture.
    before = w.project
    drag(point(10., 65), point(11., 65))
    assert len(w.project.notes) == 4 and w.project.notes[-1].start == 10.
    undo_to(before)

    # Notes: Shift-click selects two, Ctrl+C, move the playhead, Ctrl+V pastes them there.
    w.set_project(replace(w.project, notes=(Note(1., 1., 60), Note(2., .5, 64), Note(6., 1., 67))))
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(1., 60))
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, point(2., 64))
    assert view.note_selection == {0, 1}, view.note_selection
    view.setFocus(); keys(view, QKeySequence.StandardKey.Copy)
    assert QApplication.clipboard().mimeData().hasFormat(clipboard.MIME)
    w.seek_song(float(w.project.seconds(8.))); QTest.qWait(20)
    keys(view, QKeySequence.StandardKey.Paste)
    added = sorted((n.start, n.length, n.pitch) for n in w.project.notes[3:])
    assert added == [(8., 1., 60), (9., .5, 64)], w.project.notes
    assert view.note_selection == {3, 4}
    # Cut removes the pasted pair; Delete removes a selected note.
    keys(view, QKeySequence.StandardKey.Cut); assert len(w.project.notes) == 3
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point(6., 67))
    QTest.keyClick(view, Qt.Key.Key_Delete); assert [n.pitch for n in w.project.notes] == [60, 64]

    # Clips: copy the scene clip, paste it at bar 5 (beat 16): a second clip with the same scene.
    w.add_object('Cube'); w.automate(('mix', 'drawing_gain'), True); QTest.qWait(30)
    view.set_roll(cv.NOTE_ROLL)
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(round(view.x_of(2.)), cv.RULER + 10))
    assert view.focus_kind == 'clips'
    keys(view, QKeySequence.StandardKey.Copy)
    w.seek_song(float(w.project.seconds(16.))); keys(view, QKeySequence.StandardKey.Paste)
    assert [c.start for c in w.project.clips] == [0., 16.], w.project.clips
    first, second = w.project.clips
    assert second.settings == first.settings and second.lanes == first.lanes and w.clip_index == 1
    assert w.scene.settings.shapes[0].solid == 'Cube'
    # Pasting where a clip already plays moves it after that clip.
    w.seek_song(float(w.project.seconds(4.))); keys(view, QKeySequence.StandardKey.Paste)
    assert [c.start for c in w.project.clips] == [0., 16., 32.], [c.start for c in w.project.clips]
    validate_project(w.project)

    # Nodes: add a node in the Mix lane, select it with the first node, copy, paste at beat 8.
    w.select_clip(0); view.set_roll(cv.TRANSFORM_ROLL); view.lane_scroll = 0; QTest.qWait(40)
    key = ('mix', 'drawing_gain'); rect = view.block_rect(0)
    at = lambda beat, gain: QPoint(round(view.x_of(beat)), round(view.gain_y(rect, gain)))
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at(2., .5))
    QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier, at(0., 1.))
    assert view.node_selection == (key, {0, 1}), view.node_selection
    keys(view, QKeySequence.StandardKey.Copy)
    w.seek_song(float(w.project.seconds(8.))); keys(view, QKeySequence.StandardKey.Paste)
    beats = [b for b, _, _ in get_points(w.project.clips[0], key)]
    assert 8. in beats and 10. in beats and 2. in beats, beats
    keys(view, QKeySequence.StandardKey.Cut)  # Removes the two pasted nodes.
    assert [b for b, _, _ in get_points(w.project.clips[0], key)] == [0., 2., 16.]

    # Foreign clipboard text is refused with a message, nothing changes.
    before = w.project
    mime = QMimeData(); mime.setData(clipboard.MIME, b'{"format": "phosphor-composer-clipboard", "version": 1, "kind": "notes", "notes": [[0, 1, 999, 0.8]]}')
    QApplication.clipboard().setMimeData(mime); view.paste(); QTest.qWait(20)
    assert w.project == before and 'Paste failed' in (w.notice[0] if w.notice else '')
    QApplication.clipboard().setText('plain text'); view.paste(); assert w.project == before

    w.project_dirty = False; w.sandbox_scene.saved = w.sandbox_scene.document()
    w.release_views(); w.close()
print('Composer clipboard passed: notes, clips and automation nodes; keys; foreign clipboard refused.')
