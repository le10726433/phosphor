"""Run shared engine/file tests without opening the retained Tk desktop."""
import _path  # noqa: F401  (makes the phosphor package importable)
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent


def core_tests(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from core_tests(test)
        elif not test.id().startswith(('tests.unit.test_desktop.DesktopTests.', 'tests.unit.test_viewport.ViewportTests.')):
            yield test


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.discover(str(ROOT/'tests'/'unit'), pattern='test_*.py', top_level_dir=str(ROOT))
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(core_tests(suite)))
    raise SystemExit(not result.wasSuccessful())
