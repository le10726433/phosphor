"""Audio clips A3: the Missing audio files dialog (Locate…, same-folder follow-up, Search folder…,
Skip), missing-clip drawing and Locate file…, the export warning, Collect media, and audio clip
mute and solo.

Every WAV is generated in a temporary folder; no audio fixture is committed. Native or --headless.
"""
import _path  # noqa: F401
import os
import sys
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import shutil
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import soundfile
from PySide6.QtWidgets import QApplication

from phosphor.editor import qt_app as q
from phosphor.engine import audio_clips
from phosphor.engine.composition import COMPOSER, render_song
from phosphor.storage.project_files import read_project

q.configure_graphics()
app = QApplication([])
RATE = 48000


def tone(path, frequency, seconds=.5):
    t = np.arange(round(seconds * RATE)) / RATE
    path.parent.mkdir(parents=True, exist_ok=True)
    soundfile.write(path, np.column_stack((.4*np.sin(2*np.pi*frequency*t), .3*np.cos(2*np.pi*frequency*t))), RATE, subtype='FLOAT')
    return path


def wait(w, condition, seconds=20.):
    end = time.perf_counter() + seconds
    while not condition():
        assert time.perf_counter() < end, 'timed out'
        w.housekeeping(display=False); app.processEvents(); time.sleep(.01)


