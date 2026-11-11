"""Assign selected builder components without leaving construction mode."""
from PySide6.QtWidgets import QDialog,QVBoxLayout,QLabel,QComboBox,QDoubleSpinBox,QCheckBox,QDialogButtonBox,QPushButton
from phosphor.engine.drawing_motion import assign,remove_assignment,segment_indices
from phosphor.engine.motion_path import guide_route

class BuilderMotionDialog(QDialog):
    def __init__(self,builder):
        super().__init__(builder);self.builder=builder;self.selection=frozenset(builder.selected_segments)
        self.setWindowTitle('Assign motion to selected shapes');self.setModal(True);self.resize(460,380)
        layout=QVBoxLayout(self);intro=QLabel('Selected shapes keep their current offset. A nonzero Angle offset intentionally rotates the group and its offset.');intro.setWordWrap(True);layout.addWidget(intro)
        self.guide=QComboBox()
        for g in builder.drawing.guides:
            if g.enabled:self.guide.addItem(g.name,g.id)
        layout.addWidget(self.guide);layout.addWidget(QLabel('Velocity (scene units / second)'))
        self.speed=QDoubleSpinBox();self.speed.setRange(0,100);self.speed.setDecimals(3);self.speed.setSingleStep(.05);self.speed.setValue(.25);layout.addWidget(self.speed)
        self.endpoint=QComboBox();self.endpoint.addItems(['Back and forth','Stop at end']);layout.addWidget(self.endpoint)
        self.follow=QCheckBox('Follow path direction');layout.addWidget(self.follow)
        layout.addWidget(QLabel('Angle offset (degrees)'))
        self.angle=QDoubleSpinBox();self.angle.setRange(-360,360);self.angle.setDecimals(1);self.angle.setEnabled(False);layout.addWidget(self.angle)
        self.follow.toggled.connect(self.angle.setEnabled)
        selected_nodes=builder.drawing.node_ids(self.selection)
        existing=[b for b in builder.drawing.attachments if selected_nodes.intersection(b.nodes)]
        if len(existing)==1:
            b=existing[0];self.guide.setCurrentIndex(max(0,self.guide.findData(b.guide_id)));self.speed.setValue(b.speed)
            self.endpoint.setCurrentText(b.endpoint);self.follow.setChecked(b.follow);self.angle.setValue(b.angle_offset)
        self.notice=QLabel();self.notice.setWordWrap(True);layout.addWidget(self.notice)
        remove=QPushButton('Remove motion from selection');remove.clicked.connect(self.remove);layout.addWidget(remove)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Apply|QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Apply).setText('Assign / update motion')
        buttons.button(QDialogButtonBox.StandardButton.Apply).setEnabled(self.guide.count()>0)
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.apply);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.guide.currentIndexChanged.connect(self.behavior);self.behavior()

    def behavior(self):
        g=next((g for g in self.builder.drawing.guides if g.id==self.guide.currentData()),None)
        if g is None:self.notice.setText('Select a path and mark it as Motion path first. Then select the follower shapes.');self.endpoint.setEnabled(False);return
        _,closed=guide_route(self.builder.drawing.subset(segment_indices(self.builder.drawing,g.nodes)))
        self.endpoint.setEnabled(not closed)
        self.notice.setText('Closed path: continuous loop. Offset from the guide is preserved.' if closed else 'Open path: choose endpoint behavior. Offset from the guide is preserved.')

    def apply(self):
        try:
            drawing=assign(self.builder.drawing,self.selection,self.guide.currentData(),self.speed.value(),self.endpoint.currentText(),self.follow.isChecked(),self.angle.value())
            if self.builder.commit(drawing):self.notice.setText('Motion assigned with the existing offset. Enable Live audition or Apply to scene to preview it.')
        except ValueError as error:self.notice.setText(str(error))

    def remove(self):
        if self.builder.commit(remove_assignment(self.builder.drawing,self.selection)):self.notice.setText('Motion removed from the selected shapes.')
