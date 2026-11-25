"""Fast core check: the unit tests that run without Qt, minus the slow kernel suites.

    python tools/check_core.py          # Qt-free tests without the slow kernel suites
    python tools/check_core.py --slow   # also test_mesh_simplify and test_recurrence_jit

The full unit suite (`python -m unittest discover -s tests/unit -t .`) stays the release
check. This is the quick one for engine, file, audio and Qt-free editor-state changes
(audit B7: it used to repeat the full discovery). PySide6 and shiboken6 cannot be
imported here, so the result also shows the Qt-free tests pass on a machine without Qt
(the shape of a CI job). A test module or test that needs Qt is skipped and counted;
any other import error still fails.
"""
import _path  # noqa: F401  (makes the phosphor package importable)
import importlib.abc
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
QT = ('PySide6', 'shiboken6')
# Numba kernel suites: about 100 of the full suite's 170 s on the Windows workstation.
SLOW = ('test_mesh_simplify', 'test_recurrence_jit')


BLOCKED = 'check_core runs without Qt'


class BlockQt(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in QT:
            raise ModuleNotFoundError(f'No module named {name!r} ({BLOCKED})', name=name)
        return None


def needs_qt(error):
    """Our own block, raised directly or (for a module that failed to import) in unittest's
    ImportError, whose message is the original traceback."""
    return isinstance(error, ImportError) and BLOCKED in str(error)


class CoreResult(unittest.TextTestResult):
    """A test that imports Qt inside its body is skipped, not an error."""
    def addError(self, test, err):
        if needs_qt(err[1]): self.addSkip(test, 'needs Qt')
        else: super().addError(test, err)


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite): yield from flatten(item)
        else: yield item


def core_suite(slow=False):
    """(suite, modules skipped for Qt): discovery with Qt blocked."""
    for name in list(sys.modules):
        if name.split('.')[0] in QT: del sys.modules[name]
    sys.meta_path.insert(0, BlockQt())
    found = unittest.defaultTestLoader.discover(str(ROOT/'tests'/'unit'), pattern='test_*.py', top_level_dir=str(ROOT))
    suite, qt_modules = unittest.TestSuite(), []
    for test in flatten(found):
        error = getattr(test, '_exception', None)  # unittest.loader._FailedTest: the module did not import.
        module = test.id().split('.')[-1] if error is not None else test.id().split('.')[-3]
        if error is not None and needs_qt(error): qt_modules.append(module); continue
        if not slow and module in SLOW: continue
        suite.addTest(test)
    return suite, sorted(qt_modules)


if __name__ == '__main__':
    slow = '--slow' in sys.argv
    suite, qt_modules = core_suite(slow)
    print(f'check_core: {len(qt_modules)} modules need Qt and are left to the full suite ({", ".join(qt_modules)})'
          + ('' if slow else f'; slow suites left out: {", ".join(SLOW)} (--slow runs them)'), flush=True)
    result = unittest.TextTestRunner(verbosity=1, resultclass=CoreResult).run(suite)
    raise SystemExit(not result.wasSuccessful())
