"""Bloom/halo downsample factors fit the shader loop; the display never fakes a halo."""
import re
import unittest

from phosphor.display.phosphor import BLOOM_DOWN_MAX_FACTOR, bloom_uniforms, halo_uniforms
from phosphor.storage.app_paths import ASSETS_DIR

SHADERS = ASSETS_DIR / 'shaders'


def shader_text(name):
    return (SHADERS / name).read_text(encoding='utf-8')


def code_only(text):
    return re.sub(r'//[^\n]*', '', text)


class DownsampleFactorLimitTests(unittest.TestCase):
    def test_shader_limit_matches_python(self):
        found = re.findall(r'const\s+int\s+MAX_FACTOR\s*=\s*(\d+)\s*;', shader_text('phosphor_bloom_down.frag'))
        self.assertEqual([int(v) for v in found], [BLOOM_DOWN_MAX_FACTOR])

    def test_every_chosen_factor_fits_the_shader_loop(self):
        factors = set()
        for ratio in (1., 1.25, 1.5, 2., 3.):
            for width, height in ((200, 150), (636, 636), (1000, 700), (1920, 1080), (3840, 2160)):
                for glow in (.5, 1., 2., 4., 8., 16., 40., 100., 400.):
                    u = bloom_uniforms(True, 2., glow, 1., ratio, width, height)
                    factors.add(int(u['bloomFactor']))
                factors.add(int(halo_uniforms(True, width, height, ratio)['haloFactor']))
        self.assertIn(32, factors)  # The widest glow reaches the largest factor.
        self.assertIn(12, factors)  # The default halo factor on scopes of 512 px or more.
        for factor in factors:
            self.assertLessEqual(factor, BLOOM_DOWN_MAX_FACTOR, factor)
            self.assertEqual(factor, float(factor))

    def test_average_divides_by_the_taps_summed(self):
        text = code_only(shader_text('phosphor_bloom_down.frag'))
        self.assertIn('float count = factor * factor;', text)
        self.assertEqual(text.count('i < MAX_FACTOR'), 1)
        self.assertEqual(text.count('j < MAX_FACTOR'), 1)


class DisplayHaloTests(unittest.TestCase):
    def test_no_offset_bloom_fallback(self):
        text = code_only(shader_text('phosphor_display.frag'))
        self.assertIn('haloTexOn > 0.5', text)
        self.assertNotIn('0.025', text)
        # Bloom is sampled once, at the pixel itself: offset taps draw ghost lines.
        self.assertEqual(re.findall(r'texture\(bloom,[^;]*;', text), ['texture(bloom, qt_TexCoord0);'])
        self.assertEqual(re.findall(r'texture\(halo,[^;]*;', text), ['texture(halo, qt_TexCoord0);'])


if __name__ == '__main__':
    unittest.main()
