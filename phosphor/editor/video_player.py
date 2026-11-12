"""Video file input drawn by the phosphor display as a 60 Hz monochrome raster CRT.

Qt Multimedia (FFmpeg backend) decodes the file and plays its sound. Frames go to a
QML VideoOutput inside the phosphor layer and stay on the GPU; phosphor_raster.frag
turns each display step into one CRT field of scanlines. Nothing here touches the
X/Y audio engine.
"""
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QUrl, QSignalBlocker
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox, QSlider,
                               QCheckBox, QFileDialog)

VIDEO_FILTER = 'Video files (*.mp4 *.m4v *.mov *.mkv *.avi *.webm *.wmv *.ogv *.mpg *.mpeg);;All files (*)'
SCAN_LINES = {'240 lines (low-res)': 240, '480 lines (NTSC)': 480, '576 lines (PAL)': 576, '720 lines (fine)': 720}
DEFAULT_SCAN = '480 lines (NTSC)'
FIELD_RATE = 60.            # Simulated CRT refresh: one raster field per 1/60 s.
FIELD_ENERGY = .45          # Deposit for full white in one field, before Phosphor brightness.
LINE_WIDTH = .32            # Gaussian sigma of a scanline as a fraction of the line pitch.


def clock(milliseconds):
    seconds = max(0, int(milliseconds//1000))
    hours, rest = divmod(seconds, 3600)
    return f'{hours}:{rest//60:02}:{rest%60:02}' if hours else f'{rest//60}:{rest%60:02}'


def field_gain(elapsed):
    """Energy per display step, so brightness does not depend on the tick rate."""
    return FIELD_ENERGY*min(max(elapsed, 0.)*FIELD_RATE, 3.)


class VideoPlayer:
    """QMediaPlayer with sound; created on first use so startup never loads Qt Multimedia."""
    def __init__(self):
        from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
        self.QMediaPlayer = QMediaPlayer
        self.player = QMediaPlayer()
        self.audio = QAudioOutput()
        self.player.setAudioOutput(self.audio)
        self.error = ''
        self.player.errorOccurred.connect(lambda error, text: setattr(self, 'error', text or str(error)))
        self.output = None

    def attach(self, output):
        if output is not self.output:
            self.player.setVideoOutput(output); self.output = output

    def detach(self):
        self.player.stop(); self.player.setVideoOutput(None); self.output = None

    def open(self, path):
        self.error = ''
        self.player.setSource(QUrl.fromLocalFile(str(path)))

    @property
    def playing(self): return self.player.playbackState() == self.QMediaPlayer.PlaybackState.PlayingState

    def toggle(self):
        self.player.pause() if self.playing else self.player.play()

    def set_loop(self, enabled):
        self.player.setLoops(self.QMediaPlayer.Loops.Infinite if enabled else 1)

    def size_text(self):
        size = self.player.metaData().value(self.player.metaData().Key.Resolution) if self.player.hasVideo() else None
        return f'{size.width()}×{size.height()}' if size is not None and size.isValid() else ''


class VideoPanel(QWidget):
    """Input-tab controls for the video file input."""
    open_requested = Signal()

    def __init__(self, slider_class):
        super().__init__()
        self.player = None
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0); layout.setAlignment(Qt.AlignmentFlag.AlignTop); layout.setSpacing(6)
        self.open_button = QPushButton('Open video…'); self.open_button.clicked.connect(self.open_requested); layout.addWidget(self.open_button)
        self.name = QLabel('No video loaded.'); self.name.setObjectName('video_name'); layout.addWidget(self.name)
        row = QHBoxLayout(); layout.addLayout(row)
        self.seek = QSlider(Qt.Orientation.Horizontal); self.seek.setObjectName('video_seek'); self.seek.setRange(0, 0)
        self.seek.sliderReleased.connect(self.seek_released); self.seek.sliderMoved.connect(self.seek_moved); row.addWidget(self.seek, 1)
        self.time = QLabel(clock(0)+' / '+clock(0)); self.time.setProperty('inlineCaption', True); row.addWidget(self.time)
        row = QHBoxLayout(); layout.addLayout(row)
        caption = QLabel('Volume'); caption.setProperty('inlineCaption', True); caption.setFixedWidth(104); row.addWidget(caption)
        self.volume = slider_class(.8, 0., 1.); self.volume.setObjectName('video_volume'); self.volume.setFixedHeight(28)
        self.volume.edited.connect(self.volume_edited); row.addWidget(self.volume, 1)
        row = QHBoxLayout(); layout.addLayout(row)
        caption = QLabel('Raster'); caption.setProperty('inlineCaption', True); caption.setFixedWidth(104); row.addWidget(caption)
        self.scan = QComboBox(); self.scan.setObjectName('video_scan_lines'); self.scan.addItems(SCAN_LINES); self.scan.setCurrentText(DEFAULT_SCAN)
        row.addWidget(self.scan, 1)
        self.loop = QCheckBox('Loop'); self.loop.setObjectName('video_loop'); self.loop.toggled.connect(self.loop_toggled); layout.addWidget(self.loop)
        notes = ('Drawn as a 60 Hz monochrome CRT: one raster field per refresh, decayed by the phosphor.',
                 'Brightness: the Phosphor brightness slider under the scope.',
                 'Phosphor type, beam, glow and grain: Preferences. Off (line trace) uses Palette colour here.',
                 'Sound plays through the default output device.')
        for text in notes:
            label = QLabel(text); label.setWordWrap(True); layout.addWidget(label)

    @property
    def scan_lines(self): return SCAN_LINES[self.scan.currentText()]

    def attach_player(self, player):
        self.player = player
        player.audio.setVolume(self.volume.value); player.set_loop(self.loop.isChecked())

    def volume_edited(self, value):
        if self.player: self.player.audio.setVolume(float(value))

    def loop_toggled(self, enabled):
        if self.player: self.player.set_loop(enabled)

    def seek_moved(self, position):
        self.time.setText(clock(position)+' / '+clock(self.seek.maximum()))

    def seek_released(self):
        if self.player: self.player.player.setPosition(self.seek.value())

    def refresh(self):
        """Follow the player's position (called from the window's tick)."""
        if self.player is None: return
        player = self.player.player
        duration, position = player.duration(), player.position()
        if self.seek.maximum() != duration: self.seek.setRange(0, max(0, duration))
        if not self.seek.isSliderDown():
            blocker = QSignalBlocker(self.seek); self.seek.setValue(position)
            self.time.setText(clock(position)+' / '+clock(duration))

    def choose_file(self, parent):
        path, _ = QFileDialog.getOpenFileName(parent, 'Open video', '', VIDEO_FILTER)
        return Path(path) if path else None
