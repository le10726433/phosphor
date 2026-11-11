from pathlib import Path
import tempfile
import unittest
from phosphor.storage.app_files import DEFAULT_PREFERENCES, validated_preferences, atomic_json, read_preferences
from phosphor.display.glow_trail import GlowTrail, VISUAL_DEFAULTS


class GlowTests(unittest.TestCase):
    def test_wall_clock_fade_expires_without_new_emission(self):
        trail = GlowTrail()
        slot,weights = trail.update(0,2)
        self.assertEqual(weights[slot],1)
        _,weights = trail.update(.1,2)
        self.assertAlmostEqual(weights[slot],.95**2)
        _,weights = trail.update(2.1,2)
        self.assertEqual(weights[slot],0)

    def test_history_is_bounded_and_disable_clears_immediately(self):
        trail = GlowTrail()
        for i in range(100): trail.update(i*.04,.5)
        self.assertEqual(len(trail.stamps),GlowTrail.CAPACITY)
        for enabled,duration in ((False,1),(True,0)):
            slot,weights = trail.update(4,duration,enabled)
            self.assertIsNone(slot); self.assertFalse(any(weights))
            self.assertTrue(all(stamp is None for stamp in trail.stamps))

    def test_preference_migration_roundtrip_and_invalid_ranges(self):
        old = {key:value for key,value in DEFAULT_PREFERENCES.items() if key not in VISUAL_DEFAULTS}
        self.assertEqual(validated_preferences(old),DEFAULT_PREFERENCES)
        expected = dict(DEFAULT_PREFERENCES,trace_thickness=2.5,glow_size=12.,glow_intensity=2.,glow_decay=.8)
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as folder:
            path = Path(folder)/'prefs.json'; atomic_json(path,expected)
            self.assertEqual(read_preferences(path),expected)
        for key in VISUAL_DEFAULTS:
            for invalid in (True,'1',float('nan'),float('inf'),-1,1000):
                with self.assertRaises(ValueError): validated_preferences(dict(DEFAULT_PREFERENCES,**{key:invalid}))


if __name__ == '__main__': unittest.main()
