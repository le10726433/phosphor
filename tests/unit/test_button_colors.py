import unittest

from phosphor.display.crt_theme import PALETTES, button_colors, mix


def luminance(colour):
    def channel(v):
        v /= 255
        return v / 12.92 if v <= .03928 else ((v + .055) / 1.055) ** 2.4
    r, g, b = (int(colour[i:i + 2], 16) for i in (1, 3, 5))
    return .2126 * channel(r) + .7152 * channel(g) + .0722 * channel(b)


def contrast(a, b):
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + .05) / (low + .05)


class ButtonColorsTest(unittest.TestCase):
    def test_mix_endpoints(self):
        self.assertEqual(mix('#000000', '#ffffff', 0), '#000000')
        self.assertEqual(mix('#000000', '#ffffff', 1), '#ffffff')
        self.assertEqual(mix('#102030', '#304050', .5), '#203040')

    def test_buttons_stand_out_in_every_palette(self):
        # 0.67.2: Composer tool buttons were hard to see and pressed looked like unpressed.
        for name, p in PALETTES.items():
            with self.subTest(palette=name):
                b = button_colors(p)
                self.assertGreater(contrast(b['frame'], p['bg']), 2.5)
                self.assertGreater(contrast(p['fg'], b['fill']), 7)
                self.assertGreater(contrast(p['accent'], b['pressed']), 3)  # Bold text.
                self.assertGreater(contrast(b['pressed'], b['fill']), 1.5)


if __name__ == '__main__':
    unittest.main()
