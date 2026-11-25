"""Device-free AW1 track storage, sample mixing, edits, clipboard and media tests."""
from copy import deepcopy
from dataclasses import replace
from itertools import product
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile

from phosphor.engine import audio_clips as a, audio_edit as e, audio_media as media, audio_tracks as t, clipboard
from phosphor.engine.audio_clips import AudioTrack
from phosphor.engine.composition import Clip, Note, Project, render_song, validate_project
from phosphor.engine.signal_engine import Settings, Shape
from phosphor.engine.switch_sweep import project_signature
from phosphor.storage import project_files as files

RATE = 48000
MACHINE = Settings(sample_rate=RATE, switch_sweep_ms=0.)
HEADERS = (AudioTrack('Drums'), AudioTrack('Bass'), AudioTrack('Lead'))


class TrackTests(unittest.TestCase):
    """Temporary float WAVs with distinct left and right channels."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.times = np.arange(2400) / RATE
        self.clips = []
        for i in range(2):
            path = self.root / f'tone{i}.wav'
            phase = self.times * (220 + 75 * i) * 2 * np.pi
            soundfile.write(path, np.column_stack((.3*np.sin(phase), .2*np.cos(phase))), RATE, subtype='FLOAT')
            self.clips.append(replace(a.audio_clip_from_file(path, 0.), track=i))
        self.project = Project(audio_tracks=HEADERS, audio_clips=tuple(self.clips))
        self.song = self.root / 'song.project.json'
        self.scene = Clip(0., 4., Settings(shapes=(Shape(),)), 'drawing')
        a.clear_decoded()
        self.addCleanup(a.clear_decoded)

    def render(self, project, peaks=None):
        if project.clips: project = replace(project, notes=(Note(0., 4., 60),))
        return render_song(self.times, project, MACHINE, track_peaks=peaks)

    def test_migration_and_version_gates(self):
        c = self.clips[0]
        clips = (c, replace(c, muted=True), replace(c, gain_points=((0., .25, 'hold'),)))
        old = dict(files.project_document(Project()), version=9)
        self.assertEqual(files.project_from_document(old).audio_tracks, ())
        old['audio_clips'] = [files.audio_clip_document(clip, None) for clip in clips]
        project = files.project_from_document(old)
        self.assertEqual(project.audio_tracks, (AudioTrack('Audio 1'),))
        self.assertEqual(project.audio_clips, clips)
        doc = files.project_document(project)
        self.assertEqual(doc['version'], files.PROJECT_VERSION)
        self.assertNotIn('audio_clips', doc)
        with self.assertRaisesRegex(ValueError, 'Audio tracks require project version 10'):
            files.project_from_document(dict(old, audio_tracks=[]))
        with self.assertRaisesRegex(ValueError, 'Audio clips are stored in audio tracks'):
            files.project_from_document(dict(doc, audio_clips=[]))

    def test_round_trip_and_validation(self):
        p = t.set_collapsed(t.set_muted(t.set_gain(self.project, [0], .75), [1], True), [2], True)
        doc = files.project_document(p)
        self.assertEqual(files.project_from_document(doc), p)
        saved = files.save_project(self.song, p)
        self.assertEqual(files.read_project(self.song), p)
        self.assertEqual(saved['audio_tracks'][1]['clips'][0]['path'], 'tone1.wav')
        self.assertEqual(saved['audio_tracks'][2], dict(name='Lead', collapsed=True, clips=[]))
        for value in (None, [{}], [dict(name='T', clips=[], unknown=1)], [dict(name='T', clips=None)]):
            with self.assertRaisesRegex(ValueError, 'Invalid audio track fields'):
                files.project_from_document(dict(doc, audio_tracks=value))
        for changes in (dict(name='  '), dict(name='x'*65), dict(name=None), dict(gain=float('nan')),
                        dict(gain=2.1), dict(muted=1), dict(collapsed=0)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                a.validate_audio_track(replace(HEADERS[0], **changes))
        for track in (-1, True, 1., len(HEADERS)):
            with self.assertRaises(ValueError):
                validate_project(replace(p, audio_clips=(replace(self.clips[0], track=track),)))

    def test_unity_matches_a3_including_overlapping_clips_and_scene(self):
        clip = replace(self.clips[0], gain=.73, fade_in=.01, fade_out=.015, gain_points=((0., .375, 'hold'),))
        k = np.arange(len(self.times))
        fade = np.minimum(1., k / round(.01 * RATE))
        fade = np.minimum(fade, (len(k) - k) / round(.015 * RATE))
        samples, _ = soundfile.read(clip.path)
        contribution = (fade * clip.gain * .375)[:, None] * samples
        for scenes, clips in (((), (clip,)), ((self.scene,), (clip, clip))):
            reference = self.render(Project(clips=scenes))
            for _ in clips: reference += contribution
            p = Project(clips=scenes, audio_clips=clips)
            explicit = replace(p, audio_tracks=(AudioTrack('Audio 1'),))
            peaks = [0.]
            for result in (self.render(p), self.render(explicit), self.render(explicit, peaks)):
                np.testing.assert_array_equal(result, reference)
            self.assertEqual(peaks, [np.abs(contribution * len(clips)).max()])

    def test_scaled_sums_peaks_and_skipped_decodes(self):
        p = replace(self.project, clips=(self.scene,), audio_tracks=(AudioTrack('A', .5), AudioTrack('B', 1.5), HEADERS[2]))
        scene = self.render(Project(clips=(self.scene,)))
        self.assertTrue(scene.any())
        parts = [self.render(Project(audio_clips=(replace(c, track=0),))) for c in self.clips]
        expected = scene + .5*parts[0] + 1.5*parts[1]
        peaks = [0., 0., .7]
        result = self.render(p, peaks)
        np.testing.assert_allclose(result, expected, rtol=0., atol=1e-12)
        np.testing.assert_array_equal(result, self.render(p))
        self.assertEqual(peaks, [np.abs(.5*parts[0]).max(), np.abs(1.5*parts[1]).max(), .7])
        np.testing.assert_array_equal(self.render(p, []), result)
        for silent in (t.set_gain(p, [0], 0.), t.set_muted(p, [0], True)):
            a.clear_decoded()
            a.decoded(self.clips[1].path, self.clips[1].fingerprint, RATE)
            before = a.decodes
            np.testing.assert_array_equal(self.render(silent), scene + 1.5*parts[1])
            self.assertEqual(a.decodes, before)
            self.assertEqual(a.needed(silent), [(c.path, c.fingerprint) for c in reversed(self.clips)])
            with patch.object(a, 'prepare'), patch.object(a, 'decoded') as decode:
                a.prime(silent, RATE, 0.)
                decode.assert_called_once_with(self.clips[1].path, self.clips[1].fingerprint, RATE)

    def test_track_gain_follows_sum_and_peak_holds_between_blocks(self):
        clips = (replace(self.clips[0], gain=.73), replace(self.clips[1], track=0, gain=.81))
        p = Project(audio_clips=clips)
        total = self.render(p)
        for gain in (1., 1.23):
            peaks = [0.]
            playing = t.set_gain(p, [0], gain)
            np.testing.assert_array_equal(self.render(playing, peaks), gain * total)
            expected_peak = np.abs(gain * total).max()
            self.assertEqual(peaks, [expected_peak])
            render_song(self.times + 1., playing, MACHINE, track_peaks=peaks)
            self.assertEqual(peaks, [expected_peak])
            peaks[0] = expected_peak + 1.
            self.render(playing, peaks)
            self.assertEqual(peaks, [expected_peak + 1.])

    def test_solo_truth_table_and_edges(self):
        parts = [self.render(Project(audio_clips=(replace(c, track=0),))) for c in self.clips]
        scene = self.render(Project(clips=(self.scene,)))
        for cm, tm, cs, ts in product((False, True), repeat=4):
            p = replace(self.project, clips=(self.scene,))
            p = t.set_muted(e.set_muted(p, 0, cm)[0], [0], tm)
            for other_solo in (False, True):
                indices = ({0} if cs else set()) | ({1} if other_solo else set())
                tracks = {0} if ts else set()
                solo = bool(indices or tracks)
                playing = e.soloed(p, indices, tracks) if solo else p
                expected = np.zeros_like(scene) if solo else scene.copy()
                if not cm and not tm and (not solo or cs or ts): expected += parts[0]
                if not solo or other_solo: expected += parts[1]
                with self.subTest(cm=cm, tm=tm, cs=cs, ts=ts, other_solo=other_solo):
                    np.testing.assert_array_equal(self.render(playing), expected)
        self.assertFalse(self.render(e.soloed(p, set())).any())
        c = self.clips[0]
        edge = replace(c, start=float(p.beats(c.duration)), muted=True, track=1)
        p = replace(self.project, audio_clips=(c, edge))
        self.assertEqual(a.muted_edges(p, 0), (False, False))
        self.assertEqual(a.muted_edges(replace(p, audio_clips=(c, replace(edge, track=0))), 0), (False, True))
        self.assertNotEqual(project_signature(p), project_signature(t.set_muted(p, [0], True)))

    def test_track_edits_and_order(self):
        p = Project(audio_clips=(self.clips[0],))
        p, i = t.add_track(p)
        self.assertEqual((i, p.audio_tracks[i].name), (1, 'Audio 2'))
        p, i = t.add_track(p, at=0, name='audio 3')
        self.assertEqual((i, p.audio_clips[0].track), (0, 1))
        p = t.remove_tracks(p, [1])
        self.assertEqual((p.audio_clips, t.default_name(p)), ((), 'Audio 1'))
        self.assertEqual(t.remove_tracks(p, [0, 1]), Project())
        self.assertEqual(t.rename_track(p, 1, '  Kick  ').audio_tracks[1].name, 'Kick')
        self.assertEqual(t.rename_track(p, 1, 'X'*70).audio_tracks[1].name, 'X'*64)
        with self.assertRaisesRegex(ValueError, 'needs a name'): t.rename_track(p, 0, ' ')
        for target, order, indices in ((-5, (1, 2, 0, 3), (0, 1)), (9, (0, 3, 1, 2), (2, 3))):
            self.assertEqual(t.moved_order(4, [2, 1], target), (order, indices))
        p = t.reorder(self.project, (2, 1, 0))
        self.assertEqual(p.audio_clips, (self.clips[1], replace(self.clips[0], track=2)))
        for order in ([0, 1, 1], [0, True, 2], None):
            with self.assertRaisesRegex(ValueError, 'Invalid track order'): t.reorder(p, order)
        dup, where = t.duplicate_tracks(self.project, [2, 0])
        self.assertEqual(where, (3, 4))
        self.assertEqual([dup.audio_tracks[i].name for i in where], ['Drums copy', 'Lead copy'])
        self.assertEqual(dup.audio_clips[-1], replace(self.clips[0], track=3))
        self.assertEqual(t.track_clips(dup, 3), (2,))
        for gain, expected in ((-5, 0.), (9, 2.)):
            self.assertEqual(t.set_gain(p, [0, 2], gain).audio_tracks[2].gain, expected)
        with self.assertRaisesRegex(ValueError, '32 audio tracks'): t.add_track(Project(audio_tracks=(AudioTrack(),)*32))
        full = Project(audio_clips=(self.clips[0],)*256)
        with self.assertRaisesRegex(ValueError, 'Too many audio clips'): t.duplicate_tracks(full, [0])
        moved, index = e.move(self.project, 0, 1., track=1)
        self.assertEqual((index, moved.audio_clips[1]), (1, replace(self.clips[0], start=1., track=1)))
        self.assertEqual(e.describe(self.project.audio_clips, moved.audio_clips), 'Move audio clip')
        with self.assertRaisesRegex(ValueError, 'does not exist'): e.move(p, 0, 0., track=-1)

    def test_undo_labels(self):
        p = self.project
        for after, label in ((t.add_track(p)[0], 'Add audio track'), (t.insert_tracks(p, HEADERS[:2], (), 0)[0], 'Add audio tracks'),
                             (t.duplicate_tracks(p, [0])[0], 'Duplicate audio track'),
                             (t.duplicate_tracks(p, [0, 1])[0], 'Duplicate audio tracks'),
                             (t.remove_tracks(p, [1]), 'Delete audio track'), (t.remove_tracks(p, [2, 0]), 'Delete audio tracks'),
                             (t.reorder(p, [2, 0, 1]), 'Move audio track'), (t.rename_track(p, 0, 'Mix'), 'Rename audio track'),
                             (t.set_gain(p, [0, 1], .5), 'Change audio track gain'),
                             (t.set_muted(t.set_gain(p, [0], .5), [1], True), 'Edit audio tracks'),
                             (e.move(p, 0, 0., track=1)[0], 'Move audio clip'),
                             (replace(p, audio_clips=tuple(replace(c, track=2) for c in self.clips)), 'Move audio clips'),
                             (e.set_gain(p, 0, .3)[0], None), (p, None)):
            self.assertEqual(t.describe(p, after), label)
        four, _ = t.add_track(p)
        self.assertEqual(t.describe(four, t.reorder(four, [2, 3, 0, 1])), 'Move audio tracks')
        for setter, on, off in ((t.set_muted, 'Mute', 'Unmute'), (t.set_collapsed, 'Collapse', 'Expand')):
            for picks, suffix in (([0], ' audio track'), ([0, 1], ' audio tracks')):
                after = setter(p, picks, True)
                self.assertEqual(t.describe(p, after), on + suffix)
                self.assertEqual(t.describe(after, p), off + suffix)

    def test_clipboard(self):
        c = replace(self.clips[0], muted=True, routing='x', gain_points=((0., .5, 'linear'),), fade_in=.01, fade_out=.02)
        p = replace(self.project, audio_clips=(c, self.clips[1]))
        p = t.set_collapsed(t.set_gain(t.set_muted(p, [1], True), [0], .25), [0], True)
        payload = clipboard.from_text(clipboard.to_text(clipboard.copy_tracks(p, [1, 0])))
        self.assertEqual(clipboard.describe(payload), '2 audio tracks')
        self.assertEqual(clipboard.describe(clipboard.copy_tracks(p, [2])), '1 audio track')
        for at, first in ((0, 0), (None, 3)):
            after, indices = clipboard.paste_tracks(p, payload, at)
            self.assertEqual(indices, (first, first + 1))
            self.assertEqual(tuple(after.audio_tracks[i] for i in indices), p.audio_tracks[:2])
            self.assertEqual(tuple(replace(c, track=c.track-first) for c in after.audio_clips if c.track in indices), p.audio_clips)
        Path(c.path).unlink()
        self.assertTrue(clipboard.paste_tracks(Project(), payload)[0].audio_clips[0].missing)
        for item in (dict(extra='bad'), dict(clips=[{}]), dict(gain=3)):
            bad = deepcopy(payload); bad['tracks'][0].update(item)
            with self.assertRaises(ValueError): clipboard.paste_tracks(p, bad)
        with self.assertRaises(ValueError): clipboard.to_text(dict(payload, tracks=payload['tracks']*17))

    def test_collect_missing_and_relink_across_tracks(self):
        c, b = self.clips
        p = replace(self.project, audio_clips=(c, replace(c, track=1), replace(b, missing=True, track=2)))
        plan = media.collect_plan(p, self.song)
        self.assertEqual((len(plan.copies), plan.skipped), (1, 1))
        media.copy_media(plan)
        collected = media.collected(p, plan)
        for i, clip in enumerate(collected.audio_clips):
            self.assertEqual(clip.track, i)
            if i < 2: self.assertEqual(Path(clip.path), plan.folder / c.name)
        lost = replace(p, audio_clips=tuple(replace(clip, missing=True) for clip in p.audio_clips))
        self.assertEqual(media.missing_files(lost)[media.file_key(c)], [0, 1])
        linked = media.relink(lost, {media.file_key(c): plan.folder / c.name})
        self.assertEqual(linked.audio_clips, collected.audio_clips)

    def test_review_fixes(self):
        """Codex review of AW1: legacy order, cheap header edits, copy names, labels, paste reads."""
        c, b = self.clips
        # A hand-edited older file with clips out of start order opens in the canonical order.
        old = dict(files.project_document(Project()), version=9)
        old['audio_clips'] = [files.audio_clip_document(replace(c, start=1.), None), files.audio_clip_document(c, None)]
        self.assertEqual([x.start for x in files.project_from_document(old).audio_clips], [0., 1.])
        # Header edits change only the tracks (no score revalidation); an unchanged edit returns the project.
        with patch('phosphor.engine.audio_tracks.validate_project', side_effect=AssertionError('whole project validated')):
            self.assertEqual(t.set_gain(self.project, [0], .5).audio_tracks[0].gain, .5)
            self.assertIs(t.set_muted(self.project, [1], False), self.project)
        with self.assertRaises(ValueError): t.rename_track(self.project, 0, ' ')
        # A copy of a 64-character name keeps ' copy' and is labelled as a duplicate.
        long = t.rename_track(self.project, 0, 'x' * 64)
        copied, (index,) = t.duplicate_tracks(long, [0])
        self.assertTrue(copied.audio_tracks[index].name.endswith(' copy') and len(copied.audio_tracks[index].name) == 64)
        self.assertEqual(t.describe(long, copied), 'Duplicate audio track')
        # An Alt-drag copy onto another track is a duplicate.
        moved = e.duplicate(self.project, 0, 2.)[0]
        moved = e.move(moved, next(i for i, x in enumerate(moved.audio_clips) if x.start == 2.), 2., track=2)[0]
        self.assertEqual(e.describe(self.project.audio_clips, moved.audio_clips), 'Duplicate audio clip')
        # A paste checks each file once, and not at all for files the project already plays.
        payload = clipboard.from_text(clipboard.to_text(clipboard.copy_tracks(replace(self.project, audio_clips=(c, replace(c, start=1.), b)), [0, 1])))
        with patch('phosphor.engine.clipboard.resolve_audio_clip', wraps=a.resolve_audio_clip) as resolve:
            clipboard.paste_tracks(Project(), payload)
            self.assertEqual(resolve.call_count, 2)
            resolve.reset_mock(); clipboard.paste_tracks(self.project, payload)
            self.assertEqual(resolve.call_count, 0)
        many = Project(audio_tracks=(AudioTrack(),) * 31)
        with patch('phosphor.engine.clipboard.resolve_audio_clip') as resolve:
            with self.assertRaises(ValueError): clipboard.paste_tracks(many, payload)
            resolve.assert_not_called()

