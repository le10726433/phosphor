"""Audio clips A3: missing files (search order, relinking, folder searches), Collect media, and
audio clip mute and solo. Every WAV is generated in a temporary folder; no fixtures."""
import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
import soundfile

from phosphor.engine import audio_clips as a
from phosphor.engine import audio_edit as e
from phosphor.engine import audio_media as m
from phosphor.engine.composition import Project, Clip, render_song, export_song
from phosphor.engine.signal_engine import Settings
from phosphor.engine.switch_sweep import project_signature
from phosphor.storage.project_files import (PROJECT_VERSION, project_document, project_from_document,
                                            read_project, save_project)

RATE = 48000
MACHINE = Settings(sample_rate=RATE)
SWEPT = replace(MACHINE, switch_sweep_ms=5.)


def tone(path, frequency, seconds=.25, rate=RATE, amplitude=.4):
    """A float WAV. Its PEAK chunk holds a timestamp, so copy a file to get the same bytes."""
    t = np.arange(round(seconds * rate)) / rate
    samples = np.column_stack((amplitude * np.sin(2 * np.pi * frequency * t),
                               amplitude * np.cos(2 * np.pi * frequency * 1.5 * t))).astype('float32')
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    soundfile.write(path, samples, rate, subtype='FLOAT')
    return Path(path)


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        # Resolved, like clip paths: the macOS temp folder is under a link (/var is
        # /private/var), so unresolved paths would not compare equal to them.
        self.root = Path(self.temp.name).resolve()
        a.clear_decoded(); self.addCleanup(a.clear_decoded)
        self.project_path = self.root / 'song' / 'Song.project.json'
        self.project_path.parent.mkdir()

    def saved(self, *paths):
        """A saved project with one clip per path (half a second apart), reopened."""
        clips = tuple(a.audio_clip_from_file(path, i) for i, path in enumerate(paths))
        save_project(self.project_path, Project(audio_clips=clips))
        return read_project(self.project_path)

    # Automatic search ---------------------------------------------------------------
    def test_search_order_and_each_step(self):
        clip = a.audio_clip_from_file(tone(self.root / 'song' / 'sub' / 'a.wav', 220), 0.)
        candidates = a.search_candidates(clip, self.project_path, 'sub/a.wav')
        self.assertEqual(candidates, [self.project_path.parent / 'sub/a.wav', Path(clip.path),
                                      self.project_path.parent / 'Song media' / 'a.wav', self.project_path.parent / 'a.wav'])
        self.assertEqual(a.media_folder(self.project_path), self.project_path.parent / 'Song media')
        source = Path(clip.path)
        lost = replace(clip, path=str(self.root / 'gone' / 'a.wav'))
        # From the last step to the first: each earlier step, once present, wins over the later ones.
        for place in ('a.wav', 'Song media/a.wav', None, 'sub/a.wav'):
            with self.subTest(place=place):
                target = source if place is None else self.project_path.parent / place
                if place is None:
                    found = a.resolve_audio_clip(clip, self.project_path, 'nowhere/a.wav')
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target != source: shutil.copyfile(source, target)
                    found = a.resolve_audio_clip(lost, self.project_path, place if place == 'sub/a.wav' else 'nowhere/a.wav')
                self.assertFalse(found.missing)
                self.assertEqual(Path(found.path), target.resolve())
        # The first matching step wins, and a file with other contents is passed over.
        other = tone(self.root / 'other.wav', 330)
        shutil.copyfile(other, self.project_path.parent / 'sub' / 'a.wav')
        found = a.resolve_audio_clip(lost, self.project_path, 'sub/a.wav')
        self.assertEqual(Path(found.path), (self.project_path.parent / 'Song media' / 'a.wav').resolve())

    def test_skipped_clip_plays_silence_and_keeps_its_path(self):
        project = self.saved(tone(self.root / 'far' / 'x.wav', 220))
        last = project.audio_clips[0].path
        shutil.rmtree(self.root / 'far')
        a.clear_decoded()
        notices = []; reopened = read_project(self.project_path, notices)
        self.assertTrue(reopened.audio_clips[0].missing)
        self.assertEqual(notices, ['Audio file not found: x.wav'])
        self.assertEqual(m.missing_files(reopened), {(last, reopened.audio_clips[0].fingerprint): [0]})
        self.assertFalse(np.any(render_song(np.arange(RATE // 4) / RATE, reopened, MACHINE)))
        save_project(self.project_path, reopened)
        document = json.loads(self.project_path.read_text(encoding='utf-8'))
        self.assertEqual(document['audio_tracks'][0]['clips'][0]['absolute'], last)
        self.assertNotIn('missing', document['audio_tracks'][0]['clips'][0])
        self.assertTrue(read_project(self.project_path).audio_clips[0].missing)

    # Relinking -------------------------------------------------------------------------
    def test_relink_matching_file_fixes_every_clip_of_it(self):
        path = tone(self.root / 'far' / 'x.wav', 220)
        project = self.saved(path, path)  # Two clips share one file.
        moved = self.root / 'moved' / 'x.wav'; moved.parent.mkdir(); shutil.move(path, moved)
        project = read_project(self.project_path)
        self.assertEqual(m.missing_count(project), 1)
        relinked = m.relink(project, {m.file_key(project.audio_clips[0]): moved})
        self.assertEqual([Path(c.path) for c in relinked.audio_clips], [moved.resolve()] * 2)
        self.assertFalse(any(c.missing for c in relinked.audio_clips))
        self.assertEqual([replace(c, path='', missing=False) for c in project.audio_clips],
                         [replace(c, path='') for c in relinked.audio_clips])
        self.assertEqual(e.describe(project.audio_clips, relinked.audio_clips), 'Relink audio file')
        save_project(self.project_path, relinked)
        self.assertFalse(read_project(self.project_path).audio_clips[0].missing)

    def test_use_anyway_takes_the_new_file_and_shortens(self):
        project = self.saved(tone(self.root / 'far' / 'x.wav', 220, seconds=.5))
        project = e.set_fades(*e.trim_start(project, 0, .25), fade_out=.2)[0]
        clip = project.audio_clips[0]
        shutil.rmtree(self.root / 'far')
        shorter = tone(self.root / 'take2.wav', 330, seconds=.4)
        self.assertFalse(a.matches(clip, shorter))
        located = m.located_clip(replace(clip, missing=True), shorter)
        self.assertEqual((located.name, located.size, located.fingerprint), ('take2.wav', shorter.stat().st_size, a.fingerprint(shorter)))
        self.assertAlmostEqual(located.source_duration, .4)
        self.assertAlmostEqual(located.offset, clip.offset)
        self.assertAlmostEqual(located.duration, .4 - clip.offset)
        self.assertLessEqual(located.fade_out, located.duration)
        tiny = tone(self.root / 'tiny.wav', 330, seconds=.1)
        with self.assertRaises(ValueError): m.located_clip(clip, tiny)
        (self.root / 'text.wav').write_text('not audio', encoding='utf-8')
        with self.assertRaises(ValueError): m.located_clip(clip, self.root / 'text.wav')

    def test_same_folder_follow_up_and_folder_search(self):
        paths = [tone(self.root / 'far' / f'{name}.wav', f) for name, f in (('a', 220), ('b', 275), ('c', 330))]
        project = self.saved(*paths)
        # Copies, not regenerated tones: a float WAV's PEAK chunk holds a timestamp.
        (self.root / 'found' / 'deep').mkdir(parents=True)
        for path in paths: shutil.copyfile(path, self.root / 'found' / path.name)
        shutil.copyfile(paths[2], self.root / 'found' / 'deep' / 'c.wav')
        shutil.rmtree(self.root / 'far')
        tone(self.root / 'decoy' / 'b.wav', 999)  # Same name, other contents.
        project = read_project(self.project_path)
        keys = list(m.missing_files(project))
        self.assertEqual(len(keys), 3)
        # After a hand-located a.wav, the others in its folder are found by name and fingerprint.
        others = m.find_in_folder(project, self.root / 'found', set(keys) - {keys[0]})
        self.assertEqual(sorted(p.name for p in others.values()), ['b.wav', 'c.wav'])
        self.assertEqual(m.find_in_folder(project, self.root / 'decoy'), {})
        # Search folder: subfolders included, sorted top-down, the first match wins.
        found = m.search_folder(project, self.root)
        self.assertEqual({k: p.relative_to(self.root.resolve()).as_posix() for k, p in found.items()},
                         {keys[0]: 'found/a.wav', keys[1]: 'found/b.wav', keys[2]: 'found/c.wav'})
        relinked = m.relink(project, found)
        self.assertEqual(m.missing_count(relinked), 0)
        self.assertEqual(e.describe(project.audio_clips, relinked.audio_clips), 'Relink audio files')
        self.assertEqual(m.search_folder(project, self.root / 'decoy'), {})
        self.assertEqual(m.search_folder(project, self.root, stop=lambda: True).keys() <= set(keys), True)

    # Collect media ---------------------------------------------------------------------
    def test_collect_media_copies_relinks_and_reopens(self):
        first = tone(self.root / 'elsewhere' / 'kick.wav', 220)
        clash = tone(self.root / 'other' / 'kick.wav', 330)  # Same name, other file.
        inside = tone(self.root / 'song' / 'Song media' / 'pad.wav', 110)
        gone = tone(self.root / 'gone' / 'lost.wav', 440)
        project = self.saved(first, first, clash, inside, gone)
        shutil.rmtree(self.root / 'gone')
        project = read_project(self.project_path)
        for clip in project.audio_clips: a.decoded(clip.path, clip.fingerprint, RATE) if not clip.missing else None
        plan = m.collect_plan(project, self.project_path)
        folder = self.project_path.parent / 'Song media'
        self.assertEqual(plan.folder, folder)
        self.assertEqual([(s.name, d.name) for s, d in plan.copies], [('kick.wav', 'kick.wav'), ('kick.wav', 'kick (2).wav')])
        self.assertEqual(plan.skipped, 1)
        before = render_song(np.arange(2 * RATE) / RATE, project, MACHINE)
        m.copy_media(plan)
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ['kick (2).wav', 'kick.wav', 'pad.wav'])
        collected = m.collected(project, plan)
        self.assertEqual([Path(c.path).parent == folder.resolve() for c in collected.audio_clips], [True] * 4 + [False])
        self.assertTrue(collected.audio_clips[4].missing)
        self.assertEqual(e.describe(project.audio_clips, collected.audio_clips), 'Relink audio files')
        for old, new in zip(project.audio_clips, collected.audio_clips):
            if old.path != new.path: a.alias_decoded(old.path, new.path, old.fingerprint)
            if not new.missing:  # The copy reuses the decode: same array, no file read.
                self.assertIs(a.decoded(new.path, new.fingerprint, RATE), a.decoded(old.path, old.fingerprint, RATE))
        self.assertTrue(np.array_equal(render_song(np.arange(2 * RATE) / RATE, collected, MACHINE), before))
        # The collected project no longer needs the originals.
        save_project(self.project_path, collected)
        shutil.rmtree(self.root / 'elsewhere'); shutil.rmtree(self.root / 'other')
        a.clear_decoded()
        reopened = read_project(self.project_path)
        self.assertEqual([c.missing for c in reopened.audio_clips], [False] * 4 + [True])
        self.assertTrue(np.array_equal(render_song(np.arange(2 * RATE) / RATE, reopened, MACHINE), before))
        # A second collect has nothing to copy.
        again = m.collect_plan(reopened, self.project_path)
        self.assertEqual(again.copies, ())
        self.assertEqual(m.collected(reopened, again), reopened)

    def test_collect_reuses_an_identical_file_already_there(self):
        source = tone(self.root / 'elsewhere' / 'kick.wav', 220)
        project = self.saved(source)
        shutil.copyfile(source, (self.project_path.parent / 'Song media').mkdir() or self.project_path.parent / 'Song media' / 'kick.wav')
        plan = m.collect_plan(project, self.project_path)
        self.assertEqual(plan.copies, ())
        self.assertEqual(Path(m.collected(project, plan).audio_clips[0].path).parent, (self.project_path.parent / 'Song media').resolve())


class MuteSoloTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        # Resolved, like clip paths: the macOS temp folder is under a link (/var is
        # /private/var), so unresolved paths would not compare equal to them.
        self.root = Path(self.temp.name).resolve()
        a.clear_decoded(); self.addCleanup(a.clear_decoded)
        self.path = tone(self.root / 'tone.wav', 220, seconds=1.)
        clip = a.audio_clip_from_file(self.path, 0.)
        # Split at beat 1 (0.5 s at 120 BPM): two abutting pieces of one file.
        self.project, _ = e.split(Project(audio_clips=(clip,)), 0, 1.)
        self.times = np.arange(RATE) / RATE

    def render(self, project, machine=SWEPT, times=None):
        return render_song(self.times if times is None else times, project, machine)

    def test_muted_clip_is_silent_and_saved(self):
        muted, index = e.set_muted(self.project, 1, True)
        self.assertEqual(index, 1)
        self.assertEqual(e.describe(self.project.audio_clips, muted.audio_clips), 'Mute audio clip')
        self.assertEqual(e.describe(muted.audio_clips, self.project.audio_clips), 'Unmute audio clip')
        out = self.render(muted, MACHINE)
        full = self.render(self.project, MACHINE)
        self.assertTrue(np.array_equal(out[:RATE // 2], full[:RATE // 2]))  # Sweep 0: a hard edge, as before.
        self.assertFalse(np.any(out[RATE // 2:]))
        document = project_document(muted)
        self.assertEqual(document['version'], PROJECT_VERSION)
        self.assertEqual([c.get('muted') for c in document['audio_tracks'][0]['clips']], [None, True])
        self.assertEqual(project_from_document(document), muted)
        old = dict(document, version=8)
        old['audio_clips'] = [dict(c) for c in old.pop('audio_tracks')[0]['clips']]
        with self.assertRaises(ValueError): project_from_document(old)
        old['audio_clips'][1].pop('muted')
        self.assertFalse(project_from_document(old).audio_clips[1].muted)
        bad = project_document(muted); bad['audio_tracks'][0]['clips'][1]['muted'] = 1
        with self.assertRaises(ValueError): project_from_document(bad)
        # Split and duplicate keep the mute.
        self.assertTrue(all(c.muted for c in e.split(muted, 1, 1.5)[0].audio_clips[1:]))

    def test_edges_next_to_a_muted_clip_fade_over_the_sweep(self):
        sweep = round(.005 * RATE); cut = RATE // 2
        full = self.render(self.project)
        self.assertTrue(np.array_equal(full, self.render(self.project, MACHINE)))  # Nothing muted: sweep changes nothing.
        right_muted = self.render(e.set_muted(self.project, 1, True)[0])
        ramp = (cut - np.arange(cut - sweep, cut)) / sweep
        np.testing.assert_allclose(right_muted[cut - sweep:cut], full[cut - sweep:cut] * ramp[:, None], atol=1e-12)
        self.assertTrue(np.array_equal(right_muted[:cut - sweep], full[:cut - sweep]))
        self.assertFalse(np.any(right_muted[cut:]))
        left_muted = self.render(e.set_muted(self.project, 0, True)[0])
        ramp = np.arange(sweep) / sweep
        np.testing.assert_allclose(left_muted[cut:cut + sweep], full[cut:cut + sweep] * ramp[:, None], atol=1e-12)
        self.assertTrue(np.array_equal(left_muted[cut + sweep:], full[cut + sweep:]))
        self.assertFalse(np.any(left_muted[:cut]))
        self.assertEqual(a.muted_edges(e.set_muted(self.project, 1, True)[0], 0), (False, True))
        self.assertEqual(a.muted_edges(e.set_muted(self.project, 0, True)[0], 1), (True, False))
        # A gap is not an edge.
        moved = e.set_muted(e.move(self.project, 1, 1.25)[0], 1, True)[0]
        self.assertEqual(a.muted_edges(moved, 0), (False, False))
        # A longer fade of the clip itself is kept.
        faded = e.set_fades(e.set_muted(self.project, 1, True)[0], 0, fade_out=.05)[0]
        self.assertTrue(np.array_equal(self.render(faded), self.render(faded, MACHINE)))

    def test_playback_blocks_equal_export_with_mute_and_solo(self):
        project = e.set_muted(self.project, 1, True)[0]
        project = replace(project, clips=(Clip(0., 4., Settings()),))
        for case in (project, e.soloed(project, {0})):
            with self.subTest(soloed=case is not project):
                path = self.root / 'export.wav'
                export_song(path, case, SWEPT, end_beats=2., format='WAV', depth=32, rate=RATE)
                exported, _ = soundfile.read(path, dtype='float32', always_2d=True)
                blocks = np.concatenate([self.render(case, times=np.arange(i, min(i + 509, RATE)) / RATE)
                                         for i in range(0, RATE, 509)])
                self.assertTrue(np.array_equal(exported, blocks.astype('float32')))
                self.assertTrue(np.array_equal(blocks, self.render(case)))

    def test_solo(self):
        other = a.audio_clip_from_file(tone(self.root / 'other.wav', 330, seconds=.5), 0.)
        project = replace(e.add(self.project, other)[0], clips=(Clip(0., 4., Settings()),))
        index = next(i for i, c in enumerate(project.audio_clips) if c.path == other.path)
        soloed = e.soloed(project, {index})
        self.assertTrue(all(c.muted for c in soloed.clips))
        self.assertEqual([c.muted for c in soloed.audio_clips], [i != index for i in range(3)])
        self.assertEqual(soloed.audio_clips[index], project.audio_clips[index])
        alone = Project(audio_clips=(other,))
        self.assertTrue(np.array_equal(self.render(soloed, MACHINE), self.render(alone, MACHINE)))
        # Solo is never saved: the document of the soloed copy is not what the editor saves.
        self.assertNotEqual(project_signature(project), project_signature(soloed))
        self.assertNotEqual(project_signature(project), project_signature(e.set_muted(project, 0, True)[0]))


if __name__ == '__main__':
    unittest.main()
