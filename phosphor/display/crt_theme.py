"""Coordinated CRT palettes and a privately bundled JetBrains Mono font."""
from phosphor.storage.app_paths import ASSETS_DIR
import sys

PALETTES = {
    'OLED white': dict(bg='#000000', field='#000000', active='#202020', fg='#ffffff',
        accent='#ffffff', muted='#a0a0a0', grid='#242424', border='#505050', background='#000000', trace='#ffffff'),
    'Green monochrome': dict(bg='#06110c', field='#0a1c12', active='#123a22', fg='#c1f1ca',
        accent='#76ff9b', muted='#6a9b77', grid='#153524', border='#356b45', background='#020a06', trace='#81ffa5'),
    'Amber': dict(bg='#130f07', field='#21190b', active='#3a2b0f', fg='#ffe5b4',
        accent='#ffbd59', muted='#a98b55', grid='#332511', border='#70562b', background='#090703', trace='#ffd486'),
    'Cyan': dict(bg='#051015', field='#0a2027', active='#123d48', fg='#c5f5ff',
        accent='#5de6ff', muted='#5e9caa', grid='#12323d', border='#2a6170', background='#02090e', trace='#6eeeff'),
}
_fonts_loaded = False
_font_buffers = []
_font_handles = []


def blend(first, second, weight):
    a = [int(first[i:i+2], 16) for i in (1, 3, 5)]
    b = [int(second[i:i+2], 16) for i in (1, 3, 5)]
    return '#' + ''.join(f'{round(x*(1-weight)+y*weight):02x}' for x, y in zip(a, b))


def prepare_fonts(root):
    import tkinter.font as tkfont
    global _fonts_loaded
    if sys.platform == 'win32' and not _fonts_loaded:
        import ctypes
        from ctypes import wintypes
        add = ctypes.windll.gdi32.AddFontMemResourceEx
        add.argtypes = [wintypes.LPVOID, wintypes.DWORD, wintypes.LPVOID, ctypes.POINTER(wintypes.DWORD)]
        add.restype = wintypes.HANDLE
        directory = ASSETS_DIR / 'fonts'
        for name in ('JetBrainsMono-Regular.ttf', 'JetBrainsMono-Bold.ttf'):
            buffer = ctypes.create_string_buffer((directory / name).read_bytes())
            count = wintypes.DWORD()
            handle = add(buffer, len(buffer)-1, None, ctypes.byref(count))
            if handle:
                _font_buffers.append(buffer)
                _font_handles.append(handle)
        _fonts_loaded = True
    families = tkfont.families(root)
    # Memory-loaded Windows fonts are private and not returned by enumeration.
    probe = tkfont.Font(root=root, family='JetBrains Mono', size=10)
    family = 'JetBrains Mono' if probe.actual('family') == 'JetBrains Mono' else ('Consolas' if 'Consolas' in families else 'DejaVu Sans Mono')
    for name in ('TkDefaultFont', 'TkTextFont', 'TkFixedFont', 'TkMenuFont', 'TkHeadingFont', 'TkSmallCaptionFont'):
        font = tkfont.nametofont(name, root=root)
        font.configure(family=family, size=10, weight='bold' if name == 'TkHeadingFont' else 'normal')
    return family
