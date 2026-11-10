"""Run shared engine/file tests without opening the retained Tk desktop."""
import unittest


def core_tests(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from core_tests(test)
        elif not test.id().startswith(('test_desktop.DesktopTests.', 'test_viewport.ViewportTests.')):
            yield test


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.discover('.', pattern='test_*.py')
    result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(core_tests(suite)))
    raise SystemExit(not result.wasSuccessful())
