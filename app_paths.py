"""Where the app reads bundled resources and writes per-user data.

A source checkout keeps its writable data in `.user` beside the code. A packaged
macOS app bundle is read-only (and its signature must not change), so the frozen
app writes to ~/Library/Application Support/phosphor instead.
"""
from pathlib import Path
import os
import sys

FROZEN = bool(getattr(sys, 'frozen', False))
APP_DIR = Path(__file__).resolve().parent
USER_DIRECTORY_NAME = 'phosphor'


def user_dir():
    override = os.environ.get('PHOSPHOR_USER_DIR')
    if override: return Path(override).expanduser().resolve()
    if not FROZEN: return APP_DIR/'.user'
    if sys.platform == 'darwin': return Path.home()/'Library'/'Application Support'/USER_DIRECTORY_NAME
    base = os.environ.get('APPDATA') or os.environ.get('XDG_DATA_HOME') or str(Path.home()/'.local'/'share')
    return Path(base)/USER_DIRECTORY_NAME


USER_DIR = user_dir()


def relaunch_command(*arguments):
    """Command line that starts a fresh instance of this same app."""
    if FROZEN: return [sys.executable, *arguments]
    return [sys.executable, str(APP_DIR/'qt_app.py'), *arguments]
