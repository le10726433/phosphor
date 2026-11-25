"""The Q1 stress-case generators (tools/q1_workloads.py) build valid projects of the stated sizes.

No timings here: bench_performance and measure_pacing measure these workloads.
"""
import tempfile
import unittest
from collections import Counter

from phosphor.engine.composition import validate_project
from tools import q1_workloads as q1


class Q1Workloads(unittest.TestCase):
    def test_dense_song(self):
        for quick, count, beats in ((True, 2000, 96.), (False, 20000, 960.)):
            project = q1.dense_song(quick)
            validate_project(project)
            self.assertEqual(len(project.notes), count)
            self.assertTrue(project.chords and project.velocity)
            self.assertAlmostEqual(project.clips[0].length, max(n.end for n in project.notes))
            self.assertLess(abs(project.end_beats - beats), .05 * beats)  # About 8 minutes (48 s).
            sizes = Counter(Counter(n.start for n in project.notes).values())
            self.assertTrue({3, 4} <= set(sizes))
            self.assertGreater(len({n.velocity for n in project.notes}), 100)
            self.assertEqual(project, q1.dense_song(quick))  # Seeded.

    def test_complex_scene_variants(self):
        for quick, objects in ((True, 6), (False, 24)):
            variants = {a: q1.complex_project(quick, a) for a in q1.AUTOMATION}
            for automation, project in variants.items():
                validate_project(project)
                shapes = project.clips[0].settings.shapes
                self.assertEqual(len(shapes), objects)
                self.assertEqual({s.solid for s in shapes} >= {'Text'}, True)
                self.assertTrue(all(len(s.transforms) == 5 and s.transform_variables for s in shapes))
                automated = any(p.points for s in shapes for t in s.transforms for p in t.params)
                self.assertEqual(automated, automation == 'all')
                self.assertEqual(bool(project.tempo_changes), automation == 'all')
                self.assertEqual(all(s.path_start.expression for s in shapes), automation == 'path')
            self.assertEqual(len({s.solid for s in variants['static'].clips[0].settings.shapes}), 6)
            self.assertTrue(all(s.path_start.points and s.path_trace.points and s.lanes
                                for s in variants['all'].clips[0].settings.shapes))
        combined = q1.dense_song(True, q1.complex_project(True, 'all', q1.dense_beats(True)))
        validate_project(combined)
        self.assertEqual(len(combined.notes), 2000)

    def test_audio_project(self):
        with tempfile.TemporaryDirectory() as folder:
            project = q1.audio_project(folder, True)
            validate_project(project)
            self.assertEqual(len(project.audio_tracks), 4)
            self.assertEqual(len(project.audio_clips), 12)
            self.assertTrue(all(2 <= len(t.effects) <= 3 and t.pan_points and all(e.lanes for e in t.effects)
                                for t in project.audio_tracks))
            self.assertTrue(all(c.gain_points for c in project.audio_clips))


if __name__ == '__main__':
    unittest.main()
