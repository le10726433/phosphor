"""Makes the phosphor package importable when a script in this folder is run directly.

It also gives each smoke a fresh, empty user folder (PHOSPHOR_USER_DIR), unless one
is already set: smokes then start from default preferences, and nothing they save
(including a delayed preferences write after a patched block ends) reaches the
user's own .user files.
"""
import atexit
import os
from pathlib import Path
import shutil
import sys
import tempfile

@atexit.register
def check_guarded_paints():
    guard = sys.modules.get('phosphor.editor.paint_guard')
    if guard and guard._reported:
        print('Smoke failed after a guarded paint exception (traceback above).', file=sys.stderr, flush=True)
        sys.stdout.flush()
        os._exit(1)  # Raising from atexit would leave a successful exit code.


if not os.environ.get('PHOSPHOR_USER_DIR'):
    _user = tempfile.mkdtemp(prefix='phosphor-smoke-user-')
    os.environ['PHOSPHOR_USER_DIR'] = _user
    atexit.register(shutil.rmtree, _user, True)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import phosphor  # noqa: E402,F401  (sets up the local dependency folders)
