"""The browser panel (AW4): the bar button shows and hides it left of the scope; Files lists a
folder (hidden files skipped), reads a file's header, stars a favourite, searches below the folder,
drags file URLs that Composer's drop accepts, adds files at the playhead (one Undo step) and to a
new track, and lists them under Recent audio; the preview plays through a simulated output at its
own level and Play replaces it; Equations adds an Equation object (one Undo step); Transforms
inserts a saved step; Scenes and Models list the files in the folder.

Every file is generated in a temporary folder; no fixture is committed. Native or --headless.
"""
import _path  # noqa: F401
import os
import sys
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import soundfile
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from phosphor.audio.simulated_output import SimulatedDevice
from phosphor.editor import qt_app as q
from phosphor.engine.function_plot import PRESETS
from phosphor.engine.transforms import ROTATE
from phosphor.engine.composition import COMPOSER, SANDBOX
from phosphor.storage import browser_places, recent_files

q.configure_graphics()
app = QApplication([])
RATE = 48000


def wait(condition, seconds=5., message='timed out'):
    end = time.perf_counter() + seconds
    while not condition():
        if time.perf_counter() > end: raise AssertionError(message)
        app.processEvents(); time.sleep(.01)


with tempfile.TemporaryDirectory() as folder:
    root = Path(folder) / 'Samples'; (root / 'sub').mkdir(parents=True)
    t = np.arange(RATE) / RATE
    tone = .4 * np.sin(2 * np.pi * 220 * t)
    soundfile.write(root / 'tone.wav', np.column_stack((tone, np.roll(tone, 40))), RATE, subtype='FLOAT')
    soundfile.write(root / 'mono.wav', tone, RATE)
    soundfile.write(root / 'sub' / 'deep kick.wav', tone[:RATE // 2], RATE)
    soundfile.write(root / '.hidden.wav', tone, RATE)
    (root / 'notes.txt').write_text('not audio', encoding='utf-8')
    (root / 'cube.obj').write_text('v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n', encoding='utf-8')
    with (patch.object(q, 'write_workspace', lambda *args: None),
          patch.object(q, 'PREFERENCES_PATH', Path(folder) / 'preferences.json')):
        w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=COMPOSER))
        try:
            w.remember_recent = True; w.recent_path = Path(folder) / 'recent-files.json'
            w.browser_places_path = Path(folder) / 'browser.json'
            w.audio.device = SimulatedDevice(RATE)
            w.transform_panel.set_presets_path(Path(folder) / 'transform-presets.json')
            w.resize(1220, 840); w.show(); w.activateWindow(); app.processEvents()
            browser, button = w.browser, w.bar_buttons[q.BROWSER]

            # Hidden by default; the bar button shows it left of the scope at the default width.
            assert not w.left_panel.isVisible() and not button.isChecked() and button.text() == 'Library'
            assert w.left_panel.stack.currentWidget() is browser and w.preferences['left_page'] == 'browser'
            button.click(); app.processEvents()
            assert browser.isVisible() and button.isChecked() and w.preferences['browser_shown']
            assert w.browser_split.indexOf(w.left_panel) == 0 and w.browser_split.indexOf(w.splitter) == 1
            assert abs(w.browser_split.sizes()[0] - w.preferences['browser_width']) <= 2, w.browser_split.sizes()
            assert w.splitter.count() == 2  # The side panel's splitter is unchanged.

            # Files: subfolders, then audio files; hidden and other files are not listed.
            browser.open_folder(root)
            wait(lambda: browser.list.count() > 0, message='folder listing')
            names = [browser.list.item(i).text() for i in range(browser.list.count())]
            assert names == ['sub', 'mono.wav', 'tone.wav'], names
            assert w.preferences['browser_folder'] == str(root)
            item = browser.list.findItems('tone.wav', q.Qt.MatchFlag.MatchExactly)[0]
            browser.list.setCurrentItem(item)
            wait(lambda: browser.info.text() not in ('', '…'), message='file info')
            assert browser.info.text() == '1.0 s · 48 kHz · stereo', browser.info.text()

            # A favourite folder appears in Places and survives a rebuild.
            browser.star.click(); app.processEvents()
            assert browser_places.read_favourites(w.browser_places_path) == [root.resolve()] or \
                [str(p) for p in browser_places.read_favourites(w.browser_places_path)] == [str(root)]
            assert browser.star.isChecked() and browser.star.text() == '★'
            assert any(browser.places.itemText(i) == '★ Samples' for i in range(browser.places.count()))

            # Search below the folder (all words, any case); clearing it lists the folder again.
            browser.filter.setText('KICK deep'); browser.search_timer.timeout.emit()
            wait(lambda: browser.list.count() > 0, message='search')
            found = browser.list.item(0)
            assert Path(found.data(q.Qt.ItemDataRole.UserRole)).name == 'deep kick.wav' and browser.message.text() == '1 match'
            browser.filter.clear(); browser.search_timer.timeout.emit()
            wait(lambda: browser.list.count() == 3, message='relist')

            # Dragged items carry file URLs that the Composer timeline accepts.
            view = w.composer_panel.timeline
            item = browser.list.findItems('tone.wav', q.Qt.MatchFlag.MatchExactly)[0]
            mime = browser.list.mimeData([item, browser.list.findItems('sub', q.Qt.MatchFlag.MatchExactly)[0]])
            assert [Path(p).name for p in view.audio_paths(mime)] == ['tone.wav']

            # Add at the playhead on the selected track: one Undo step; Recent audio lists it.
            w.commit_history(); size = w.history._index
            browser.list.clearSelection(); browser.list.setCurrentItem(item); item.setSelected(True)
            browser.add.click(); app.processEvents(); w.commit_history()
            assert len(w.project.audio_clips) == 1 and w.project.audio_clips[0].track == 0
            assert w.history._index == size + 1
            menu = browser.build_file_menu(item)
            new_track = next(a for a in menu.actions() if a.text() == 'Add to a new track')
            new_track.trigger(); app.processEvents(); w.commit_history()
            assert len(w.project.audio_tracks) == 2 and w.project.audio_clips[-1].track == 1, w.project.audio_tracks
            recent = recent_files.read_recent('audio', w.recent_path)
            assert [p.name for p in recent] == ['tone.wav'], recent
            # Recent audio is read on the worker; a folder listing still pending never replaces it.
            browser.open_folder(root); browser.show_recent()
            wait(lambda: browser.list.count() == 1, message='recent audio')
            for _ in range(20): app.processEvents(); time.sleep(.01)
            assert [browser.list.item(i).text() for i in range(browser.list.count())] == ['tone.wav']
            browser.open_folder(root); wait(lambda: browser.list.count() == 3, message='relist')

            # Preview: plays once through the output at the panel's level; Play replaces it.
            item = browser.list.findItems('mono.wav', q.Qt.MatchFlag.MatchExactly)[0]
            browser.list.setCurrentItem(item)
            browser.level.setValue(40)
            browser.play.click()
            wait(lambda: w.audio.preview_running, message='preview start')
            assert browser.play.text() == '■' and abs(w.audio.preview_level - .4) < 1e-9
            for page in ('effects', 'browser'):
                w.left_panel.buttons[page].click()
                assert w.preferences['left_page'] == page and w.audio.preview_running
            assert w.play_button.text() == 'Play'  # A preview isn't playback.
            browser.level.setValue(55); assert abs(w.audio.preview_level - .55) < 1e-9
            w.play(); app.processEvents()
            assert not w.audio.preview and w.audio.running and w.audio.composer is not None
            assert w.preview_blocked() == 'Stop playback to preview.'
            w.stop_song(); app.processEvents()
            wait(lambda: browser.play.text() == '▶', message='preview button resets')
            # Space in the list previews (it isn't the window's Play shortcut); Space again stops.
            browser.list.setFocus(); app.processEvents()
            QTest.keyClick(browser.list, Qt.Key.Key_Space); wait(lambda: w.audio.preview_running, message='Space previews')
            assert w.audio.composer is None, 'Space started the song'
            QTest.keyClick(browser.list, Qt.Key.Key_Space); app.processEvents()
            assert not w.audio.running and browser.play.text() == '▶'
            wait(lambda: not browser.poll.isActive(), message='the poll timer stops')
            # An unreadable file says so.
            (root / 'broken.wav').write_bytes(b'RIFF0000WAVEjunk')
            browser.start_preview(root / 'broken.wav')
            wait(lambda: browser.message.text().startswith('Could not preview broken.wav'), message='preview error')
            assert not w.audio.running and browser.play.text() == '▶'

            # Models and Scenes list the files in the Files folder.
            browser.tabs.setCurrentIndex(4)
            wait(lambda: 'cube.obj' in browser.models.names(), message='models tab')

            # Equations: Sandbox, double-click a built-in: one new Equation object, one Undo step.
            w.set_workspace(SANDBOX); app.processEvents()
            browser.tabs.setCurrentIndex(2); app.processEvents()
            assert 'Helix' in browser.equations.names()
            count = len(w.scene.settings.shapes)
            browser.equations.run(browser.equations.item_named('Helix')); app.processEvents()
            assert len(w.scene.settings.shapes) == count + 1 and w.scene.shape.solid == q.PARAMETRIC_OBJECT
            assert w.scene.shape.plot == PRESETS['Helix'], w.scene.shape.plot
            w.undo(); app.processEvents()
            assert len(w.scene.settings.shapes) == count

            # Transforms: a saved step is listed and inserts into the selected object.
            w.add_object('Cube'); app.processEvents()
            panel = w.transform_panel
            panel.add(ROTATE); app.processEvents()
            assert panel.save_step(0, name='Tilt')
            browser.tabs.setCurrentIndex(3); app.processEvents()
            assert browser.transforms.names() == ['Tilt'], browser.transforms.names()
            before = len(w.scene.shape.transforms)
            browser.transforms.run(browser.transforms.item_named('Tilt')); app.processEvents()
            assert len(w.scene.shape.transforms) == before + 1

            # Hide from Effects during a preview, then reopen on the remembered page.
            browser.start_preview(root / 'tone.wav')
            wait(lambda: w.audio.preview_running, message='preview before hiding')
            w.left_panel.buttons['effects'].click()
            button.click(); app.processEvents()
            assert not w.left_panel.isVisible() and not w.preferences['browser_shown'] and not w.audio.preview_running
            button.click(); app.processEvents()
            assert w.effects_panel.isVisible() and w.preferences['left_page'] == 'effects'
            w.save_preferences()
            saved = q.read_preferences(q.PREFERENCES_PATH)
            assert saved['left_page'] == 'effects' and saved['browser_shown']
        finally:
            w.history_timer.stop(); w.timer.stop(); w.force_close = True
            w.shutdown_audio(); w.release_views(); w.close()
print('Browser panel passed: show/hide, listing, info, favourite, search, drag, add, recent, preview, models, equations, transforms.')
