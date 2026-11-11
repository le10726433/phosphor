"""The app must exit cleanly after the shape builder was used, however it is closed.

Runs the real qt_app.main() in a child process and checks the exit code: the crash this
guards against happens during interpreter shutdown, after every assertion has passed.
"""
import _path  # noqa: F401  (makes the phosphor package importable)
import subprocess
import sys

CASES = ('never opened', 'left builder', 'closed inside builder', 'application quit')


def child(case):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from phosphor.editor import qt_app as q
    original = q.Window

    class Scripted(original):
        def __init__(self, options):
            super().__init__(options)
            QTimer.singleShot(800, self.enter)

        def enter(self):
            if case != 'never opened':
                self.open_builder()
            QTimer.singleShot(500, self.leave)

        def leave(self):
            if case == 'left builder':
                assert self.leave_builder()
            QTimer.singleShot(300, QApplication.quit if case == 'application quit' else self.close)

    q.Window = Scripted
    sys.argv = ['app.py']
    return q.main()


if __name__ == '__main__':
    if len(sys.argv) > 2 and sys.argv[1] == '--child':
        sys.exit(child(sys.argv[2]))
    failures = []
    for case in CASES:
        result = subprocess.run([sys.executable, __file__, '--child', case], capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            failures.append(f'{case}: exit {result.returncode}\n{result.stderr[-800:]}')
    assert not failures, '\n'.join(failures)
    print('Builder quit passed: clean exit codes with the builder unused, left, still open and on application quit.')
