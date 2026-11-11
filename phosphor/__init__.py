"""phosphor: turns scenes and music into stereo X/Y signals for an oscilloscope.

Importing the package makes this checkout's local dependency folders importable,
so every entry point (app.py, tools, tests) sets them up the same way.
"""
from pathlib import Path
import sys

for _name in ('.audio-deps', '.qt-deps'):
    _folder = Path(__file__).resolve().parent.parent/_name
    if _folder.is_dir() and str(_folder) not in sys.path:
        sys.path.append(str(_folder))
