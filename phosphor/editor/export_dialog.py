"""Shared offline export choices; exec() can be patched by UI smokes."""
from PySide6.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QComboBox, QLabel, QDialogButtonBox
from phosphor.storage import audio_writer


class ExportDialog(QDialog):
    def __init__(self, parent, preferences, duration, loop_duration=None):
        super().__init__(parent)
        self.setWindowTitle('Export audio')
        layout = QVBoxLayout(self); form = QFormLayout(); layout.addLayout(form)
        self.format = QComboBox(); self.format.addItems(['WAV', 'FLAC'])
        self.depth = QComboBox()
        self.rate = QComboBox()
        for rate in audio_writer.RATES: self.rate.addItem(f'{rate / 1000:g} kHz', rate)
        self.range = QComboBox(); self.range.addItems(['Whole song', 'Loop range'])
        form.addRow('Format', self.format); form.addRow('Bit depth', self.depth); form.addRow('Sample rate', self.rate)
        if loop_duration is not None: form.addRow('Range', self.range)
        self.size_label = QLabel(); layout.addWidget(self.size_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self.duration, self.loop_duration = duration, loop_duration
        if audio_writer.soundfile is None:
            item = self.format.model().item(1); item.setEnabled(False)
            item.setToolTip('Install soundfile to export FLAC.')
            self.format.setToolTip('FLAC and float WAV require soundfile; integer WAV remains available.')
        self.format.setCurrentText(preferences['export_format'] if audio_writer.soundfile is not None else 'WAV')
        self.format.currentTextChanged.connect(self.update_depths)
        self.update_depths()
        index = self.depth.findData(preferences['export_depth'])
        self.depth.setCurrentIndex(max(0, index))
        self.rate.setCurrentIndex(self.rate.findData(preferences['export_rate']))
        self.range.setCurrentIndex(preferences['export_range'] == 'loop')
        for control in (self.depth, self.rate, self.range): control.currentIndexChanged.connect(self.update_size)
        self.update_size()

    def update_depths(self, *args):
        previous = self.depth.currentData(); self.depth.clear()
        depths = (16, 24, 32) if self.format.currentText() == 'WAV' and audio_writer.soundfile is not None else (16, 24)
        for depth in depths: self.depth.addItem('32-bit float' if depth == 32 else f'{depth}-bit', depth)
        self.depth.setCurrentIndex(max(0, self.depth.findData(previous)))
        self.update_size()

    def update_size(self, *args):
        duration = self.loop_duration if self.loop_duration is not None and self.range.currentIndex() else self.duration
        size = duration * (self.rate.currentData() or 48000) * 2 * (self.depth.currentData() or 16) / 8
        suffix = ' before compression; FLAC size varies' if self.format.currentText() == 'FLAC' else ' plus file header'
        self.size_label.setText(f'Estimated size: {size / 1024**2:.2f} MiB{suffix}')

    def choices(self):
        return self.format.currentText(), self.depth.currentData(), self.rate.currentData(), 'loop' if self.range.currentIndex() else 'whole'
