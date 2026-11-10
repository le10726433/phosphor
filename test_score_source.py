import tempfile
from pathlib import Path
import unittest
import wave
import numpy as np
from live_midi import mido_module
from score_source import (SOURCES, MIDI_FILE_SOURCE, SCALE_NAME, file_tempo, source_duration, score_components,
                          score_arrays, melody_line, melody_arrays)
from signal_engine import Settings, render, export_wav, validate, source_duration as settings_duration

mido = mido_module()


def write_midi(path, bpm=90, notes=((72, 1), (75, 1), (77, 2)), chord_under=True):
    """A one-track file: a melody in quarter-note beats, optionally with a lower chord tone."""
    track = mido.MidiTrack()
    track.append(mido.MetaMessage('set_tempo', tempo=mido.bpm2tempo(bpm)))
    for pitch, beats in notes:
        track.append(mido.Message('note_on', note=pitch, velocity=90))
        if chord_under: track.append(mido.Message('note_on', note=pitch-12, velocity=60))
        track.append(mido.Message('note_off', note=pitch, time=480*beats))
        if chord_under: track.append(mido.Message('note_off', note=pitch-12))
    file = mido.MidiFile(ticks_per_beat=480); file.tracks.append(track); file.save(str(path))
    return str(path)


class MelodyTests(unittest.TestCase):
    def test_melody_keeps_top_line_and_skips_notes_under_a_held_note(self):
        notes = [(0., 1., 60), (0., 1., 72), (.005, .5, 67),  # chord: top note 72
                 (.5, .9, 64),  # starts under the held 72: accompaniment
                 (1., 1.5, 74), (1.2, 2., 76)]  # a higher note cuts the held one short
        self.assertEqual(melody_line(notes), [(0., 1., 72), (1., 1.2, 74), (1.2, 2., 76)])

    def test_rests_fill_long_gaps_and_short_gaps_are_legato(self):
        starts, durations, pitches, gates, beats = melody_arrays([(.5, 1., 60), (1.01, 2., 62), (3., 4., 64)], 60.)
        np.testing.assert_allclose(starts, [0, .5, 1.01, 2., 3.])
        np.testing.assert_array_equal(pitches, [-1, 60, 62, -1, 64])
        self.assertAlmostEqual(gates[1], .5/.51)
        self.assertEqual(beats, 4.)


class MidiFileSourceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = write_midi(Path(self.folder.name)/'tune.mid')

    def tearDown(self):
        self.folder.cleanup()

    def test_no_music_is_bundled(self):
        self.assertEqual(SOURCES, (SCALE_NAME, MIDI_FILE_SOURCE))
        self.assertFalse((Path(__file__).resolve().parent/'assets'/'scores').exists())

    def test_file_tempo_gives_original_speed(self):
        self.assertEqual(file_tempo(self.path), 90)
        starts, durations, pitches, gates, beats = score_arrays(self.path)
        self.assertEqual(list(pitches), [72, 75, 77])  # top line only, chord tones dropped
        self.assertAlmostEqual(beats, 4, places=4)  # the file stores tempo in whole microseconds
        self.assertAlmostEqual(source_duration(90, self.path), 4*60/90, places=4)
        self.assertAlmostEqual(source_duration(180, self.path), 2*60/90, places=4)

    def test_renders_chunk_consistently_and_exports(self):
        settings = Settings(source=MIDI_FILE_SOURCE, midi_file=self.path, bpm=90)
        times = np.arange(24000)/48000
        music, phase, envelope = score_components(times, 90, self.path)
        self.assertGreater(abs(music).max(), .1)
        self.assertLessEqual(abs(music).max(), .8)
        _, original, output = render(times, settings)
        np.testing.assert_array_equal(original, music)
        chunks = np.concatenate([render(times[:9999], settings)[2], render(times[9999:], settings)[2]])
        np.testing.assert_array_equal(output, chunks)
        self.assertAlmostEqual(settings_duration(settings), source_duration(90, self.path))
        wav_path = Path(self.folder.name)/'source.wav'
        export_wav(wav_path, settings, .1, source_only=True)
        with wave.open(str(wav_path), 'rb') as wav:
            pcm = np.frombuffer(wav.readframes(4800), dtype='<i2').reshape(-1, 2)
        expected = score_components(np.arange(4800)/48000, 90, self.path)[0]
        np.testing.assert_array_equal(pcm, np.rint(expected*32767).astype('<i2'))

    def test_edited_file_is_read_again(self):
        first = score_arrays(self.path)[2]
        write_midi(self.path, notes=((60, 1), (62, 1), (64, 1), (65, 1), (67, 1)))
        self.assertEqual(list(score_arrays(self.path)[2]), [60, 62, 64, 65, 67])
        self.assertNotEqual(len(first), 5)

    def test_missing_unreadable_and_unset_files_are_clear_errors(self):
        bad = Path(self.folder.name)/'bad.mid'; bad.write_bytes(b'not midi')
        for path in ('', str(Path(self.folder.name)/'missing.mid'), str(bad)):
            with self.subTest(path=path), self.assertRaises(ValueError):
                score_arrays(path)
        with self.assertRaises(ValueError):
            validate(Settings(source=MIDI_FILE_SOURCE))

    def test_scene_stores_the_file_and_finds_it_beside_a_moved_scene(self):
        from app_files import read_scene, scene_document, atomic_json
        settings = Settings(source=MIDI_FILE_SOURCE, midi_file=self.path, bpm=90)
        document = scene_document(settings, 'mixed', '')
        self.assertEqual((document['version'], document['settings']['midi_file']), (7, self.path))
        self.assertNotIn('midi_file', scene_document(Settings(), 'mixed', '')['settings'])
        with tempfile.TemporaryDirectory() as moved:
            moved = Path(moved)
            write_midi(moved/'tune.mid')
            document['settings']['midi_file'] = '/no/longer/here/tune.mid'
            atomic_json(moved/'song.scene.json', document)
            loaded = read_scene(moved/'song.scene.json')[0]
            self.assertEqual(Path(loaded.midi_file), (moved/'tune.mid').resolve())

    def test_scenes_using_removed_songs_open_with_the_scale(self):
        from app_files import read_scene, scene_document, atomic_json
        for title in ("Howl's Moving Castle", "Howel's Moving Castle", "A Cruel Angel's Thesis"):
            document = scene_document(Settings(), 'mixed', '')
            document['settings']['source'] = title
            with self.subTest(title=title), tempfile.TemporaryDirectory() as folder:
                atomic_json(Path(folder)/'old.scene.json', document)
                self.assertEqual(read_scene(Path(folder)/'old.scene.json')[0].source, SCALE_NAME)


if __name__ == '__main__':
    unittest.main()
