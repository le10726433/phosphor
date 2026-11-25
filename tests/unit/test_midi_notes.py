"""Tick-based MIDI conversion and immutable note insertion."""
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from phosphor.audio.live_midi import mido_module
from phosphor.engine.composition import Note, Project, MAX_NOTES
from phosphor.engine.midi_notes import MidiNotes, read_midi, write_midi, import_notes


class MidiNotesTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name)/'notes.mid'
        self.mido = mido_module()

    def save(self, events):
        file = self.mido.MidiFile(ticks_per_beat=480)
        track = self.mido.MidiTrack(); file.tracks.append(track)
        for kind, values in events:
            cls = self.mido.MetaMessage if kind == 'set_tempo' else self.mido.Message
            track.append(cls(kind, **values))
        file.save(str(self.path))

    def test_grid_round_trip(self):
        project = Project(bpm=100., notes=(Note(1/480, 239/480, 60, 80/127),
                                           Note(1., 1., 72, 127/127)))
        write_midi(self.path, project)
        midi = read_midi(self.path)
        self.assertEqual(midi.notes, project.notes)
        self.assertEqual(midi.bpm, project.bpm)
        file = self.mido.MidiFile(str(self.path))
        self.assertEqual(file.type, 0)
        self.assertEqual(file.ticks_per_beat, 480)

    def test_tempo_and_meter_round_trip_and_opt_in(self):
        # Full velocity (the new-note default) survives MIDI exactly.
        velocity = 1.0
        project = Project(bpm=100., notes=(Note(0., 1., 60, velocity), Note(9., 1., 72, velocity)),
                          tempo_changes=((3., 60.), (9., 150.)),
                          meter_changes=((0, 3, 4), (2, 6, 8), (4, 4, 4)))
        write_midi(self.path, project)
        midi = read_midi(self.path)
        self.assertEqual(midi.bpm, project.bpm)
        self.assertEqual(midi.tempo_changes, project.tempo_changes)
        self.assertEqual(midi.meter_changes, project.meter_changes)
        self.assertEqual(import_notes(Project(), midi, set_tempo=True), project)
        original = Project(bpm=80., tempo_changes=((2., 90.),), meter_changes=((0, 5, 8),))
        self.assertEqual(import_notes(original, midi), replace(original, notes=project.notes))
        # Maps describe the whole song even when notes are inserted later.
        inserted = import_notes(original, midi, 'insert', 4., set_tempo=True)
        self.assertEqual(inserted.tempo_changes, project.tempo_changes)
        self.assertEqual(inserted.meter_changes, project.meter_changes)
        events, tick = [], 0
        for message in self.mido.MidiFile(str(self.path)).tracks[0]:
            tick += message.time
            if message.type in ('set_tempo', 'time_signature'): events.append((tick, message.type))
        self.assertEqual(events, [(0, 'set_tempo'), (0, 'time_signature'), (1440, 'set_tempo'),
                                  (2880, 'time_signature'), (4320, 'set_tempo'), (5760, 'time_signature')])

    def test_chords_drums_and_channel_pairing(self):
        self.save([
            ('note_on', dict(note=60, velocity=50, channel=0)),
            ('note_on', dict(note=72, velocity=90, channel=1)),
            ('note_on', dict(note=100, velocity=127, channel=9)),
            ('note_off', dict(note=72, channel=9, time=120)),
            ('note_off', dict(note=60, channel=0, time=120)),
            ('note_off', dict(note=72, channel=1, time=240)),
            ('note_off', dict(note=100, channel=9)),
        ])
        self.assertEqual(read_midi(self.path).notes, (Note(0., 1., 72, 90/127),))

    def test_tempo_map_keeps_ticks_and_truncates_overlap(self):
        self.save([
            ('set_tempo', dict(tempo=500000)),
            ('note_on', dict(note=60, velocity=80)),
            ('set_tempo', dict(tempo=1000000, time=240)),
            ('note_on', dict(note=72, velocity=100, time=240)),
            ('note_off', dict(note=60, time=240)),
            ('note_off', dict(note=72, time=240)),
        ])
        midi = read_midi(self.path)
        self.assertEqual(midi.notes, (Note(0., 1., 60, 80/127), Note(1., 1., 72, 100/127)))
        self.assertEqual(midi.bpm, 120.)
        self.assertEqual(midi.tempo_changes, ((.5, 60.),))

    def test_first_tempo_after_zero_keeps_midi_default_until_change(self):
        self.save([
            ('note_on', dict(note=60, velocity=80)),
            ('set_tempo', dict(tempo=1000000, time=480)),
            ('note_off', dict(note=60, time=480)),
        ])
        midi = read_midi(self.path)
        self.assertEqual(midi.bpm, 120.)
        self.assertEqual(midi.tempo_changes, ((1., 60.),))

    def test_near_chord_and_lower_note_under_held_melody(self):
        self.save([
            ('note_on', dict(note=60, velocity=80)),
            ('note_on', dict(note=72, velocity=90, time=10)),
            ('note_off', dict(note=60, time=110)),
            ('note_on', dict(note=64, velocity=70, time=120)),
            ('note_off', dict(note=64, time=120)),
            ('note_on', dict(note=72, velocity=0, time=120)),
        ])
        self.assertEqual(read_midi(self.path).notes,
                         (Note(10/480, 470/480, 72, 90/127),))

    def test_empty_and_asynchronous_files_refused(self):
        self.save([])
        with self.assertRaises(ValueError): read_midi(self.path)
        file = self.mido.MidiFile(type=2)
        file.tracks.append(self.mido.MidiTrack())
        file.save(str(self.path))
        with self.assertRaises(ValueError): read_midi(self.path)

    def test_insert_replace_tempo_and_limits(self):
        original = Project(notes=(Note(0., 1., 60),))
        midi = MidiNotes((Note(.5, .5, 72),), 90.)
        inserted = import_notes(original, midi, 'insert', 2.13, .25, True)
        self.assertEqual(inserted.notes, original.notes + (Note(2.75, .5, 72),))
        self.assertEqual(inserted.bpm, 90.)
        self.assertEqual(original, Project(notes=(Note(0., 1., 60),)))
        self.assertEqual(import_notes(original, midi).notes, midi.notes)
        self.assertEqual(import_notes(original, midi).bpm, 120.)
        self.assertEqual(import_notes(original, midi, 'insert', 2.13).notes[-1].start, 2.63)
        with self.assertRaises(ValueError):
            import_notes(Project(notes=original.notes * MAX_NOTES), midi, 'insert')
        with self.assertRaises(ValueError): import_notes(original, midi, 'bad')

    def test_out_of_range_dropped_and_cap_reported(self):
        events = []
        for pitch in (20, 21, 108, 109):
            events.extend([('note_on', dict(note=pitch, velocity=80, time=480)),
                           ('note_off', dict(note=pitch, time=480))])
        self.save(events)
        midi = read_midi(self.path)
        self.assertEqual([note.pitch for note in midi.notes], [21, 108])
        self.assertEqual(midi.dropped, 2)
        events = []
        for _ in range(MAX_NOTES + 1):
            events.extend([('note_on', dict(note=60, velocity=80, time=480)),
                           ('note_off', dict(note=60, time=480))])
        self.save(events)
        midi = read_midi(self.path)
        self.assertEqual(len(midi.notes), MAX_NOTES)
        self.assertEqual(midi.dropped, 1)


if __name__ == '__main__': unittest.main()
