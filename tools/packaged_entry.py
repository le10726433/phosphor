"""PyInstaller entry point: a packaged app is the Qt editor only."""
import sys

from phosphor.editor.qt_app import main

sys.exit(main())
