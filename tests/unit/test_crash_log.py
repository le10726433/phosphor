"""Crash log: a native crash leaves every thread's Python stack and Qt's messages in
logs/phosphor.log, the previous session's log is kept, and diagnostic logging adds the
Qt window-buffer categories only when the preference asks for it."""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[2]

CHILD = textwrap.dedent('''
    import faulthandler, os, sys, threading, time
    sys.path.insert(0, {app!r})
    from phosphor.storage import crash_log
    crash_log.start()
    print('stdout reaches the log', flush=True)
    print('rules=' + os.environ.get('QT_LOGGING_RULES', ''), flush=True)
    from PySide6.QtCore import QCoreApplication, QTimer, qWarning
    app = QCoreApplication([])
    def worker():
        time.sleep(30)
    threading.Thread(target=worker, name='audio-like-worker', daemon=True).start()
    def crash():
        qWarning('a Qt warning before the crash')
        crash_log.note('about to crash')
        if {crash!r}: faulthandler._sigsegv()
        app.quit()
    QTimer.singleShot(50, crash)
    app.exec()
''')


def run_child(user, crash=True):
    env = dict(os.environ, PHOSPHOR_CRASH_LOG='1', PHOSPHOR_USER_DIR=str(user), QT_QPA_PLATFORM='offscreen')
    env.pop('QT_LOGGING_RULES', None)
    return subprocess.run([sys.executable, '-c', CHILD.format(app=str(APP), crash=crash)], env=env,
                          capture_output=True, text=True, timeout=60)


class CrashLogTests(unittest.TestCase):
    def setUp(self):
        self.user = Path(tempfile.mkdtemp())
        self.log = self.user/'logs'/'phosphor.log'

    def test_native_crash_records_python_stacks_and_qt_messages(self):
        result = run_child(self.user)
        self.assertNotEqual(result.returncode, 0)
        text = self.log.read_text(encoding='utf-8')
        self.assertIn('diagnostic logging off', text)
        self.assertIn('stdout reaches the log', text)
        self.assertIn('a Qt warning before the crash', text)  # Qt forced to stderr, i.e. the file.
        self.assertIn('phosphor: about to crash', text)
        self.assertIn('Fatal Python error: Segmentation fault', text)
        self.assertIn('Current thread', text)
        self.assertIn('crash', text.split('Fatal Python error')[1])  # The crashing Python frame.
        self.assertEqual(result.stderr, '')  # Everything went to the log, not the terminal.

    def test_previous_session_kept_and_diagnostics_follow_the_preference(self):
        run_child(self.user)
        first = self.log.read_text(encoding='utf-8')
        (self.user/'preferences.json').write_text(json.dumps({'diagnostic_logging': True}), encoding='utf-8')
        result = run_child(self.user, crash=False)
        self.assertEqual(result.returncode, 0, self.log.read_text(encoding='utf-8'))
        self.assertEqual((self.user/'logs'/'phosphor.previous.log').read_text(encoding='utf-8'), first)
        text = self.log.read_text(encoding='utf-8')
        self.assertIn('diagnostic logging on', text)
        self.assertIn('rules=qt.qpa.backingstore*=true', text)
        self.assertNotIn('Fatal Python error', text)

    def test_off_in_source_runs_without_the_variable(self):
        from phosphor.storage import crash_log
        if crash_log.FROZEN: self.skipTest('packaged build')
        previous = os.environ.pop('PHOSPHOR_CRASH_LOG', None)
        try: self.assertFalse(crash_log.wanted())
        finally:
            if previous is not None: os.environ['PHOSPHOR_CRASH_LOG'] = previous

    def test_preference_is_validated_and_kept_by_preferences_ok(self):
        from phosphor.storage.app_files import DEFAULT_PREFERENCES, validated_preferences
        self.assertIs(DEFAULT_PREFERENCES['diagnostic_logging'], False)
        self.assertIs(validated_preferences(dict(DEFAULT_PREFERENCES, diagnostic_logging='yes'))['diagnostic_logging'], False)
        older = {k: v for k, v in DEFAULT_PREFERENCES.items() if k != 'diagnostic_logging'}
        self.assertIs(validated_preferences(older)['diagnostic_logging'], False)
        from phosphor.storage import crash_log
        path = self.user/'preferences.json'
        path.write_text(json.dumps(dict(DEFAULT_PREFERENCES, diagnostic_logging=True)), encoding='utf-8')
        self.assertTrue(crash_log.diagnostics_requested(path))
        path.write_text('not json', encoding='utf-8')
        self.assertFalse(crash_log.diagnostics_requested(path))


if __name__ == '__main__':
    unittest.main()
