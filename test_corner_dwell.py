from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
import numpy as np
from corner_dwell import corner_positions,dwell_phase,MAX_CYCLE_FRACTION
from signal_engine import Settings,Shape,render,planned_path,prepare_audio,export_wav
from signal_preferences import validated_signal_preferences,read_signal_preferences
from app_files import atomic_json,scene_document
from midi_synth import MidiSynth
from live_midi import mido_module


class CornerDwellTests(unittest.TestCase):
    def test_holds_at_corners_and_caps_total_cycle_without_shifting_wraps(self):
        phase=np.arange(4000)/4000
        corners=np.array([0.,.25,.5,.75])
        warped=dwell_phase(phase,1000.,corners,500.)
        held=np.any(np.isclose(warped[:,None],corners[None,:],atol=1e-12),axis=1)
        self.assertAlmostEqual(float(np.mean(held)),MAX_CYCLE_FRACTION,delta=.002)
        self.assertTrue(np.all(np.diff(warped)>=-1e-12))
        for cycle in range(4):
            np.testing.assert_allclose(dwell_phase(phase+cycle,1000,corners,500),warped,atol=1e-12)
        self.assertGreater(warped[-1],.999)
        np.testing.assert_array_equal(dwell_phase(phase,1000,corners,0),phase)

    def test_curves_do_not_dwell_at_every_tessellation_point(self):
        for solid in ('Tilting circle','Octant sphere','Square (2D)'):
            path=planned_path(0,60,(Shape(solid,rotation_seconds=None),),'No changes',8)
            count=len(corner_positions(*path))
            if solid=='Tilting circle':self.assertEqual(count,0)
            elif solid=='Square (2D)':self.assertEqual(count,4)
            else:self.assertLess(count,30)

    def test_generated_and_prepared_audio_remain_chunk_independent(self):
        times=np.arange(9000)/48000
        import tempfile
        from test_score_source import write_midi
        folder=tempfile.TemporaryDirectory(); self.addCleanup(folder.cleanup)
        tune=write_midi(Path(folder.name)/'tune.mid')
        for source in ('C-major scale','MIDI file'):
            base=Settings(source=source,midi_file=tune,shapes=(Shape('Square (2D)',rotation_seconds=None),))
            dwell=replace(base,corner_dwell_us=80.)
            full=render(times,dwell)
            chunks=np.concatenate([render(t,dwell)[2] for t in np.array_split(times,7)])
            np.testing.assert_allclose(full[2],chunks,atol=1e-13)
            np.testing.assert_array_equal(full[1],render(times,base)[1])
            self.assertGreater(np.max(abs(full[0]-render(times,base)[0])),.001)
        music=np.column_stack([np.sin(times*2*np.pi*220)]*2)
        prepared=prepare_audio(music)
        full=render(times,dwell,prepared)[2]
        chunks=np.concatenate([render(t,dwell,prepared)[2] for t in np.array_split(times,7)])
        np.testing.assert_allclose(full,chunks,atol=1e-13)

    def test_midi_phase_and_original_timbre_unchanged(self):
        mido=mido_module();times=np.arange(4096)/48000
        a,b=MidiSynth(),MidiSynth()
        for synth in (a,b):synth.handle(mido.Message('note_on',note=69,velocity=100))
        base=Settings();dwell=replace(base,corner_dwell_us=80.)
        first=a.render(times,base);second=b.render(times,dwell)
        np.testing.assert_array_equal(first[1],second[1])
        self.assertEqual(a.voices[0].phase,b.voices[0].phase)
        self.assertGreater(np.max(abs(first[0]-second[0])),.001)
        c=MidiSynth();c.handle(mido.Message('note_on',note=69,velocity=100))
        chunks=np.concatenate([c.render(t,dwell)[2] for t in np.array_split(times,5)])
        np.testing.assert_allclose(chunks,second[2],atol=1e-12)

    def test_hardware_profile_is_separate_and_source_wav_unmodified(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'signal.json'
            from signal_preferences import DEFAULT_SIGNAL_PREFERENCES
            self.assertEqual(read_signal_preferences(path),DEFAULT_SIGNAL_PREFERENCES)
            atomic_json(path,dict(corner_dwell_us=80.))
            self.assertEqual(read_signal_preferences(path)['corner_dwell_us'],80.)
            a,b=Path(folder)/'a.wav',Path(folder)/'b.wav'
            base=Settings();dwell=replace(base,corner_dwell_us=80.)
            export_wav(a,base,.05,source_only=True);export_wav(b,dwell,.05,source_only=True)
            self.assertEqual(a.read_bytes(),b.read_bytes())
            self.assertEqual(scene_document(base,'mixed',''),scene_document(dwell,'mixed',''))
        for value in (-1,501,float('nan'),True):
            with self.assertRaises(ValueError):validated_signal_preferences(dict(corner_dwell_us=value))
