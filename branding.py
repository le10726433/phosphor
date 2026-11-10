"""Shared palette artwork; paths and scene identifiers remain backward compatible."""
from pathlib import Path

APP_NAME = 'phosphor'
LOGOS = {
    'Green monochrome': 'logo_green.png',
    'Amber': 'logo_amber.png',
    'Cyan': 'logo_cyan.png',
    'OLED white': 'logo_white.png',
}

def logo_path(palette):
    return Path(__file__).resolve().parent / 'assets' / 'logos' / LOGOS[palette]
