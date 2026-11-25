"""Ordered favourite folders, including temporarily disconnected volumes."""
import json
import os
from pathlib import Path

from phosphor.storage.app_paths import USER_DIR
from phosphor.storage.recent_files import _write_json

BROWSER_PATH = USER_DIR / 'browser.json'
MAX_FAVOURITES = 50


def read_favourites(path=BROWSER_PATH) -> list[Path]:
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as error:
        raise ValueError('Cannot read browser favourites.') from error
    if not isinstance(data, dict) or data.get('format') != 'phosphor-browser':
        raise ValueError('Unknown browser file format.')
    if type(data.get('version')) is not int or data['version'] != 1:
        raise ValueError('Unsupported browser file version.')
    if not isinstance(data.get('favourites'), list):
        raise ValueError('Invalid browser favourites list.')
    return [Path(item) for item in data['favourites'] if isinstance(item, str)]


def _key(folder):
    # abspath, not resolve: a favourite on an offline share must never stall the GUI.
    return os.path.normcase(os.path.abspath(str(Path(folder).expanduser())))


def _save(folders, path):
    _write_json({'format': 'phosphor-browser', 'version': 1,
                 'favourites': [str(folder) for folder in folders]}, path)


def add_favourite(folder, path=BROWSER_PATH):
    folders = read_favourites(path)
    folder = Path(os.path.abspath(str(Path(folder).expanduser())))
    key = os.path.normcase(str(folder))
    if any(_key(item) == key for item in folders):
        return
    if len(folders) >= MAX_FAVOURITES:
        raise ValueError(f'At most {MAX_FAVOURITES} favourite folders.')
    _save([*folders, folder], path)


def remove_favourite(folder, path=BROWSER_PATH):
    folders = read_favourites(path)
    key = _key(folder)
    remaining = [item for item in folders if _key(item) != key]
    if len(remaining) != len(folders):
        _save(remaining, path)
