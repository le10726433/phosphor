import json
from pathlib import Path
import tempfile
import unittest
import wave
import numpy as np
from live_midi import LiveMidi, mido_module
from midi_synth import MidiSynth, MIDI_SOURCE, PRESETS, MAX_VOICES
from signal_engine import Settings, Shape
from audio_preview import AudioPreview

mido = mido_module()


class MidiSynthTests(unittest.TestCase):
    def test_input_gain_scales_both_signals_without_changing_pitch_or_velocity(self):
        times = np.arange(2048) / 48000
        results = []
        for gain in (0, 1, 4, 8):
            synth = MidiSynth()
            synth.handle(mido.Message('note_on', note=65, velocity=80))
            result = synth.render(times, Settings(midi_gain=gain))
            self.assertEqual(synth.voices[0].velocity, 80)
            results.append((result, synth.voices[0].phase))
        for result, phase in results:
            self.assertEqual(phase, results[1][1])
        for index, gain in enumerate((0, 1, 4, 8)):
            for signal, reference in zip(results[index][0], results[1][0]):
                np.testing.assert_allclose(signal, reference * gain, atol=1e-12)

    def test_velocity_zero_release_sustain_and_channel_isolation(self):
        synth = MidiSynth()
        synth.handle(mido.Message('note_on', channel=0, note=60, velocity=100))
        synth.handle(mido.Message('note_on', channel=1, note=60, velocity=50))
        synth.render(np.arange(1024) / 48000, Settings())
        synth.handle(mido.Message('control_change', channel=0, control=64, value=127))
        synth.handle(mido.Message('note_on', channel=0, note=60, velocity=0))
        self.assertFalse(synth.voices[0].held)
        self.assertIsNone(synth.voices[0].release_age)
        self.assertTrue(synth.voices[1].held)
        synth.handle(mido.Message('control_change', channel=0, control=64, value=0))
        self.assertIsNotNone(synth.voices[0].release_age)
        synth.render(np.arange(16000) / 48000, Settings())
        self.assertEqual([v.channel for v in synth.voices], [1])

    def test_sostenuto_latches_only_notes_already_held(self):
        synth = MidiSynth()
        synth.handle(mido.Message('note_on', note=60))
        synth.handle(mido.Message('control_change', control=66, value=127))
        synth.handle(mido.Message('note_on', note=64))
        synth.handle(mido.Message('note_off', note=60))
        synth.handle(mido.Message('note_off', note=64))
        self.assertIsNone(synth.voices[0].release_age)
        self.assertIsNotNone(synth.voices[1].release_age)
        synth.handle(mido.Message('control_change', control=66, value=0))
        self.assertIsNotNone(synth.voices[0].release_age)

    def test_voice_limit_filter_and_panic(self):
        synth = MidiSynth()
        synth.set_options('Pulse (long)', 1)
        self.assertFalse(synth.handle(mido.Message('note_on', channel=0, note=50)))
        for note in range(60, 70):
            synth.handle(mido.Message('note_on', channel=1, note=note))
        active = [v for v in synth.voices if v.stolen_age is None]
        self.assertEqual([v.note for v in active], list(range(62, 70)))
        self.assertEqual(synth.stolen, 2)
        # Stolen voices fade out briefly rather than stopping mid-cycle, then are removed.
        settings = Settings()
        synth.render(np.arange(480)/48000, settings)
        self.assertEqual(len(synth.voices), MAX_VOICES)
        synth.set_options('Sine (short)', 0)
        self.assertEqual(synth.voices, [])

    def test_stolen_voice_fades_without_a_jump(self):
        synth, settings = MidiSynth(), Settings()
        synth.set_options('Sine (long)', -1)
        for note in range(60, 68):
            synth.handle(mido.Message('note_on', note=note, velocity=127))
        played = synth.render(np.arange(4800)/48000, settings)[1][:, 0]
        normal_step = np.abs(np.diff(played[-480:])).max()
        synth.handle(mido.Message('note_on', note=80, velocity=127))
        after = synth.render(np.arange(4800, 4810)/48000, settings)[1][:, 0]
        # Removing a full-level voice at once jumps by several normal steps.
        self.assertLess(abs(after[0]-played[-1]), 1.5*normal_step)
        self.assertEqual(sum(v.stolen_age is None for v in synth.voices), MAX_VOICES)

    def test_continuous_phase_bend_and_octave_alternation(self):
        settings = Settings()
        def run(bend=0, mod=0):
            synth = MidiSynth()
            synth.handle(mido.Message('note_on', note=69, velocity=127))
            synth.handle(mido.Message('pitchwheel', pitch=bend))
            synth.handle(mido.Message('control_change', control=1, value=mod))
            synth.render(np.arange(1200) / 48000, settings)
            return synth
        # 25 ms = 11 cycles at A4; modulation first interval is one octave below.
        self.assertAlmostEqual(run().voices[0].phase, 0, places=10)
        self.assertAlmostEqual(run(mod=127).voices[0].phase, .5, places=10)
        self.assertAlmostEqual(run(bend=-8192).voices[0].phase, (11 * 2 ** (-2/12)) % 1, places=10)
        a, b = MidiSynth(), MidiSynth()
        for synth in (a, b):
            synth.handle(mido.Message('note_on', note=65, velocity=127))
        times = np.arange(4096) / 48000
        whole = a.render(times, settings)[1]
        chunks = np.concatenate([b.render(times[:1234], settings)[1], b.render(times[1234:], settings)[1]])
        np.testing.assert_allclose(whole, chunks, atol=1e-12)

    def test_presets_and_eight_voice_headroom(self):
        settings = Settings(shapes=(Shape(position_x=.5, shape_size=.5), Shape('Octahedron', -.5, 0, .5, 7)))
        for preset in PRESETS:
            synth = MidiSynth()
            synth.set_options(preset, -1)
            for note in range(60, 68):
                synth.handle(mido.Message('note_on', note=note, velocity=127))
            drawing, original, output = synth.render(np.arange(2048) / 48000, settings)
            self.assertTrue(np.isfinite(output).all())
            self.assertLessEqual(abs(output).max(), .95)
            self.assertGreater(abs(original).max(), .001)
            synth.panic()
            np.testing.assert_array_equal(synth.render(np.arange(512) / 48000, settings)[2], 0)


