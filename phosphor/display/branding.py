"""Shared palette artwork; paths and scene identifiers remain backward compatible."""
from phosphor.storage.app_paths import ASSETS_DIR

APP_NAME = 'phosphor'
LOGOS = {
    'Green monochrome': 'logo_green.png',
    'Amber': 'logo_amber.png',
    'Cyan': 'logo_cyan.png',
    'OLED white': 'logo_white.png',
}

def logo_path(palette):
    return ASSETS_DIR / 'logos' / LOGOS[palette]
