from pathlib import Path
import tempfile
import unittest
import numpy as np
from phosphor.storage.app_files import DEFAULT_PREFERENCES, validated_preferences, atomic_json, read_preferences
from phosphor.display.trace_interpolation import interpolate_trace


class InterpolationTests(unittest.TestCase):
    def test_off_and_short_traces_preserve_identity(self):
        for count in (0,1,20):
            points = np.zeros((count,2),dtype=np.float32)
            self.assertIs(interpolate_trace(points,'Off'),points)
            if count < 2: self.assertIs(interpolate_trace(points,'Cubic 8x'),points)

    def test_original_samples_endpoints_and_buffer_are_preserved(self):
        points = np.array([[0,0],[1,.4],[-.3,1.2],[.2,0]],dtype=np.float32)
        before = points.copy()
        for factor in (4,8):
            result = interpolate_trace(points,f'Cubic {factor}x')
            self.assertEqual(len(result),(len(points)-1)*factor+1)
            np.testing.assert_array_equal(result[::factor],points)
            np.testing.assert_array_equal(points,before)
            self.assertTrue(np.isfinite(result).all())
            self.assertFalse(np.shares_memory(result,points))

    def test_cubic_reduces_sine_reconstruction_error(self):
        t = np.arange(64,dtype=np.float32)
        points = np.column_stack((np.sin(t*.3),np.cos(t*.3)))
        result = interpolate_trace(points,'Cubic 4x')
        dense = np.arange(len(result))/4
        expected = np.column_stack((np.sin(dense*.3),np.cos(dense*.3)))
        linear = np.column_stack([np.interp(dense,t,points[:,axis]) for axis in (0,1)])
        self.assertLess(np.mean((result[4:-4]-expected[4:-4])**2),np.mean((linear[4:-4]-expected[4:-4])**2))

    def test_preference_migration_persistence_and_invalid_values(self):
        old = {key:value for key,value in DEFAULT_PREFERENCES.items() if key != 'trace_interpolation'}
        self.assertEqual(validated_preferences(old),DEFAULT_PREFERENCES)
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            path = Path(folder)/'preferences.json'
            expected = dict(DEFAULT_PREFERENCES,trace_interpolation='Cubic 8x')
            atomic_json(path,expected)
            self.assertEqual(read_preferences(path),expected)
        for invalid in ('Unknown',True,[],None):
            with self.assertRaises(ValueError): validated_preferences(dict(DEFAULT_PREFERENCES,trace_interpolation=invalid))


if __name__ == '__main__': unittest.main()
