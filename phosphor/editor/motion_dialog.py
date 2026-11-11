"""Motion guide and rigid-group assignment, independent of timeline features."""
from dataclasses import replace
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QLabel,QComboBox,QDoubleSpinBox,
    QCheckBox,QListWidget,QListWidgetItem,QDialogButtonBox)
from phosphor.engine.signal_engine import is_motion_guide,validate,world_motion_route,projected_solid
from phosphor.engine.motion_path import Motion

class MotionDialog(QDialog):
    def __init__(self,window):
        super().__init__(window);self.editor=window;self.setWindowTitle('Motion paths');self.setModal(True);self.resize(470,550)
        layout=QVBoxLayout(self)
        layout.addWidget(QLabel('Choose a guide and the objects that move together.'))
        self.guide=QComboBox();self.guide.addItem('No motion / remove assignment',None)
        for i,shape in enumerate(window.scene.settings.shapes):
            if is_motion_guide(shape):self.guide.addItem(f'{i+1:02}  {shape.custom.name}',shape.uid)
        layout.addWidget(self.guide)
        layout.addWidget(QLabel('Velocity (scene units / second)'))
        self.speed=QDoubleSpinBox();self.speed.setRange(0,100);self.speed.setDecimals(3);self.speed.setSingleStep(.05);self.speed.setValue(.25);layout.addWidget(self.speed)
        layout.addWidget(QLabel('Open-path endpoint behavior (closed paths always loop)'))
        self.endpoint=QComboBox();self.endpoint.addItems(['Back and forth','Stop at end']);layout.addWidget(self.endpoint)
        self.route_info=QLabel();self.route_info.setWordWrap(True);layout.addWidget(self.route_info)
        self.follow=QCheckBox('Follow path direction');layout.addWidget(self.follow)
        layout.addWidget(QLabel('Angle offset (degrees; when following direction)'))
        self.angle=QDoubleSpinBox();self.angle.setRange(-360,360);self.angle.setDecimals(1);layout.addWidget(self.angle)
        self.angle.setEnabled(False);self.follow.toggled.connect(self.angle.setEnabled)
        layout.addWidget(QLabel('Objects to move as a rigid group'))
        self.objects=QListWidget();layout.addWidget(self.objects,1)
        primary=window.scene.shape;binding=primary.motion
        for i,shape in enumerate(window.scene.settings.shapes):
            if is_motion_guide(shape):continue
            label=shape.custom.name if shape.custom is not None and shape.solid=='Custom shape' else shape.solid
            item=QListWidgetItem(f'{i+1:02}  {label}');item.setData(Qt.ItemDataRole.UserRole,i)
            item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
            checked=i==window.scene.selected or (binding is not None and shape.motion==binding)
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked);self.objects.addItem(item)
        if binding is not None:
            self.guide.setCurrentIndex(max(0,self.guide.findData(binding.guide_id)));self.speed.setValue(binding.speed)
            self.endpoint.setCurrentText(binding.endpoint);self.follow.setChecked(binding.follow);self.angle.setValue(binding.angle_offset)
        elif self.guide.count()>1:self.guide.setCurrentIndex(1)
        self.notice=QLabel('Apply restarts motion. Play Audio also restarts it. Open paths turn 180° on reversal when following direction.')
        self.notice.setWordWrap(True);layout.addWidget(self.notice)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Apply|QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Apply).setText('Apply & restart motion')
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply)
        buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.guide.currentIndexChanged.connect(self.guide_behavior);self.guide_behavior()

    def guide_behavior(self):
        guide_id=self.guide.currentData()
        if guide_id is None:
            self.endpoint.setEnabled(False)
            self.route_info.setText('To create a guide, use Build shape and enable Use as motion path. Choose No motion to remove assignments.')
            return
        guide=next(s for s in self.editor.scene.settings.shapes if s.uid==guide_id)
        _,closed=world_motion_route(guide);self.endpoint.setEnabled(not closed)
        self.route_info.setText('Closed path · continuous loop' if closed else 'Open path · choose endpoint behavior')

    def apply(self):
        indices=[self.objects.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.objects.count()) if self.objects.item(i).checkState()==Qt.CheckState.Checked]
        if not indices:self.notice.setText('Select at least one object to move.');return
        shapes=list(self.editor.scene.settings.shapes);guide_id=self.guide.currentData()
        try:
            if guide_id:
                guide=next(s for s in shapes if s.uid==guide_id);world_motion_route(guide)
                centers=[]
                for i in indices:
                    shape=shapes[i];vertices=projected_solid(0,shape)*shape.shape_size
                    centers.append((vertices.min(axis=0)+vertices.max(axis=0))/2+[shape.position_x,shape.position_y])
                anchor=tuple(np.mean(centers,axis=0).tolist())
                binding=Motion(guide_id,self.speed.value(),self.endpoint.currentText(),self.follow.isChecked(),self.angle.value(),anchor).validate()
            else:binding=None
            for i in indices:shapes[i]=replace(shapes[i],motion=binding)
            candidate=replace(self.editor.scene.settings,shapes=tuple(shapes));validate(candidate)
        except (ValueError,StopIteration) as error:self.notice.setText(str(error));return
        self.editor.scene.settings=candidate;self.editor.restart_motion();self.editor.sync()
        self.notice.setText('Motion applied and restarted. The preview runs continuously; Play Audio restarts from the beginning.')