class RecordingTests(unittest.TestCase):
    def test_high_gain_clips_at_output_and_records_the_same_samples(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            player = AudioPreview()
            player.live = True
            settings = Settings(source=MIDI_SOURCE, midi_gain=16)
            player.parameters = (settings, 'mixed')
            player.midi.reset(48000)
            path = Path(folder) / 'loud.wav'
            player.midi.record(path)
            for _ in range(8):
                player.midi.receive(mido.Message('note_on', note=60, velocity=127))

            class Stream:
                def __init__(self):
                    self.blocks = []

                def write(self, block):
                    self.blocks.append(block.copy())
                    if len(self.blocks) == 3:
                        player.stop_event.set()
                    return False

                def close(self):
                    pass

            stream = Stream()
            player._run(stream)
            self.assertEqual(player.error, '')
            self.assertGreater(player.clipped_samples, 0)
            signal = np.concatenate(stream.blocks)
            self.assertEqual(abs(signal).max(), 1)
            with wave.open(str(path), 'rb') as wav:
                pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2').reshape(-1, 2)
            np.testing.assert_array_equal(pcm, np.rint(signal * 32767).astype('<i2'))
            metadata = json.loads(path.with_suffix('.json').read_text())
            self.assertEqual(metadata['changes'][0]['settings']['midi_gain'], 16)

    def test_worker_records_actual_output_midi_and_scene_and_finalizes_on_stop(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            path = Path(folder) / 'take.001.wav'
            player = AudioPreview()
            player.live = True
            settings = Settings(source=MIDI_SOURCE)
            player.parameters = (settings, 'mixed')
            player.midi.reset(48000)
            player.midi.record(path)
            player.midi.receive(mido.Message('note_on', note=69, velocity=100))

            class Stream:
                def __init__(self):
                    self.blocks = []
                    self.closed = False

                def write(self, block):
                    self.blocks.append(block.copy())
                    if len(self.blocks) == 2:
                        player.midi.receive(mido.Message('note_off', note=69))
                        player.parameters = (settings, 'music')
                    if len(self.blocks) == 4:
                        player.stop_event.set()
                    return False

                def close(self):
                    self.closed = True

            stream = Stream()
            player._run(stream)
            self.assertEqual(player.error, '')
            self.assertTrue(stream.closed)
            self.assertIsNone(player.midi.capture)
            with wave.open(str(path), 'rb') as wav:
                self.assertEqual(wav.getnframes(), 2048)
                pcm = np.frombuffer(wav.readframes(2048), dtype='<i2').reshape(-1, 2)
            expected = np.rint(np.concatenate(stream.blocks) * 32767).astype('<i2')
            np.testing.assert_array_equal(pcm, expected)
            midi = mido.MidiFile(str(Path(folder) / 'take.001.mid'))
            self.assertAlmostEqual(midi.length, 2048 / 48000, delta=.001)
            self.assertTrue(any(msg.type == 'note_off' and msg.note == 69 for msg in midi.tracks[0]))
            data = json.loads((Path(folder) / 'take.001.json').read_text())
            self.assertEqual(data['frames'], 2048)
            self.assertEqual([change['listening_mode'] for change in data['changes']], ['mixed', 'music'])

    def test_record_name_is_not_overwritten_and_panic_is_recorded(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            runtime = LiveMidi()
            runtime.reset(48000)
            settings = Settings(source=MIDI_SOURCE)
            path = Path(folder) / 'take.wav'
            path.write_bytes(b'existing')
            runtime.record(path)
            runtime.process(0, settings, 'mixed')
            self.assertIsNone(runtime.capture)
            self.assertEqual(path.read_bytes(), b'existing')
            runtime.record(Path(folder) / 'new.wav')
            runtime.process(0, settings, 'mixed')
            runtime.panic()
            runtime.process(0, settings, 'mixed')
            self.assertTrue(any(message.type == 'control_change' and message.control == 120 for _, message in runtime.capture.events))
            runtime.finish_record()


if __name__ == '__main__':
    unittest.main()
