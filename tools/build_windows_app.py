"""Package the Qt editor as a portable Windows folder and zip.

    python -m pip install --target .win-build/tools -r requirements/requirements-windows-build.txt
    python tools/build_windows_app.py

Dependencies come from .qt-deps/.audio-deps (or the running interpreter's site
packages). Output: .win-build/dist/phosphor/phosphor.exe and
.win-build/dist/phosphor-<version>-windows-x64.zip. The executable is unsigned, so
SmartScreen asks first-time users to confirm ("More info" -> "Run anyway").
"""
import _path  # noqa: F401  (makes the phosphor package importable)
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

HERE = Path(__file__).resolve().parent.parent
BUILD = HERE/'.win-build'
TOOLS = BUILD/'tools'
DIST = BUILD/'dist'
APP = DIST/'phosphor'
DEPENDENCIES = [HERE/name for name in ('.qt-deps', '.audio-deps') if (HERE/name).is_dir()]

# Qt modules the app never loads (its only QML import is QtQuick). The PySide6 hook
# bundles every QML plugin regardless, which costs well over 100 MB.
UNUSED_QT = ('Qt63D', 'Qt6Charts', 'Qt6DataVisualization', 'Qt6Graphs', 'Qt6Location', 'Qt6Multimedia',
             'Qt6Pdf', 'Qt6Positioning', 'Qt6Quick3D', 'Qt6RemoteObjects', 'Qt6Scxml', 'Qt6Sensors',
             'Qt6SpatialAudio', 'Qt6TextToSpeech', 'Qt6VirtualKeyboard', 'Qt6WebChannel', 'Qt6WebEngine',
             'Qt6WebSockets', 'Qt6WebView', 'Qt6QuickTest', 'Qt6Test', 'Qt6Sql')
ESSENTIAL = ('Qt6Core', 'Qt6Gui', 'Qt6Widgets', 'Qt6Qml', 'Qt6Quick', 'Qt6Svg', 'QtCore', 'QtGui',
             'QtWidgets', 'QtQml', 'QtQuick', 'qwindows', 'qtquick2plugin')


def run(*command, **options):
    print('+', ' '.join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], check=True, **options)


def make_icon():
    """Green logo on a dark rounded tile, as a multi-size .ico."""
    from PIL import Image, ImageDraw
    logo = Image.open(HERE/'phosphor/assets/logos/logo_green.png').convert('RGBA')
    tile = Image.new('RGBA', (1024, 1024), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle((60, 60, 964, 964), radius=200, fill=(8, 18, 10, 255))
    scale = 760/max(logo.size)
    logo = logo.resize((round(logo.width*scale), round(logo.height*scale)), Image.LANCZOS)
    tile.alpha_composite(logo, ((1024-logo.width)//2, (1024-logo.height)//2))
    icon = BUILD/'phosphor.ico'
    tile.save(icon, sizes=[(size, size) for size in (16, 24, 32, 48, 64, 128, 256)])
    return icon


def imported_dlls(binary):
    import pefile
    try: pe = pefile.PE(str(binary), fast_load=True)
    except pefile.PEFormatError: return set()
    try:
        pe.parse_data_directories([pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT']])
        return {entry.dll.decode().lower() for entry in getattr(pe, 'DIRECTORY_ENTRY_IMPORT', ())}
    finally:
        pe.close()  # pefile maps the file, which would block deleting it


def prune_qt(app):
    internal = app/'_internal'
    pyside = internal/'PySide6'
    removed = set()
    for dll in list(internal.rglob('Qt6*.dll')):
        if dll.stem.startswith(UNUSED_QT):
            removed.add(dll.name.lower()); dll.unlink()
    for binary in list(pyside.rglob('*.pyd')):
        if binary.stem.startswith(tuple('Qt'+name[3:] for name in UNUSED_QT)):
            binary.unlink()
    # Drop every QML module or plugin that links a removed DLL.
    for folder in (pyside/'qml', pyside/'plugins'):
        for binary in list(folder.rglob('*.dll')):
            if binary.exists() and removed & imported_dlls(binary):
                module = binary.parent
                if folder == pyside/'plugins': binary.unlink()
                else: shutil.rmtree(module)
    for folder in (pyside/'resources', pyside/'translations'):
        for path in list(folder.glob('qtwebengine*')): path.unlink() if path.is_file() else shutil.rmtree(path)
    if (pyside/'QtWebEngineProcess.exe').exists(): (pyside/'QtWebEngineProcess.exe').unlink()
    # Anything else that links a removed DLL goes too, repeated until nothing does.
    while dependents := [binary for binary in internal.rglob('*') if binary.suffix in ('.dll', '.pyd')
                         and binary.is_file() and removed & imported_dlls(binary)]:
        for binary in dependents:
            if binary.stem in ESSENTIAL: raise SystemExit(f'{binary} links a pruned Qt module.')
            print(f'  also removing {binary.relative_to(internal)}')
            removed.add(binary.name.lower()); binary.unlink()
    for name in ESSENTIAL:
        if not any(internal.rglob(f'{name}.*')): raise SystemExit(f'Pruning removed {name}.')
    print(f'Pruned {len(removed)} unused Qt binaries.')


def folder_size(path):
    return sum(file.stat().st_size for file in path.rglob('*') if file.is_file())


def build():
    if sys.platform != 'win32': raise SystemExit('Build the Windows app on Windows.')
    environment = dict(os.environ, PYTHONPATH=os.pathsep.join(str(path) for path in (TOOLS, *DEPENDENCIES)))
    environment.pop('PYTHONHOME', None)
    if subprocess.run([sys.executable, '-c', 'import PyInstaller'], env=environment).returncode:
        raise SystemExit('Install the build tools: python -m pip install --target .win-build/tools -r requirements/requirements-windows-build.txt')
    sys.path.insert(0, str(TOOLS))
    BUILD.mkdir(exist_ok=True)
    icon = make_icon()
    shutil.rmtree(APP, ignore_errors=True)
    run(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--onedir',
        '--name', 'phosphor', '--icon', icon,
        '--distpath', DIST, '--workpath', BUILD/'work', '--specpath', BUILD,
        '--paths', HERE, '--add-data', f'{HERE/"phosphor"/"assets"};phosphor/assets',
        # mido picks its backend by name at runtime.
        '--hidden-import', 'mido.backends.rtmidi',
        # The Tk fallback (app.py --cpu) and the macOS capture helper are not part of the Windows app.
        '--exclude-module', 'tkinter', '--exclude-module', '_tkinter',
        HERE/'tools'/'packaged_entry.py', cwd=HERE, env=environment)

    prune_qt(APP)
    from phosphor.storage.build_info import VERSION
    for name in ('README.md', 'LICENSE', 'NOTICE.md'): shutil.copy(HERE/name, APP/name)
    archive = DIST/f'phosphor-{VERSION}-windows-x64.zip'
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for file in sorted(APP.rglob('*')):
            if file.is_file(): bundle.write(file, Path('phosphor')/file.relative_to(APP))
    print(f'\nBuilt {APP/"phosphor.exe"} ({folder_size(APP)/2**20:.0f} MB)')
    print(f'Built {archive} ({archive.stat().st_size/2**20:.0f} MB)')


if __name__ == '__main__':
    if len(sys.argv) > 1: raise SystemExit(__doc__)
    build()
