import unittest
from playback_diagnostics import timing_stats, presentation_stats


class DiagnosticTests(unittest.TestCase):
    def test_fps_includes_time_without_presentations(self):
        # A stalled interval must reduce FPS even if the remaining intervals are fast.
        result = presentation_stats([1.,1.016,1.032],1.)
        self.assertEqual(result['fps'],3.)
        self.assertAlmostEqual(result['intervals_ms']['median'],16.)

    def test_empty_measurements_are_explicit(self):
        self.assertEqual(timing_stats([]),{})
        self.assertEqual(presentation_stats([],3.)['fps'],0.)
