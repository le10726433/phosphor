"""Composer audio-track effects (AW6): the Library Effects page (add, parameters, bypass, reorder,
remove; one Undo step each), the header's fx badge, an effect parameter's lane under its track
(add a node, Undo), save and reopen, and playback equal to export with effects on.

The WAV is generated in a temporary folder; no audio fixture is committed. Native or --headless.
"""
import _path  # noqa: F401
import os
import sys
if '--headless' in sys.argv: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import soundfile
from PySide6.QtCore import Qt, QPoint, QModelIndex
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from phosphor.editor import qt_app as q
from phosphor.engine import audio_clips
from phosphor.engine.audio_clips import AudioTrack
from phosphor.engine.composition import COMPOSER, export_song, render_song
from phosphor.engine.track_chain import ChainState
from phosphor.engine.track_effects import effect_lane, effect_value, new_effect
from phosphor.storage.project_files import read_project, save_project

q.configure_graphics()
app = QApplication([])
RATE = 48000

with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    t = np.arange(RATE) / RATE
    path = root / 'tone.wav'
    tone = .4 * np.sin(2 * np.pi * 220 * t) + .2 * np.sin(2 * np.pi * 3520 * t)
    soundfile.write(path, np.column_stack((tone, np.roll(tone, 40))), RATE, subtype='FLOAT')
    with patch.object(q, 'write_workspace', lambda *args: None), patch.object(q.Window, 'remember_file', lambda *args: None):
        w = q.Window(SimpleNamespace(scene=None, ready_file=None, workspace=COMPOSER))
        try:
            w.resize(1220, 840); w.show(); w.activateWindow(); app.processEvents(); w.timer.stop()
            total = sum(w.vsplit.sizes()); w.vsplit.setSizes([total - 560, 560]); app.processEvents()
            clip = audio_clips.audio_clip_from_file(path, 0.)
            w.set_project(replace(w.project, bpm=120., audio_tracks=(AudioTrack('Lead'),), audio_clips=(clip,)))
            w.commit_history()
            panel = w.composer_panel; view = panel.timeline
            view.roll_hear_notes = False
            view.set_roll_option('roll_audio_gain_lane', False)
            view.scale, view.scroll = 40., 0.
            app.processEvents()

            # The first open in a fresh window lays out and shows the chain (it once stayed blank
            # until a style change), sized to its rows with the parameters below it.
            w.set_project(replace(w.project, audio_tracks=(AudioTrack('Lead', effects=(new_effect('filter'), new_effect('delay'))),)))
            w.commit_history(); app.processEvents()
            fx = w.open_track_effects(0); QTest.qWait(50)
            assert fx.chain.count() == 2
            for k in range(2):
                rect = fx.chain.visualItemRect(fx.chain.item(k))
                assert not rect.isEmpty() and fx.chain.viewport().rect().contains(rect.center()), (k, rect)
                assert fx.chain.indexAt(rect.center()).row() == k
            for count in (3, 6, 7, 8):
                chain = tuple(new_effect('delay') for _ in range(count))
                w.set_project(replace(w.project, audio_tracks=(AudioTrack('Lead', effects=chain),)))
                app.processEvents()
                fx.chain.scrollToTop()
                viewport = fx.chain.viewport().rect()
                for k in range(min(count, 6)):
                    assert viewport.contains(fx.chain.visualItemRect(fx.chain.item(k))), (count, k, viewport)
                if count > 6:
                    assert not viewport.contains(fx.chain.visualItemRect(fx.chain.item(6)))
                    for k in range(6, count):
                        fx.chain.scrollToItem(fx.chain.item(k))
                        assert viewport.contains(fx.chain.visualItemRect(fx.chain.item(k))), (count, k)
            assert fx.form_host.y() > fx.add.y() > fx.chain.y()  # The buttons, then the parameters, below the list.
            w.toggle_browser(False)
            w.set_project(replace(w.project, audio_tracks=(AudioTrack('Lead'),))); w.commit_history(); app.processEvents()

            def steps(action, label='Change audio track effects'):
                w.commit_history(); size = w.history._index
                action(); w.commit_history(); app.processEvents()
                assert w.history._index == size + 1, ('one step', w.history.undo_label)
                if label: assert w.history.undo_label == label, (w.history.undo_label, label)

            def effects(): return w.project.audio_tracks[0].effects

            # The track menu opens the hidden Library on Effects (no badge without effects).
            menu = view.build_track_menu(0)
            assert menu.effects.text() == 'Effects…'
            assert view.track_fx_rect(view.track_row(0)) is None
            menu.effects.trigger(); app.processEvents()
            fx = w.effects_panel
            assert w.left_panel.isVisible() and w.left_panel.stack.currentWidget() is fx
            assert fx.isVisible() and fx.track == 0 and fx.chain.count() == 0 and not fx.remove.isEnabled()

            # Add a filter and a delay (one step each); the first is selected after adding.
            steps(lambda: fx.add_actions['filter'].trigger())
            steps(lambda: fx.add_actions['delay'].trigger())
            assert [e.kind for e in effects()] == ['filter', 'delay'] and fx.chain.count() == 2
            assert fx.chain.currentRow() == 1 and fx.shown_kind == 'delay'
            assert view.build_track_menu(0).effects.text() == 'Effects… (2)'
            badge = view.track_fx_rect(view.track_row(0))
            assert badge is not None and not badge.intersects(view.name_rect(view.track_row(0)))
            assert view.header_tooltip(view.track_row(0), badge.center().x(), badge.center().y()).startswith('Effects: Filter, Delay')
            w.left_panel.buttons['browser'].click(); w.toggle_browser(False)
            QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=badge.center().toPoint())
            app.processEvents()
            assert w.left_panel.isVisible() and w.left_panel.stack.currentWidget() is fx and fx.track == 0
            fx.chain.setCurrentRow(1)

            # Parameters: the spin box types a value; a choice box sets a mode.
            size = w.history._index
            spin = fx.rows['time'].spin
            spin.setFocus(); spin.selectAll()
            QTest.keyClicks(spin, '125')
            QTest.mouseClick(fx.bypass, Qt.MouseButton.LeftButton)
            assert effect_value(effects()[1], 'time') == 125.
            assert w.project.audio_tracks[0].effects_bypass and w.history._index == size + 2
            w.undo()
            assert not w.project.audio_tracks[0].effects_bypass and effect_value(effects()[1], 'time') == 125.
            w.undo()
            assert effect_value(effects()[1], 'time') != 125.
            steps(lambda: fx.choices['mode'].setCurrentIndex(1))
            assert effect_value(effects()[1], 'mode') == 'ping-pong'
            fx.chain.setCurrentRow(0); app.processEvents()
            assert fx.shown_kind == 'filter' and set(fx.rows) == {'cutoff', 'resonance'}
            steps(lambda: fx.rows['cutoff'].spin.setValue(500.))
            assert abs(effect_value(effects()[0], 'cutoff') - 500.) < 1e-9

            # Bypass one effect (its checkbox) and the whole chain.
            steps(lambda: (fx.chain.item(0).setCheckState(Qt.CheckState.Unchecked), app.processEvents()))
            assert effects()[0].bypass and not effects()[1].bypass
            steps(lambda: fx.bypass.setChecked(True))
            assert w.project.audio_tracks[0].effects_bypass
            w.undo(); w.undo(); app.processEvents()
            assert not effects()[0].bypass and not w.project.audio_tracks[0].effects_bypass
            assert fx.chain.item(0).checkState() == Qt.CheckState.Checked and not fx.bypass.isChecked()

            # Reorder (Down), then Undo puts it back.
            fx.chain.setCurrentRow(0); app.processEvents()
            steps(lambda: fx.down.click())
            assert [e.kind for e in effects()] == ['delay', 'filter'] and fx.chain.currentRow() == 1
            w.undo(); app.processEvents()
            assert [e.kind for e in effects()] == ['filter', 'delay']

            # An effect parameter's lane: Lane shows it under this track; click adds a node (one step).
            fx.chain.setCurrentRow(0); app.processEvents()
            fx.rows['cutoff'].lane.click(); app.processEvents()
            assert view.roll_audio_gain_lane and view.lane_for(0) == ('fx', 0, 'cutoff') and fx.rows['cutoff'].lane.isChecked()
            choices = view.build_track_menu(0).lane_menu
            assert choices.effect_actions[(0, 'cutoff')].isChecked()
            choices.effect_actions[(0, 'resonance')].trigger()
            assert fx.rows['resonance'].lane.isChecked() and not fx.rows['cutoff'].lane.isChecked()
            choices.effect_actions[(0, 'cutoff')].trigger()
            lane = view.gain_lane_rect(0)
            # The Library's minimum height leaves the timeline short; Lane scrolled the tracks to show it.
            assert view.audio_top <= lane.top() and lane.bottom() <= view.audio_end, (lane, view.audio_end)
            at =QPoint(round(view.x_of(1.)), round(lane.top() + lane.height() * .25))
            steps(lambda: QTest.mouseClick(view, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, at), None)
            points = effect_lane(effects()[0], 'cutoff')
            assert len(points) == 1 and abs(points[0][0] - .5) < 1e-6, points
            app.processEvents()
            assert not fx.rows['cutoff'].spin.isEnabled() and fx.rows['cutoff'].lane.text() == 'Lane ~'
            assert not view.grab().isNull()
            view.set_audio_lane('gain'); assert view.lane_for(0) == 'gain'

            # Review fixes. Up/Down follow the selection; two slider drags are two Undo steps (no manual
            # commits between them); an effect lane follows its effect when an earlier one is removed;
            # the page follows its track by name when tracks are reordered.
            fx.chain.setCurrentRow(1); app.processEvents(); assert not fx.down.isEnabled()
            fx.chain.setCurrentRow(0); app.processEvents(); assert fx.down.isEnabled() and not fx.up.isEnabled()
            w.commit_history(); size = w.history._index
            for value in (300, 600):
                row = fx.rows['resonance']; row.slider.setSliderDown(True)
                row.slider.setValue(value - 50); app.processEvents()
                start = w.history._index
                row.slider.setValue(value); app.processEvents()
                assert w.history._index == start
                row.slider.setSliderDown(False); app.processEvents()
            assert w.history._index == size + 2, ('two drags, two steps', w.history._index - size)
            w.undo(); w.undo(); app.processEvents()
            steps(lambda: fx.add_actions['saturate'].trigger())  # Saturate goes after the filter: index 1.
            fx.chain.setCurrentRow(2); app.processEvents(); assert fx.shown_kind == 'delay'
            fx.rows['time'].lane.click(); app.processEvents(); assert view.lane_for(0) == ('fx', 2, 'time')
            fx.chain.setCurrentRow(0); app.processEvents()
            steps(lambda: fx.remove.click())
            assert [e.kind for e in effects()] == ['saturate', 'delay'] and view.lane_for(0) == ('fx', 1, 'time')
            w.undo(); w.undo(); app.processEvents()
            assert [e.kind for e in effects()] == ['filter', 'delay']
            view.add_track(); app.processEvents(); w.commit_history()
            fx.track_box.setCurrentIndex(1)
            assert fx.track_data() is w.project.audio_tracks[1] and fx.chain.count() == 0
            view.scroll_tracks(-view.track_scroll)  # With the Library open the track rows scroll; show track 0's header.
            QTest.mouseClick(view, Qt.MouseButton.LeftButton, pos=view.name_rect(view.track_row(0)).center().toPoint())
            assert fx.track_box.currentIndex() == 0 and fx.chain.count() == 2
            view.move_tracks([0], 1); app.processEvents(); w.commit_history(); app.processEvents()
            assert w.project.audio_tracks[1].name == 'Lead' and fx.track_data() is w.project.audio_tracks[1]
            assert fx.track_box.currentText() == 'Lead' and fx.chain.count() == 2 and view.lane_for(1) == 'gain'
            w.undo(); w.undo(); app.processEvents()
            assert len(w.project.audio_tracks) == 1 and fx.track == 0
            view.set_audio_lane('gain')

            # Remove the delay (one step); Undo brings it back.
            fx.chain.setCurrentRow(1); app.processEvents()
            steps(lambda: fx.remove.click())
            assert [e.kind for e in effects()] == ['filter']
            w.undo(); app.processEvents(); assert len(effects()) == 2 and fx.chain.count() == 2

            # Save and reopen keeps the chain; playback blocks with one ChainState equal the export.
            song = root / 'song.project.json'
            save_project(song, w.project)
            reopened = read_project(song)
            assert reopened.audio_tracks[0] == w.project.audio_tracks[0], (reopened.audio_tracks[0], w.project.audio_tracks[0])
            settings = replace(w.output_settings(), sample_rate=RATE)
            seconds = 1.2
            out = root / 'song.wav'
            export_song(out, reopened, settings, 0., float(reopened.beats(seconds)), format='WAV', depth=32)
            exported, _ = soundfile.read(out, dtype='float64')
            state = ChainState(); times = np.arange(round(seconds * RATE)) / RATE
            played = np.concatenate([render_song(times[i:i+480], reopened, settings, chain_state=state) for i in range(0, len(times), 480)])
            assert np.allclose(exported, np.clip(played, -1, 1), atol=1e-6), np.abs(exported - np.clip(played, -1, 1)).max()
            dry = render_song(times, replace(reopened, audio_tracks=(AudioTrack('Lead'),)), settings)
            assert not np.allclose(played, dry), 'effects change the sound'
            delay = new_effect('delay')
            lead = AudioTrack('Lead', effects=(delay, new_effect('filter')))
            bass = AudioTrack('Bass', effects=(delay, new_effect('compressor')))
            w.load_project(replace(w.project, audio_tracks=(lead, bass)), None)
            slider = fx.rows['time'].slider
            slider.setSliderDown(True); slider.setValue(300)
            first = effect_value(w.project.audio_tracks[0].effects[0], 'time')
            assert first != effect_value(delay, 'time')
            fx.track_box.setCurrentIndex(1)
            slider.setValue(800); slider.setSliderDown(False)
            assert w.history._index == 1
            assert w.project.audio_tracks[1] == bass
            assert effect_value(w.project.audio_tracks[0].effects[0], 'time') == first
            w.undo()
            assert w.project.audio_tracks == (lead, bass)

            # Move through the list's InternalMove model path and switch before the queued rebuild.
            fx.set_track(0)
            model = fx.chain.model()
            assert model.moveRow(QModelIndex(), 0, QModelIndex(), 2)
            assert [effect.kind for effect in effects()] == ['filter', 'delay']
            fx.track_box.setCurrentIndex(1); app.processEvents()
            assert fx.chain.currentRow() == 0 and fx.shown_kind == 'delay'

            other = replace(bass, name='Lead')
            w.load_project(replace(w.project, audio_tracks=(lead, other)), None)
            fx.track_box.setCurrentIndex(1)
            view.move_tracks([1], -1); app.processEvents()
            assert fx.track == 0 and fx.track_data() is other
            fx.bypass.click()
            assert w.project.audio_tracks[0].effects_bypass and w.project.audio_tracks[1] is lead
            w.undo(); w.undo()
            assert fx.track == 1 and fx.track_data() is other
            fx.bypass.click()
            assert w.project.audio_tracks[1].effects_bypass
            w.undo()
            assert fx.track == 0 and fx.track_data() is lead  # Both names match the replaced track.

            tracks = tuple(AudioTrack(f'New {i}', effects=(delay,), collapsed=True) for i in range(32))
            last = len(tracks) - 1
            w.load_project(replace(w.project, audio_tracks=tracks), None)
            assert fx.track_box.currentIndex() == 0 and fx.track_data() is tracks[0]
            fx.set_track(last); view.scroll_tracks(-view.track_scroll)
            assert view.track_row(last).top >= view.audio_end
            fx.rows['time'].lane.click()
            lane = view.gain_lane_rect(last)
            assert not w.project.audio_tracks[last].collapsed and view.audio_top <= lane.top() and lane.bottom() <= view.audio_end
            app.processEvents()
            assert w.history._index == 1
            w.undo()
            assert w.project.audio_tracks[last].collapsed
            w.set_project(replace(w.project, audio_tracks=(), audio_clips=())); app.processEvents()
            assert fx.message.isVisible() and 'Add an audio track' in fx.message.text() and fx.add_track.isVisible()
            fx.add_track.click()
            assert len(w.project.audio_tracks) == 1 and fx.track_data() is w.project.audio_tracks[0]
            w.set_workspace(q.SANDBOX); app.processEvents()
            assert fx.message.isVisible() and fx.message.text() == "Effects live on Composer's audio tracks."
            assert not fx.add_track.isVisible() and not fx.editor.isVisible()
        finally:
            w.history_timer.stop(); w.timer.stop(); w.force_close = True
            w.shutdown_audio(); w.release_views(); w.close()
print('Track effects passed: panel editing, track selection, fx badge, lanes, Undo, save/reopen, playback equals export.')
