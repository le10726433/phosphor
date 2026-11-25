"""Audio-only golden tests. Generate the initial archive with tools/golden_audio.py."""
from pathlib import Path
import unittest

import numpy as np

from tests.unit.golden_scenes import SCENES, snapshot, assert_snapshot


class GoldenAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).with_name('golden_audio.npz')
        if not path.exists():
            raise AssertionError('Missing golden_audio.npz: run python tools/golden_audio.py --update')
        cls.golden = np.load(path, allow_pickle=False)
        cls.addClassCleanup(cls.golden.close)


def make_test(name):
    def test(self):
        assert_snapshot(name, snapshot(name), self.golden)
    test.__name__ = f'test_{name}'
    return test


for _name in SCENES:
    setattr(GoldenAudioTests, f'test_{_name}', make_test(_name))
