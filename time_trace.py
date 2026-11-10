"""Bounded, read-only X(t)/Y(t) view of the same raw PCM window as the X-Y view."""
import numpy as np
from PySide6.QtCore import Qt,QPointF,QRectF
from PySide6.QtGui import QPainter,QPen,QColor,QPolygonF
from PySide6.QtWidgets import QWidget,QSizePolicy
from crt_theme import PALETTES


class TimeTrace(QWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.signal=np.zeros((1920,2),dtype=np.float32)
        self.sample_rate=48000
        self.colors=PALETTES['Green monochrome']
        self.frozen=False
        self.setMinimumSize(0,260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Expanding)

    def set_samples(self,signal,sample_rate,colors):
        if self.frozen and self.colors==colors:return
        self.colors=colors
        if not self.frozen:
            self.signal=np.asarray(signal).copy()
            self.sample_rate=sample_rate
        self.update()

    def paintEvent(self,event):
        painter=QPainter(self);painter.fillRect(self.rect(),QColor(self.colors['background']))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width,height=self.width(),self.height()
        pad=36;gap=50;plot_height=max(20,(height-2*pad-gap)/2)
        peak=max(1.,float(np.max(np.abs(self.signal))) if self.signal.size else 1.)
        scale=np.ceil(peak*2)/2
        duration=len(self.signal)/self.sample_rate*1000
        for channel,title in enumerate(('X(t) · left','Y(t) · right')):
            rect=QRectF(pad,pad+channel*(plot_height+gap),max(1,width-pad-12),plot_height)
            painter.setPen(QColor(self.colors['fg']));painter.drawText(QPointF(rect.left(),rect.top()-10),title)
            painter.setPen(QPen(QColor(self.colors['grid']),1))
            painter.drawRect(rect)
            for i in range(1,4):
                x=rect.left()+rect.width()*i/4
                painter.drawLine(QPointF(x,rect.top()),QPointF(x,rect.bottom()))
            painter.drawLine(QPointF(rect.left(),rect.center().y()),QPointF(rect.right(),rect.center().y()))
            painter.setPen(QColor(self.colors['muted']))
            for y,text in ((rect.top()+5,f'{scale:g}'),(rect.center().y()+4,'0'),(rect.bottom(),f'-{scale:g}')):
                painter.drawText(QPointF(2,y),text)
            painter.drawText(QPointF(rect.left(),rect.bottom()+16),f'-{duration:.1f} ms')
            painter.drawText(QPointF(rect.right()-22,rect.bottom()+16),'0')
            if len(self.signal)>1:
                # Every raw sample is retained. Drawing is confined to each channel box.
                x=np.linspace(rect.left(),rect.right(),len(self.signal))
                y=rect.center().y()-self.signal[:,channel]/scale*rect.height()/2
                painter.save();painter.setClipRect(rect)
                painter.setPen(QPen(QColor(self.colors['trace'] if channel==0 else self.colors['accent']),1.2))
                painter.drawPolyline(QPolygonF([QPointF(float(a),float(b)) for a,b in zip(x,y)]))
                painter.restore()
        painter.end()
