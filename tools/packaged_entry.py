"""PyInstaller entry point: a packaged app is the Qt editor only."""
import sys

# Before Qt loads: the crash log and its Qt logging settings must be in place first.
from phosphor.storage import crash_log
crash_log.start()

from phosphor.editor.qt_app import main  # noqa: E402

sys.exit(main())
