"""Build the local, ad-hoc signed input-only Core Audio capture helper."""
from pathlib import Path
import shutil
import subprocess
import sys
import threading

from phosphor.storage.app_paths import APP_DIR as HERE

SOURCE = HERE/'native'/'macos_capture'
BUNDLE = HERE/'.native'/'Oscilloscope Music Audio Capture.app'
EXECUTABLE = BUNDLE/'Contents/MacOS/OscilloscopeAudioCapture'
BUILD_LOCK = threading.Lock()


def packaged_helper():
    # build_macos_app.py places the prebuilt, signed helper in Contents/Helpers.
    return Path(sys.executable).resolve().parent.parent/'Helpers'/BUNDLE.name/'Contents/MacOS'/EXECUTABLE.name


def build_helper():
    if sys.platform != 'darwin': raise RuntimeError('The native capture helper requires macOS.')
    if getattr(sys,'frozen',False):
        helper = packaged_helper()
        if not helper.exists(): raise RuntimeError('This app bundle is missing its audio capture helper; rebuild it with tools/build_macos_app.py.')
        return helper
    with BUILD_LOCK:
        inputs = [SOURCE/'capture.m', SOURCE/'Info.plist', Path(__file__)]
        if EXECUTABLE.exists() and all(p.stat().st_mtime <= EXECUTABLE.stat().st_mtime for p in inputs):
            return EXECUTABLE
        EXECUTABLE.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(SOURCE/'Info.plist',BUNDLE/'Contents/Info.plist')
        commands = [
            ['xcrun','clang','-O2','-fobjc-arc','-std=gnu11','-mmacosx-version-min=14.2',
             '-framework','Foundation','-framework','CoreAudio',str(SOURCE/'capture.m'),
             '-Xlinker','-sectcreate','-Xlinker','__TEXT','-Xlinker','__info_plist',
             '-Xlinker',str(SOURCE/'Info.plist'),'-o',str(EXECUTABLE)],
            ['codesign','--force','--sign','-','--identifier','org.oscilloscopemusic.audio-capture',str(BUNDLE)]
        ]
        try:
            for command in commands:
                subprocess.run(command,check=True,capture_output=True,text=True,timeout=60)
        except (OSError,subprocess.SubprocessError) as error:
            EXECUTABLE.unlink(missing_ok=True)
            detail = getattr(error,'stderr','') or str(error)
            raise RuntimeError('Could not build macOS audio capture. Install Xcode Command Line Tools. '+detail[-3000:]) from error
        return EXECUTABLE


if __name__ == '__main__': print(build_helper())