with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    a, b = tone(root / 'far' / 'a.wav', 220), tone(root / 'far' / 'b.wav', 275)
    c = tone(root / 'far2' / 'c.wav', 330)
    project_path = root / 'song' / 'Song.project.json'; project_path.parent.mkdir()
    with patch.object(q, 'write_workspace', lambda *args: None), patch.object(q.Window, 'remember_file', lambda *args: None):
        w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=COMPOSER))
        try:
            w.resize(1220, 840); w.show(); w.activateWindow(); app.processEvents(); w.timer.stop()
            questions, warnings, answers = [], [], []
            w.ask = lambda title, text: (questions.append(text), answers.pop(0))[1]
            w.warn = lambda title, text: warnings.append(text)
            view = w.composer_panel.timeline
            view.scale, view.scroll = 60., 0.
            w.set_project(replace(w.project, bpm=120.)); w.commit_history()
            assert w.add_audio_clip_files([a, b, c], 0.)
            assert [x.name for x in w.project.audio_clips] == ['a.wav', 'b.wav', 'c.wav']
            w.project_path = project_path; assert w.save_project()

            # Move the files away: a and b to one folder, c deeper in another.
            (root / 'found').mkdir(); shutil.move(str(a), root / 'found' / 'a.wav'); shutil.move(str(b), root / 'found' / 'b.wav')
            (root / 'found2' / 'sub').mkdir(parents=True); shutil.move(str(c), root / 'found2' / 'sub' / 'c.wav')
            audio_clips.clear_decoded()

            seen = {}
            def drive(dialog):
                """Locate a.wav (b.wav is offered from the same folder), then Search folder for c.wav."""
                dialog.show(); app.processEvents()
                seen['rows'] = dialog.files.topLevelItemCount()
                assert not dialog.grab().isNull()
                w.pick_audio_file = lambda name, folder='': str(root / 'found' / name)
                answers[:] = [True]  # Relink the other file in the same folder.
                dialog.locate_button.click(); app.processEvents()
                seen['after_locate'] = dialog.files.topLevelItemCount()
                w.pick_folder = lambda title: str(root / 'found2')
                dialog.search_button.click(); app.processEvents()
                seen['after_search'] = dialog.files.topLevelItemCount()
                return dialog.result()
            w.run_dialog = drive
            w.open_project_path(str(project_path))
            assert seen == {'rows': 3, 'after_locate': 1, 'after_search': 0, }, seen
            assert len(questions) == 1 and 'other missing audio file is in found' in questions[0], questions
            assert not any(x.missing for x in w.project.audio_clips)
            assert [Path(x.path).parent.name for x in w.project.audio_clips] == ['found', 'found', 'sub']
            assert w.history.undo_label == 'Relink audio file', w.history.undo_label  # Search folder: the last step.
            assert all(audio_clips.waveform(x.path, x.fingerprint) is not None for x in w.project.audio_clips)  # Decoded by the worker.
            w.undo(); assert w.project.audio_clips[2].missing
            w.redo(); assert not w.project.audio_clips[2].missing
            assert w.save_project()

            # Skip leaves a clip missing: hatched, Locate file… in its menu, silent, and the export warns.
            shutil.rmtree(root / 'found2'); audio_clips.clear_decoded()
            w.run_dialog = lambda dialog: (dialog.show(), dialog.skip_button.click(), dialog.result())[2]
            w.open_project_path(str(project_path))
            clip = w.project.audio_clips[2]
            assert clip.missing and view.audio_badge(2) == 'Missing'
            assert not view.grab().isNull()
            assert view.audio_tooltip(view.x_of(2.5), view.audio_top + 20)[0].startswith('c.wav is missing'), view.audio_tooltip(view.x_of(2.5), view.audio_top + 20)
            target = root / 'export.wav'
            w.export(path=str(target), format='WAV', depth=32, rate=RATE, range='whole')
            wait(w, lambda: target.exists() and not w.exporting)
            assert any('1 audio clip is missing' in text for text in warnings), warnings
            menu = view.build_audio_menu(2)
            assert menu.locate is not None and view.build_audio_menu(0).locate is None
            # A different file asks first: No keeps the clip missing, Yes uses it.
            take2 = tone(root / 'take2.wav', 440, seconds=.4)
            w.pick_audio_file = lambda name, folder='': str(take2)
            answers[:] = [False]; menu.locate.trigger(); app.processEvents()
            assert w.project.audio_clips[2].missing and 'Use it anyway?' in questions[-1]
            answers[:] = [True]; view.build_audio_menu(2).locate.trigger(); app.processEvents()
            clip = w.project.audio_clips[2]
            assert not clip.missing and clip.name == 'take2.wav' and abs(clip.duration - .4) < 1e-9, clip
            assert w.history.undo_label == 'Relink audio file'
            assert w.missing_audio_action.isVisible()
            w.show_missing_audio()  # Nothing missing: a status message, no dialog.

            # Mute and solo from the clip menu.
            view.build_audio_menu(1).mute.trigger(); w.commit_history()
            assert w.project.audio_clips[1].muted and w.history.undo_label == 'Mute audio clip'
            assert view.audio_silenced(1) and not view.audio_silenced(0)
            view.build_audio_menu(0).solo.trigger(); app.processEvents()
            assert w.audio_solos == {0} and view.audio_solos == {0} and view.audio_badge(0) == 'S'
            assert view.build_audio_menu(0).solo.isChecked() and view.audio_silenced(2)
            soloed = w.output_project()
            assert all(x.muted for x in soloed.clips) and [x.muted for x in soloed.audio_clips] == [False, True, True]
            assert not w.project.audio_clips[0].muted and not any(x.muted for x in w.project.clips)  # Solo is never saved.
            assert not view.grab().isNull()
            warnings.clear(); target = root / 'solo.wav'
            w.export(path=str(target), format='WAV', depth=32, rate=RATE, range='whole')
            wait(w, lambda: target.exists() and not w.exporting)
            assert any('soloed' in text for text in warnings), warnings
            exported, _ = soundfile.read(target, dtype='float32', always_2d=True)
            machine = replace(w.output_settings(), sample_rate=RATE, motion_time_offset=0.)
            expected = render_song(np.arange(len(exported)) / RATE, soloed, machine).astype('float32')
            assert np.array_equal(exported, expected)  # Export is what playback renders.
            assert np.any(exported) and not np.any(exported[RATE:])  # a.wav alone (0–0.5 s); b, c and the scene are silent.
            view.build_audio_menu(0).solo.trigger(); assert not w.audio_solos and w.output_project() is w.project

            # Collect media copies the files beside the project, relinks them and saves.
            assert w.collect_media()
            media = project_path.parent / 'Song media'
            assert sorted(p.name for p in media.iterdir()) == ['a.wav', 'b.wav', 'take2.wav'], list(media.iterdir())
            assert all(Path(x.path).parent == media.resolve() for x in w.project.audio_clips)
            assert not w.project_dirty
            shutil.rmtree(root / 'found'); take2.unlink()
            reopened = read_project(project_path)
            assert not any(x.missing for x in reopened.audio_clips) and reopened.audio_clips[1].muted
            assert w.history.undo_label == 'Relink audio files', w.history.undo_label
            assert not view.grab().isNull()
        finally:
            w.history_timer.stop(); w.timer.stop(); w.force_close = True
            w.shutdown_audio(); w.release_views(); w.close()
print('Missing audio passed: dialog Locate/same folder/Search folder/Skip, Locate file…, export warnings, mute, solo and Collect media.')
