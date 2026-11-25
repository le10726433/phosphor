"""Physical wheel modifiers; Qt is imported only when resolving an event."""
import sys

ACTIONS = ('wheel_scroll', 'wheel_zoom', 'wheel_height', 'wheel_builder_zoom')
LABELS = ('Scroll time', 'Zoom time', 'Row / block height', 'Builder zoom')


def defaults(platform=None):
    mac = (platform or sys.platform) == 'darwin'
    return dict(zip(ACTIONS, ('shift', 'control', 'command' if mac else 'alt', 'none')))


def choices(platform=None):
    return ('none', 'shift', 'control', 'alt') + (('command',) if (platform or sys.platform) == 'darwin' else ())


def physical_label(key, platform=None):
    mac = (platform or sys.platform) == 'darwin'
    return {'none': 'None', 'shift': 'Shift', 'control': 'Control' if mac else 'Ctrl',
            'alt': 'Option' if mac else 'Alt', 'command': 'Command'}[key]


def validated_mapping(values, platform=None):
    result = defaults(platform)
    result.update((key, values[key]) for key in ACTIONS if key in values)
    for value in result.values():
        if not isinstance(value, str) or value not in choices(platform):
            raise ValueError('Select a wheel modifier available on this platform.')
    if len(set(result[key] for key in ACTIONS[:3])) != 3:
        raise ValueError('Composer wheel actions must use different modifiers. Choose an unused modifier first.')
    return result


def qt_modifier(key, platform=None):
    from PySide6.QtCore import Qt
    mac = (platform or sys.platform) == 'darwin'
    return {'none': Qt.KeyboardModifier.NoModifier, 'shift': Qt.KeyboardModifier.ShiftModifier,
            'alt': Qt.KeyboardModifier.AltModifier,
            'control': Qt.KeyboardModifier.MetaModifier if mac else Qt.KeyboardModifier.ControlModifier,
            'command': Qt.KeyboardModifier.ControlModifier if mac else Qt.KeyboardModifier.MetaModifier}[key]


def matches(modifiers, key, platform=None, unmodified_any=False):
    # Builder's original mapping accepts wheel with any held keys.
    if key == 'none':
        return unmodified_any or not modifiers
    return bool(modifiers & qt_modifier(key, platform))


def gesture(key, platform=None):
    return 'Wheel' if key == 'none' else physical_label(key, platform) + '+wheel'
