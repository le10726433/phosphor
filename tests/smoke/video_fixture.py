"""Write a small MJPEG AVI test video with Qt only (no ffmpeg needed). Needs a QGuiApplication."""
from pathlib import Path
import struct

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter


def chunk(fourcc, data):
    return fourcc + struct.pack('<I', len(data)) + data + (b'\0' if len(data) % 2 else b'')


def riff_list(kind, fourcc, data):
    return kind + struct.pack('<I', len(data)+4) + fourcc + data


def frame_image(index, frames, width, height):
    """Grey ramp, bars, a moving ball and a frame counter: easy to judge on a raster."""
    image = QImage(width, height, QImage.Format.Format_RGB32); image.fill(QColor(0, 0, 0))
    p = QPainter(image); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    ramp = QLinearGradient(0, 0, width, 0); ramp.setColorAt(0, QColor(0, 0, 0)); ramp.setColorAt(1, QColor(255, 255, 255))
    p.fillRect(QRectF(0, height*.78, width, height*.22), ramp)
    for i, level in enumerate((255, 191, 128, 64)):
        p.fillRect(QRectF(width*(.06+i*.06), height*.08, width*.045, height*.3), QColor(level, level, level))
    t = index/frames
    p.setBrush(QColor(255, 255, 255)); p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(width*(.35+.5*t), height*.5), height*.1, height*.1)
    p.setPen(QColor(230, 230, 230)); p.setFont(QFont('Helvetica', int(height*.09)))
    p.drawText(QRectF(width*.5, height*.06, width*.45, height*.2), Qt.AlignmentFlag.AlignRight, f'{index:03}')
    p.end()
    return image


def jpeg(image):
    data = QByteArray(); buffer = QBuffer(data); buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, 'JPG', 85); buffer.close()
    return bytes(data)


def write_test_video(path, frames=60, fps=30, width=640, height=360):
    images = [jpeg(frame_image(i, frames, width, height)) for i in range(frames)]
    largest = max(map(len, images))
    avih = struct.pack('<14I', round(1e6/fps), largest*fps, 0, 0x10, frames, 0, 1, largest, width, height, 0, 0, 0, 0)
    strh = b'vids' + b'MJPG' + struct.pack('<IHH6IiI4h', 0, 0, 0, 0, 1, fps, 0, frames, largest, -1, 0, 0, 0, width, height)
    strf = struct.pack('<IiiHH4sIiiII', 40, width, height, 1, 24, b'MJPG', width*height*3, 0, 0, 0, 0)
    header = riff_list(b'LIST', b'hdrl', chunk(b'avih', avih) + riff_list(b'LIST', b'strl', chunk(b'strh', strh) + chunk(b'strf', strf)))
    movi, index, offset = b'', b'', 4  # Index offsets count from the 'movi' fourcc.
    for data in images:
        piece = chunk(b'00dc', data)
        index += b'00dc' + struct.pack('<III', 0x10, offset, len(data))
        movi += piece; offset += len(piece)
    body = header + riff_list(b'LIST', b'movi', movi) + chunk(b'idx1', index)
    Path(path).write_bytes(riff_list(b'RIFF', b'AVI ', body))
    return Path(path)
