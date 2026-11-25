"""Coordinated CRT palettes for the editor and scope."""

MACOS_GRAPHITE = 'macOS Graphite'

PALETTES = {
    'OLED white': dict(bg='#000000', field='#0d0d0d', groove='#676767', active='#202020', fg='#ffffff',
        accent='#ffffff', muted='#a0a0a0', grid='#4b4b4b', border='#818181', background='#000000', trace='#ffffff'),
    'Green monochrome': dict(bg='#06110c', field='#13271b', active='#123a22', fg='#c1f1ca',
        accent='#76ff9b', muted='#6a9b77', grid='#345742', border='#5c916a', background='#020a06', trace='#81ffa5'),
    'Amber': dict(bg='#130f07', field='#2c2313', active='#3a2b0f', fg='#ffe5b4',
        accent='#ffbd59', muted='#a98b55', grid='#58482e', border='#987e51', background='#090703', trace='#ffd486'),
    'Cyan': dict(bg='#051015', field='#132b32', active='#123d48', fg='#c5f5ff',
        accent='#5de6ff', muted='#5e9caa', grid='#325560', border='#558a98', background='#02090e', trace='#6eeeff'),
    MACOS_GRAPHITE: dict(bg='#262628', field='#3c3c3f', groove='#717175', active='#38465a', fg='#e1e1e5',
        accent='#a5c9f4', muted='#ababaf', grid='#4d4f54', border='#79797d', background='#1a1b1e', trace='#c8d5e4'),
}


def mix(a, b, t):
    """Blend hex colour `a` toward `b` by `t` (0..1)."""
    a, b = (tuple(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in (a, b))
    return '#' + ''.join(f'{round(x + (y - x) * t):02x}' for x, y in zip(a, b))


def button_colors(p):
    """Framed tool buttons (contrast option A, 0.67.2): fill, border, and the pressed fill."""
    return dict(fill=mix(p['field'], p['fg'], .06), frame=mix(p['border'], p['fg'], .25),
                pressed=mix(p['active'], p['accent'], .25))
