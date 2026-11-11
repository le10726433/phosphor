"""Package the Qt editor as a double-clickable, ad-hoc signed macOS app.

    .venv/bin/python -m pip install -r requirements/requirements-macos-build.txt
    .venv/bin/python tools/build_macos_app.py [--dmg]

Output: .mac-build/dist/phosphor.app (and phosphor-<version>-macos-arm64.dmg with --dmg).
The signature is ad hoc, so it runs on this Mac. Other Macs need a Developer ID
signature and notarization, or the user must approve it in Privacy & Security.
"""
import _path  # noqa: F401  (makes the phosphor package importable)
import argparse
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent.parent
BUILD = HERE/'.mac-build'
DIST = BUILD/'dist'
APP = DIST/'phosphor.app'
IDENTIFIER = 'org.oscilloscopemusic.phosphor'
# PySide6 6.10's extension modules are built for macOS 15 (otool: minos 15.0); on
# 13 or 14 the bundle would start and then fail to load Qt.
MIN_MACOS = '15.0'


def run(*command, **options):
    print('+', ' '.join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], check=True, **options)


def make_icon():
    """Green logo on a dark rounded tile, at every size iconutil needs."""
    from PIL import Image, ImageDraw
    logo = Image.open(HERE/'phosphor/assets/logos/logo_green.png').convert('RGBA')
    tile = Image.new('RGBA', (1024, 1024), (0, 0, 0, 0))
    ImageDraw.Draw(tile).rounded_rectangle((100, 100, 924, 924), radius=185, fill=(8, 18, 10, 255))
    scale = 700/max(logo.size)
    logo = logo.resize((round(logo.width*scale), round(logo.height*scale)), Image.LANCZOS)
    tile.alpha_composite(logo, ((1024-logo.width)//2, (1024-logo.height)//2))
    iconset = BUILD/'phosphor.iconset'
    shutil.rmtree(iconset, ignore_errors=True); iconset.mkdir(parents=True)
    for size in (16, 32, 128, 256, 512):
        tile.resize((size, size), Image.LANCZOS).save(iconset/f'icon_{size}x{size}.png')
        tile.resize((size*2, size*2), Image.LANCZOS).save(iconset/f'icon_{size}x{size}@2x.png')
    icon = BUILD/'phosphor.icns'
    run('iconutil', '-c', 'icns', iconset, '-o', icon)
    return icon

# Qt frameworks the app never loads (its only QML import is QtQuick). The PySide6
# hook bundles every QML plugin regardless, which costs well over 200 MB.
UNUSED_FRAMEWORKS = ('Qt3D', 'QtCharts', 'QtDataVisualization', 'QtGraphs', 'QtLocation', 'QtMultimedia',
                     'QtPdf', 'QtPositioning', 'QtQuick3D', 'QtRemoteObjects', 'QtScxml', 'QtSensors',
                     'QtSpatialAudio', 'QtTextToSpeech', 'QtVirtualKeyboard', 'QtWebChannel', 'QtWebEngine',
                     'QtWebSockets', 'QtWebView', 'QtQuickTest', 'QtTest', 'QtSql')


def linked_frameworks(binary):
    result = subprocess.run(['otool', '-L', str(binary)], capture_output=True, text=True)
    return re.findall(r'(\w+)\.framework/', result.stdout)


def remove(path):
    if path.is_symlink() or path.is_file(): path.unlink()
    elif path.is_dir(): shutil.rmtree(path)


def prune_qt(app):
    contents = app/'Contents'
    removed = set()
    for framework in (contents/'Frameworks/PySide6/Qt/lib').glob('*.framework'):
        if framework.stem.startswith(UNUSED_FRAMEWORKS):
            removed.add(framework.stem); remove(framework)
    for root in ('Frameworks', 'Resources'):
        for name in removed: remove(contents/root/name)
    # Drop every QML module or plugin that links a removed framework.
    for qml in (contents/'Frameworks/PySide6/Qt/qml', contents/'Frameworks/PySide6/Qt/plugins'):
        for binary in list(qml.rglob('*.dylib')):
            if binary.exists() and not binary.is_symlink() and removed & set(linked_frameworks(binary)):
                module = binary.parent
                relative = module.relative_to(contents/'Frameworks')
                remove(module); remove(contents/'Resources'/relative)
    while dangling := [path for path in contents.rglob('*') if path.is_symlink() and not path.exists()]:
        for path in dangling: path.unlink()
    for binary in contents.rglob('*'):
        if binary.suffix in ('.dylib', '.so') and binary.is_file() and removed & set(linked_frameworks(binary)):
            raise SystemExit(f'{binary} still links a pruned Qt framework.')
    print(f'Pruned {len(removed)} unused Qt frameworks.')


def bundle_version():
    from phosphor.storage.build_info import VERSION
    return VERSION, re.match(r'\d+(\.\d+)*', VERSION).group(0)


def build(dmg=False):
    if sys.platform != 'darwin': raise SystemExit('Build the macOS app on a Mac.')
    try: import PyInstaller  # noqa: F401
    except ImportError: raise SystemExit('Install the build tools: .venv/bin/python -m pip install -r requirements/requirements-macos-build.txt')
    from phosphor.audio.build_macos_capture import build_helper, BUNDLE as HELPER
    build_helper()
    BUILD.mkdir(exist_ok=True)
    icon = make_icon()
    shutil.rmtree(APP, ignore_errors=True)
    run(sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--windowed', '--onedir',
        '--name', 'phosphor', '--icon', icon, '--osx-bundle-identifier', IDENTIFIER,
        '--distpath', DIST, '--workpath', BUILD/'work', '--specpath', BUILD,
        '--paths', HERE, '--add-data', f'{HERE/"phosphor"/"assets"}:phosphor/assets',
        # mido picks its backend by name at runtime.
        '--hidden-import', 'mido.backends.rtmidi',
        # The Tk fallback (app.py --cpu) is not part of the packaged Qt app.
        '--exclude-module', 'tkinter', '--exclude-module', '_tkinter',
        HERE/'tools'/'packaged_entry.py', cwd=HERE)

    prune_qt(APP)
    version, short = bundle_version()
    info_path = APP/'Contents/Info.plist'
    with info_path.open('rb') as file: info = plistlib.load(file)
    info.update(CFBundleName='phosphor', CFBundleDisplayName='phosphor',
                CFBundleShortVersionString=short, CFBundleVersion=version,
                LSMinimumSystemVersion=MIN_MACOS, NSHighResolutionCapable=True,
                LSApplicationCategoryType='public.app-category.music',
                NSAudioCaptureUsageDescription='Display system audio as a live stereo X-Y trace in phosphor. '
                    'Audio stays in memory and is never played back or saved.')
    with info_path.open('wb') as file: plistlib.dump(info, file)

    for name in ('LICENSE', 'NOTICE.md'): shutil.copy(HERE/name, APP/'Contents/Resources'/name)
    helpers = APP/'Contents/Helpers'
    helpers.mkdir(exist_ok=True)
    run('ditto', HELPER, helpers/HELPER.name)  # ditto keeps the helper's own signature
    # Sign the outer bundle last; nested code is already signed, so no --deep.
    run('codesign', '--force', '--sign', '-', '--identifier', IDENTIFIER, APP)
    run('codesign', '--verify', '--strict', '--verbose=2', APP)
    print(f'\nBuilt {APP}')

    if dmg:
        image = DIST/f'phosphor-{version}-macos-arm64.dmg'
        image.unlink(missing_ok=True)
        staging = BUILD/'dmg'
        shutil.rmtree(staging, ignore_errors=True); staging.mkdir()
        run('ditto', APP, staging/APP.name)
        (staging/'Applications').symlink_to('/Applications')
        for name in ('README.md', 'LICENSE', 'NOTICE.md'): shutil.copy(HERE/name, staging/name)
        run('hdiutil', 'create', '-volname', 'phosphor', '-srcfolder', staging, '-ov', '-format', 'UDZO', image)
        shutil.rmtree(staging)
        print(f'Built {image}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dmg', action='store_true', help='also make a drag-to-Applications disk image')
    build(parser.parse_args().dmg)
