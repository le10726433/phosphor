"""Makes the phosphor package importable when a script in this folder is run directly."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import phosphor  # noqa: E402,F401  (sets up the local dependency folders)
