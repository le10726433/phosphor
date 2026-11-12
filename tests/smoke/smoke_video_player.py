"""Video file input on the phosphor raster: open, play, pause, seek, raster light, clean exit.

Native Qt only for the picture checks; --headless checks the controls and that a
missing phosphor display is reported instead of crashing.
"""
import _path  # noqa: F401  (makes the phosphor package importable)
import os
import sys
import tempfile
headless = '--headless' in sys.argv
if headless: os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from phosphor.editor import qt_app as q
from phosphor.editor.video_player import field_gain, clock, FIELD_ENERGY
from video_fixture import write_test_video
q.os.environ['QSG_RENDER_LOOP'] = 'basic'
q.configure_graphics(); app = QApplication([])
assert clock(0) == '0:00' and clock(61500) == '1:01' and clock(3723000) == '1:02:03'
assert abs(field_gain(1/60)-FIELD_ENERGY) < 1e-12 and field_gain(1/120) == FIELD_ENERGY/2 and field_gain(5) == 3*FIELD_ENERGY
w = q.Window(SimpleNamespace(scene=None, ready_file=None)); w.resize(1220, 840); w.show(); QTest.qWait(200)
w.save_brightness = lambda: None  # Never write the user's preferences.
# Picture checks need known appearance: a decaying phosphor, default brightness, no grain.
w.apply_preferences(dict(w.preferences, phosphor='P31 green', palette='Green monochrome', beam_intensity=5.,
                         phosphor_grain=0., phosphor_shimmer=0., crt_glow=False))
assert w.monitor.count() == 4
w.monitor.setCurrentIndex(3); w.monitor_changed(3)
assert w.video_monitor and w.video_panel.isVisible() and not w.generated_panel.isVisible()
assert w.tabs.currentIndex() == 0 and not any(w.tabs.isTabEnabled(i) for i in (1, 2, 3))
assert w.viewer_heading.text() == 'RASTER CRT · 60 Hz'
with patch.object(q.QMessageBox, 'information') as information:
    w.export(); assert information.called
with tempfile.TemporaryDirectory() as folder:
    clip = write_test_video(Path(folder)/'test.avi')
    if headless or w.host.phosphor is None:
        errors = []
        with patch.object(w, 'error', side_effect=errors.append): w.open_video(clip)
        assert w.host.phosphor is not None or errors, 'A missing phosphor display must be reported'
        print('Video input (headless): controls and missing-display handling passed; picture not verified.')
    else:
        w.video_panel.loop.setChecked(True)
        w.open_video(clip)
        player = w.video.player
        for _ in range(100):
            QTest.qWait(30)
            if player.position() > 300: break
        assert w.video.playing and not w.video.error, (player.playbackState(), w.video.error)
        assert w.video_panel.name.text() == 'test.avi' and w.video_panel.seek.maximum() == 2000
        assert 'VIDEO · 480-LINE RASTER' in w.status.text() and '640×360' in w.status.text(), w.status.text()
        assert w.play_button.text() == '❚❚ PAUSE VIDEO'
        assert w.host.phosphor.root.property('videoMode')
        image = w.host.view.grabWindow().convertToFormat(QImage.Format.Format_Grayscale8)
        width, height = image.width(), image.height()
        row = lambda y: np.array([image.pixelColor(x, y).value() for x in range(0, width, 4)])
        # 16:9 video letterboxed in the square: the white bar at 6-10% of the width and
        # 8-38% of the video height lights up; the bars above the picture stay dark.
        top = (height-width*9/16)/2
        bar = image.pixelColor(int(width*.08), int(top+width*9/16*.2)).value()
        border = row(int(top*.5)).max()
        assert bar > 120 and border < 40, (bar, border)
        # Scanlines: within a lit mid-grey bar, brightness alternates line by line at 240 lines.
        w.video_panel.scan.setCurrentText('240 lines (low-res)'); QTest.qWait(400)
        image = w.host.view.grabWindow().convertToFormat(QImage.Format.Format_Grayscale8)
        # Centre of the 128 grey bar, which spans 18-22.5% of the width (video_fixture: bars
        # at 6, 12, 18 and 24%, each 4.5% wide). 25% is the 64 bar, too dim at 1x.
        x = int(width*.2025)
        column = np.array([image.pixelColor(x, int(top+width*9/16*.12)+y).value() for y in range(int(width*9/16*.2))])
        assert column.max()-column.min() > 12, (column.min(), column.max())
        if '--verbose' in sys.argv: print('scanline range on the 128 bar:', column.min(), column.max(), flush=True)
        # Pause, seek and resume; switching input pauses the video.
        w.play(); QTest.qWait(150)
        assert not w.video.playing and w.play_button.text() == '▶ PLAY VIDEO', w.play_button.text()
        player.setPosition(1500); QTest.qWait(200); assert abs(player.position()-1500) < 200, player.position()
        w.play(); QTest.qWait(200); assert w.video.playing
        w.monitor.setCurrentIndex(0); w.monitor_changed(0)
        QTest.qWait(50)
        assert not w.video.playing and not w.host.phosphor.root.property('videoMode')
        w.resize(1220, 840); w.monitor_changed(3); QTest.qWait(300)
        (q.HERE/'.user').mkdir(exist_ok=True); w.host.view.grabWindow().save(str(q.HERE/'.user/video-raster.png'))
        print('Video input: open, play, raster light and scanlines, pause/seek/resume, input switching passed.')
    # Close the clip before the folder is removed: Windows cannot delete a file the player
    # still holds open (macOS can, so this passed there).
    if w.video is not None:
        w.video.player.stop(); w.video.player.setSource(QUrl()); QTest.qWait(100)
w.timer.stop(); w.shutdown_audio(); w.release_views(); w.force_close = True; w.close(); app.processEvents()
