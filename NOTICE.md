# Third-party software

phosphor is licensed under the GNU General Public License v3 or later (see
[LICENSE](LICENSE)). Its source code is at https://github.com/le10726433/phosphor.

The packaged macOS and Windows apps also contain the following software, each
under its own license. Versions are those pinned for the release builds.

| Component | Used for | License | Source |
| --- | --- | --- | --- |
| CPython 3.12 | Runtime | PSF License 2.0 | https://www.python.org |
| Qt 6.10 and PySide6 / Shiboken6 6.10.3 | Interface and GPU rendering | LGPL-3.0 | https://code.qt.io, https://download.qt.io/official_releases/QtForPython/ |
| NumPy 2 | Signal and geometry math | BSD-3-Clause (with bundled MIT, Zlib and 0BSD parts) | https://numpy.org |
| python-sounddevice | Audio output | MIT | https://github.com/spatialaudio/python-sounddevice |
| PortAudio | Audio output (bundled by sounddevice) | MIT | https://www.portaudio.com |
| Mido | MIDI files and messages | MIT | https://github.com/mido/mido |
| python-rtmidi and RtMidi | MIDI input | MIT | https://github.com/SpotlightKid/python-rtmidi |
| cffi, pycparser | sounddevice bindings | MIT-0, BSD-3-Clause | https://github.com/python-cffi/cffi |
| PyAudioWPatch (Windows only) | WASAPI system-audio capture | Apache-2.0 | https://github.com/s0d3s/PyAudioWPatch |
| JetBrains Mono | Interface font | SIL Open Font License 1.1 ([assets/fonts/OFL.txt](assets/fonts/OFL.txt)) | https://github.com/JetBrains/JetBrainsMono |
| PyInstaller bootloader | App launcher | GPL-2.0-or-later with the PyInstaller bootloader exception | https://pyinstaller.org |

Build-only tools, not shipped in the apps: PyInstaller and its hooks (GPL-2.0-or-later
with exception), Pillow (MIT-CMU) for the icons, altgraph and macholib (MIT).

## Qt (LGPL-3.0)

Qt and PySide6 are dynamically linked and shipped as separate libraries: in macOS,
`phosphor.app/Contents/Frameworks/PySide6`; in Windows, `phosphor/_internal/PySide6`.
You may replace them with your own builds of the same versions. The release builds
use the unmodified PyPI wheels `PySide6==6.10.3`, whose corresponding source is
available from the Qt Project at the links above. Unused Qt modules are removed from
the packages; no Qt code is modified. The LGPL v3 text is at
https://www.gnu.org/licenses/lgpl-3.0.html.

## Music

phosphor bundles no music. MIDI files are read from the user's own disk.
