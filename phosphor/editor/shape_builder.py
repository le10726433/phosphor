"""Native Qt Quick line/curve constructor; audio receives frozen snapshots."""
from dataclasses import replace
import json
import math
from pathlib import Path
import numpy as np
from PySide6.QtCore import Qt, Signal, QEvent, QMimeData, QPoint, QSignalBlocker
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,
    QLineEdit,QCheckBox,QDoubleSpinBox,QComboBox,QFileDialog,QApplication,QMenu,QMessageBox)
from PySide6.QtQuick import QQuickWindow,QSGNode
from phosphor.engine.custom_shape import Drawing,Curve,compiled,paths,text_drawing,bold_preview,MAX_NODES,MAX_EDGES,CUSTOM_OBJECT
from phosphor.engine.stroke_style import set_style,style_of,MAX_BOLD,MAX_DEPTH
from phosphor.engine.route_plan import ROUTE_OFF
from phosphor.engine.curve_geometry import curve_spans
from phosphor.display.qt_scope import GpuScope
from phosphor.display.qt_geometry import stroke_triangles
from phosphor.storage.app_files import atomic_json
from phosphor.editor.builder_selection import axis_delta,intersects_box,dashed_lines
from phosphor.engine.motion_path import guide_route
from phosphor.engine.drawing_motion import set_role,reconcile,validate_routes,segment_indices

CLIPBOARD_TYPE='application/x-phosphor-geometry+json'
DRAW_WIDTH=1.6
BOX_WIDTH=DRAW_WIDTH/2


class ConstructionCanvas(GpuScope):
    """Editable line/node overlays, using the same QSG triangle buffers as output."""
    def __init__(self,parent,editor):
        super().__init__(parent);self.editor=editor
        self.setAcceptedMouseButtons(Qt.MouseButton.AllButtons)
        self.setAcceptHoverEvents(True);self.setFlag(self.Flag.ItemIsFocusScope,True)
        self.zoom=1.;self.pan=np.zeros(2);self.pan_start=None

    @property
    def scale(self):return max(1.,.4*min(self.width(),self.height())*self.zoom)

    def pixel(self,point):return np.array(point)*[self.scale,-self.scale]+[self.width()/2,self.height()/2]+self.pan
    def world(self,point):return (np.array(point)-[self.width()/2,self.height()/2]-self.pan)/[self.scale,-self.scale]

    def mousePressEvent(self,event):
        self.forceActiveFocus();p=np.array([event.position().x(),event.position().y()])
        if event.button()==Qt.MouseButton.MiddleButton:self.pan_start=(p,self.pan.copy())
        elif event.button()==Qt.MouseButton.LeftButton:self.editor.press(p,event.modifiers())
        elif event.button()==Qt.MouseButton.RightButton:self.editor.context_menu(p)
        event.accept()

    def mouseMoveEvent(self,event):
        p=np.array([event.position().x(),event.position().y()])
        if self.pan_start:self.pan=self.pan_start[1]+p-self.pan_start[0];self.update()
        else:self.editor.move(p,event.modifiers())
        event.accept()

    def mouseReleaseEvent(self,event):
        if event.button()==Qt.MouseButton.RightButton:event.accept();return
        if self.pan_start:self.pan_start=None
        else:
            self.editor.move(np.array([event.position().x(),event.position().y()]),event.modifiers())
            self.editor.release()
        event.accept()

    def hoverMoveEvent(self,event):
        self.editor.hover(np.array([event.position().x(),event.position().y()]),event.modifiers());event.accept()

    def wheelEvent(self,event):
        p=np.array([event.position().x(),event.position().y()]);world=self.world(p)
        self.zoom=float(np.clip(self.zoom*1.15**(event.angleDelta().y()/120),.005,12))
        self.pan+=p-self.pixel(world);self.update();event.accept()

    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Escape:self.editor.cancel_gesture()
        elif event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter):self.editor.finish_stroke()
        elif event.key() in (Qt.Key.Key_Delete,Qt.Key.Key_Backspace):self.editor.delete_selection()
        elif event.matches(QKeySequence.StandardKey.Undo):self.editor.undo()
        elif event.matches(QKeySequence.StandardKey.Redo):self.editor.redo()
        elif event.matches(QKeySequence.StandardKey.Copy):self.editor.copy_selection()
        elif event.matches(QKeySequence.StandardKey.Cut):self.editor.cut_selection()
        elif event.matches(QKeySequence.StandardKey.Paste):self.editor.paste_selection()
        else:super().keyPressEvent(event);return
        event.accept()

    def updatePaintNode(self,old,data):
        if old is None:
            old=QSGNode();self.root_node=old;self.layers=[None]*7
            for i in (0,6,1,2,3,4,5):self.layer(old,i)
        e=self.editor;drawing=e.drag_drawing or e.drawing
        nodes={i:(x,y) for i,x,y in drawing.nodes}
        nodes.update((i,(x,y)) for i,x,y in e.pending)
        def lines_mesh(lines,width=1.):
            values=[stroke_triangles(line,width) for line in lines]
            return np.concatenate(values) if values else np.empty((0,2),dtype=np.float32)
        # Grid follows editor pan/zoom; no camera state enters geometry or audio.
        step=2.**round(math.log2(48/self.scale));grid=[]
        low=self.world((0,self.height()));high=self.world((self.width(),0))
        for axis in (0,1):
            for value in np.arange(math.floor(low[axis]/step)*step,high[axis]+step,step)[:200]:
                a=low.copy();b=high.copy();a[axis]=b[axis]=value
                grid.append((self.pixel(a),self.pixel(b)))
        self.upload(0,lines_mesh(grid),QColor(self.colors['grid']))
        # World-origin axes remain behind editable geometry and follow the
        # same camera transform. Letter glyphs stay a readable screen size.
        ox,oy=self.pixel((0,0));w,h=self.width(),self.height();axes=[]
        if 0<=oy<=h:
            axes.append([(0,oy),(w,oy)])
            label=np.array([max(0,w-20),min(max(2,oy+8),max(2,h-14))])
            axes.extend((label+np.array([[0,0],[8,10]]),label+np.array([[8,0],[0,10]])))
        if 0<=ox<=w:
            axes.append([(ox,0),(ox,h)])
            label=np.array([min(max(2,ox+8),max(2,w-12)),8.])
            axes.extend((label+np.array([[0,0],[4,5],[8,0]]),label+np.array([[4,5],[4,10]])))
        if 0<=ox<=w and 0<=oy<=h:
            label=np.array([min(ox+7,max(0,w-12)),min(oy+7,max(0,h-14))])
            axes.append(label+np.array([[2,0],[6,0],[8,2],[8,8],[6,10],[2,10],[0,8],[0,2],[2,0]]))
        self.upload(6,lines_mesh(axes,1.2),QColor(self.colors['muted']))
        try:edges=[self.pixel(p) for p in paths(drawing)]
        except ValueError:edges=[]
        guide_indices={k for g in drawing.guides if g.enabled for k in segment_indices(drawing,g.nodes)}
        # Bold shapes show their outline as the trace; the editable centre line stays faint.
        try:bold_indices,outline=bold_preview(drawing) if drawing.styles else (frozenset(),())
        except ValueError:bold_indices,outline=frozenset(),()
        traced=[p for k,p in enumerate(edges) if k not in guide_indices and k not in bold_indices]
        self.upload(1,lines_mesh(traced+[self.pixel(p) for p in outline],DRAW_WIDTH),QColor(self.colors['trace']))
        squares=[];selected=[]
        group_nodes={i for k in e.selected_segments if k<len(drawing.segments) for i in drawing.segments[k]}
        for i,position in nodes.items():
            highlighted=i==e.selected or i in group_nodes
            p=self.pixel(position);r=5 if highlighted else 3
            target=selected if highlighted else squares
            target.append(p+np.array([[-r,-r],[r,-r],[r,r],[-r,-r],[r,r],[-r,r]]))
        self.upload(2,np.concatenate(squares) if squares else np.empty((0,2)),QColor(self.colors['muted']))
        highlights=[]
        for index in e.selected_segments:
            if index<len(edges):highlights.append(edges[index])
        box_lines=[]
        if e.box is not None:
            a,b=e.box;corners=np.array([a,[b[0],a[1]],b,[a[0],b[1]],a]);box_lines=dashed_lines(corners)
        self.upload(5,lines_mesh(box_lines,BOX_WIDTH),QColor(self.colors['accent']))
        if e.snap_target is not None and e.snap_target in nodes:
            p=self.pixel(nodes[e.snap_target]);r=9
            highlights.extend([(p+[-r,-r],p+[r,-r]),(p+[r,-r],p+[r,r]),(p+[r,r],p+[-r,r]),(p+[-r,r],p+[-r,-r])])
        highlight_mesh=lines_mesh(highlights,3.5)
        if selected:highlight_mesh=np.concatenate((highlight_mesh,*selected))
        self.upload(3,highlight_mesh,QColor(self.colors['accent']))
        ghost=[edges[k] for k in bold_indices if k<len(edges)]
        for k in guide_indices:
            if k<len(edges):ghost.extend(dashed_lines(edges[k]))
        points=[n[1:] for n in e.pending]
        if points:
            if e.hover_point is not None and np.linalg.norm(np.array(points[-1])-e.hover_point)>1e-8:points.append(e.hover_point)
            try:
                count={'Arc':3,'Semicircle':2,'Circle':2}.get(e.tool)
                if (e.tool=='Spline' and len(points)>=2) or (count and len(points)==count):
                    ghost.extend(self.pixel(p) for p in curve_spans(e.tool,tuple(tuple(p) for p in points)))
                else:ghost.append(self.pixel(np.asarray(points)))
            except ValueError:ghost.append(self.pixel(np.asarray(points)))
        # Dashed connectors disclose stereo travel between disconnected strokes.
        if drawing.playable:
            try:
                visible=drawing.subset(set(range(len(drawing.segments)))-guide_indices) if guide_indices else drawing
                if not visible.playable:raise ValueError('Only motion guides selected')
                # Dashes show the jumps of the route actually played (Signal output › Route minimizing).
                from phosphor.engine.signal_engine import planned_route
                vertices,_,_,travel=planned_route(CUSTOM_OBJECT,visible,e.route_mode)
                for a,b in travel:
                    start=self.pixel(vertices[a,:2]);end=self.pixel(vertices[b,:2])
                    for t in np.arange(0,1,.08):ghost.append((start+(end-start)*t,start+(end-start)*min(1,t+.04)))
            except ValueError:pass
        self.upload(4,lines_mesh(ghost,1),QColor(self.colors['muted']))
        return old


class ShapeBuilder(QWidget):
    applied=Signal(object)
    back=Signal()

    def __init__(self,view_host,parent=None):
        super().__init__(parent)
        self.drawing=Drawing();self.last_applied=self.drawing
        self.past=[];self.future=[];self.pending=[];self.hover_point=None
        self.selected=None;self.selected_edge=None;self.snap_target=None
        self.selected_segments=frozenset();self.box=None;self.box_base=frozenset()
        self.drag_segments=frozenset();self.drag_start=None;self.axis_lock=None
        self.paste_count=0
        self.motion_dialog=None
        self.drag_drawing=None;self.drag_origin=None;self._refreshing=False
        self.tool='Polyline'
        self.route_mode=ROUTE_OFF  # Set by the window from the signal preference.
        outer=QVBoxLayout(self);outer.setContentsMargins(0,0,0,0);outer.setSpacing(8)
        bar=QHBoxLayout();outer.addLayout(bar)
        self.back_button=QPushButton('← Back to scene');self.back_button.clicked.connect(self.back);bar.addWidget(self.back_button)
        bar.addWidget(QLabel('SHAPE BUILDER'))
        self.name=QLineEdit(self.drawing.name);self.name.setMaxLength(64);self.name.setMaximumWidth(220)
        self.name.editingFinished.connect(self.rename);bar.addWidget(self.name);bar.addStretch()
        self.live=QCheckBox('Live audition');self.live.setToolTip('Completed edits update the scene. Unfinished strokes and drags stay local.')
        self.live.toggled.connect(self.audition_changed);bar.addWidget(self.live)
        self.apply_button=QPushButton('Apply to scene');self.apply_button.clicked.connect(self.apply);bar.addWidget(self.apply_button)
        tools=QHBoxLayout();outer.addLayout(tools);self.tools={}
        for name in ('Select','Line','Polyline','Arc','Semicircle','Spline','Circle','Text'):
            button=QPushButton(name);button.setCheckable(True);button.clicked.connect(lambda checked=False,n=name:self.set_tool(n))
            self.tools[name]=button;tools.addWidget(button)
        tools.addStretch();fit=QPushButton('Fit');fit.clicked.connect(self.fit);tools.addWidget(fit)
        tools=QHBoxLayout();outer.addLayout(tools)
        self.undo_button=QPushButton('Undo');self.undo_button.clicked.connect(self.undo);tools.addWidget(self.undo_button)
        self.redo_button=QPushButton('Redo');self.redo_button.clicked.connect(self.redo);tools.addWidget(self.redo_button)
        self.finish_button=QPushButton('Finish stroke ↵');self.finish_button.clicked.connect(self.finish_stroke);tools.addWidget(self.finish_button)
        self.snap=QCheckBox('Snap + join');self.snap.setChecked(True);tools.addWidget(self.snap)
        self.motion_role=QCheckBox('Use as motion path');self.motion_role.toggled.connect(self.change_motion_role);tools.addWidget(self.motion_role)
        self.motion_role.setToolTip('Select one connected path to change its role. Other paths in this drawing keep their own roles.')
        # Text tool: type, then click the canvas to place the letters as ordinary strokes.
        self.text_panel=QWidget();text_row=QHBoxLayout(self.text_panel);text_row.setContentsMargins(0,0,0,0)
        self.text_entry=QLineEdit();self.text_entry.setObjectName('builder_text');self.text_entry.setPlaceholderText('Text to place');self.text_entry.setMaxLength(64)
        text_row.addWidget(self.text_entry,1);text_row.addWidget(QLabel('Size'))
        self.text_size=QDoubleSpinBox();self.text_size.setObjectName('builder_text_size');self.text_size.setRange(.01,50);self.text_size.setDecimals(3);self.text_size.setSingleStep(.05);self.text_size.setValue(.3)
        text_row.addWidget(self.text_size);tools.addWidget(self.text_panel,1);self.text_panel.hide()
        tools.addStretch()
        body=QHBoxLayout();outer.addLayout(body,1)
        self.view=QQuickWindow();self.canvas=ConstructionCanvas(self.view.contentItem(),self)
        self.view.installEventFilter(self)
        self.container=QWidget.createWindowContainer(self.view,self);self.container.setMinimumSize(300,260);body.addWidget(self.container,1)
        self.view.widthChanged.connect(self.resize_canvas);self.view.heightChanged.connect(self.resize_canvas)
        panel=QWidget();panel.setFixedWidth(245);right=QVBoxLayout(panel);right.setContentsMargins(0,0,0,0);body.addWidget(panel)
        self.selection=QLabel('Click a node to edit');self.selection.setWordWrap(True);right.addWidget(self.selection)
        # Node controls hide while whole shapes are selected, when the style rows show instead.
        self.node_panel=QWidget();node_rows=QVBoxLayout(self.node_panel);node_rows.setContentsMargins(0,0,0,0);right.addWidget(self.node_panel)
        self.coordinates=[]
        for axis in ('X','Y'):
            row=QHBoxLayout();row.addWidget(QLabel(axis));spin=QDoubleSpinBox();spin.setRange(-100,100);spin.setDecimals(4);spin.setSingleStep(.05)
            spin.editingFinished.connect(self.numeric_move);row.addWidget(spin);node_rows.addLayout(row);self.coordinates.append(spin)
        self.incident=QComboBox();node_rows.addWidget(self.incident)
        self.radius_panel=QWidget();radius_row=QHBoxLayout(self.radius_panel);radius_row.setContentsMargins(0,0,0,0)
        radius_row.addWidget(QLabel('Radius'));self.radius=QDoubleSpinBox();self.radius.setRange(.0001,100);self.radius.setDecimals(4)
        self.radius.editingFinished.connect(self.edit_radius);radius_row.addWidget(self.radius);right.addWidget(self.radius_panel)
        self.detach_button=QPushButton('Detach selected segment');self.detach_button.clicked.connect(self.detach);node_rows.addWidget(self.detach_button)
        self.flip_button=QPushButton('Flip side');self.flip_button.clicked.connect(self.flip);right.addWidget(self.flip_button)
        # Bold outline width and Z extrusion depth for the selected connected shapes. The rows
        # show only with a selection, so the panel still fits 1000x700 without a scrollbar.
        self.style_panel=QWidget();style_rows=QVBoxLayout(self.style_panel);style_rows.setContentsMargins(0,0,0,0)
        self.style_spins={}
        for key,label,high,step,tip in (('bold','Bold width',MAX_BOLD,.01,'Trace the outline of the strokes fattened to this width. Off = plain lines.'),
                                        ('depth','Extrude depth',MAX_DEPTH,.05,'Front and back copies this far apart along Z, joined at corners. Spin or tilt the shape in Transform to see the depth.')):
            row=QHBoxLayout();caption=QLabel(label);caption.setToolTip(tip);row.addWidget(caption)
            spin=QDoubleSpinBox();spin.setObjectName('style_'+key);spin.setRange(0,high);spin.setDecimals(3);spin.setSingleStep(step)
            spin.setSpecialValueText('Off');spin.setToolTip(tip);spin.editingFinished.connect(lambda key=key:self.edit_style(key))
            row.addWidget(spin);style_rows.addLayout(row);self.style_spins[key]=spin
        right.addWidget(self.style_panel)
        actions=QHBoxLayout();right.addLayout(actions)
        self.delete_button=QPushButton('Delete');self.delete_button.clicked.connect(self.delete_selection);actions.addWidget(self.delete_button)
        self.assign_button=QPushButton('Assign motion…');self.assign_button.clicked.connect(self.show_motion);actions.addWidget(self.assign_button)
        self.assign_button.setToolTip('Select the follower shapes without the motion guide, then assign their path here.')
        files=QHBoxLayout();right.addLayout(files)
        save=QPushButton('Save shape…');save.clicked.connect(self.save_shape);files.addWidget(save)
        load=QPushButton('Load shape…');load.clicked.connect(self.load_shape);files.addWidget(load)
        right.addWidget(QLabel('SCENE OUTPUT'))
        # The preview gives up height first so the controls and status keep theirs at 1000x700;
        # its old 150 px minimum was never met there and only squeezed the status lines.
        self.preview=view_host();self.preview.setMinimumHeight(60);self.preview.setMaximumHeight(220);right.addWidget(self.preview,1)
        self.play_button=QPushButton('▶ PLAY AUDIO');right.addWidget(self.play_button)
        self.info=QLabel();self.info.setWordWrap(True);right.addWidget(self.info)
        help=QLabel('Select: drag defining points · Delete removes · Option/Alt bypasses snapping\nScroll to zoom · middle-drag to pan · Fit frames the drawing\nOpen strokes retrace; dotted links are visible stereo travel.')
        self.hint=QLabel();self.hint.setWordWrap(True);outer.addWidget(self.hint)
        help.setWordWrap(True);outer.addWidget(help)
        self.notice=QLabel();self.notice.setWordWrap(True);outer.addWidget(self.notice)
        self.refresh()

    def resize_canvas(self,*args):self.canvas.setSize(self.view.size());self.canvas.update()

    def eventFilter(self,watched,event):
        # Native window containers can temporarily lose their Quick focus item.
        # Handle canvas commands at the window boundary, not text-entry widgets.
        if watched is self.view and event.type()==QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Escape,Qt.Key.Key_Return,Qt.Key.Key_Enter,Qt.Key.Key_Delete,Qt.Key.Key_Backspace) or any(event.matches(key) for key in (QKeySequence.StandardKey.Undo,QKeySequence.StandardKey.Redo,QKeySequence.StandardKey.Copy,QKeySequence.StandardKey.Cut,QKeySequence.StandardKey.Paste)):
                self.canvas.keyPressEvent(event);return True
        return super().eventFilter(watched,event)

    def begin(self,drawing):
        self.selected_segments=frozenset()
        self.drawing=drawing;self.last_applied=drawing;self.past=[];self.future=[]
        self.live.setChecked(False);self.cancel_gesture();self.selected=None;self.selected_edge=None
        self.set_tool('Polyline');self.name.setText(drawing.name);self.refresh();self.fit()

    @property
    def unapplied(self):return self.drawing!=self.last_applied or bool(self.pending) or self.drag_drawing is not None

    def set_colors(self,colors):
        self.canvas.colors=colors;self.view.setColor(QColor(colors['background']));self.canvas.update()

    def set_tool(self,name):
        self.cancel_gesture();self.tool=name
        for label,button in self.tools.items():button.setChecked(label==name)
        self.view.setCursor(Qt.CursorShape.ArrowCursor if name=='Select' else Qt.CursorShape.CrossCursor)
        self.refresh()

    def segment_kind(self,index):
        if index is None or index<len(self.drawing.edges):return 'Line'
        offset=index-len(self.drawing.edges)
        return self.drawing.curves[offset].kind if offset<len(self.drawing.curves) else 'Segment'

    def refresh(self):
        self.selected_segments=frozenset(k for k in self.selected_segments if k<len(self.drawing.segments))
        self._refreshing=True;self.name.setText(self.drawing.name)
        self.undo_button.setEnabled(bool(self.past));self.redo_button.setEnabled(bool(self.future))
        minimum=3 if self.tool=='Arc' else 2
        self.finish_button.setEnabled(len(self.pending)>=minimum);self.apply_button.setEnabled(self.drawing.playable and not self.pending and self.drag_drawing is None)
        selected_kind=self.segment_kind(self.selected_edge)
        nodes=self.drawing.node_ids(self.selected_segments)
        matching=[g for g in self.drawing.guides if g.enabled and nodes.intersection(g.nodes)]
        single=bool(self.selected_segments) and self.drawing.connected((min(self.selected_segments),))==self.selected_segments
        blocker=QSignalBlocker(self.motion_role)
        self.motion_role.setChecked(bool(matching) or self.drawing.motion_path)
        self.motion_role.setEnabled(single and not self.pending)
        self.assign_button.setEnabled(bool(nodes) and not matching and not self.pending)
        self.radius_panel.setVisible(selected_kind=='Circle' and self.selected_edge is not None)
        if selected_kind=='Circle' and self.selected_edge is not None:
            curve=self.drawing.curves[self.selected_edge-len(self.drawing.edges)]
            coords={i:np.array((x,y)) for i,x,y in self.drawing.nodes}
            self.radius.setValue(float(np.linalg.norm(coords[curve.nodes[1]]-coords[curve.nodes[0]])))
        self.selection.setText(f'Node {self.selected}' if self.selected is not None else f'{selected_kind} selected' if self.selected_edge is not None else 'Select a point or segment to edit')
        if self.selected is None and len(self.selected_segments)>1:self.selection.setText(f'{len(self.selected_segments)} segments selected')
        if matching:self.selection.setText(matching[0].name+' selected')
        position=next((n[1:] for n in self.drawing.nodes if n[0]==self.selected),None)
        for spin,value in zip(self.coordinates,position or (0,0)):spin.setEnabled(position is not None);spin.setValue(value)
        self.incident.clear()
        for k,segment in enumerate(self.drawing.segments):
            if self.selected in segment:self.incident.addItem(f'{self.segment_kind(k)} {k+1}: {segment[0]} → {segment[-1]}',k)
        self.detach_button.setEnabled(self.incident.count()>1)
        self.flip_button.setEnabled(self.selected_edge is not None and selected_kind=='Semicircle' and self.selected is None)
        self.delete_button.setEnabled(self.selected is not None or bool(self.selected_segments))
        style=style_of(self.drawing,self.selected_segments) if self.selected_segments else None
        self.style_panel.setVisible(bool(self.selected_segments))
        self.node_panel.setVisible(self.selected is not None or not self.selected_segments)
        for key,spin in self.style_spins.items():
            spin.setEnabled(bool(self.selected_segments) and not self.pending)
            blocker=QSignalBlocker(spin);spin.setValue(getattr(style,key) if style is not None else 0.)
        self.text_panel.setVisible(self.tool=='Text')
        self.info.setText(f'{len(self.drawing.nodes)}/{MAX_NODES} nodes · {len(self.drawing.segments)}/{MAX_EDGES} segments\n'+('Live audition on' if self.live.isChecked() else 'Live audition off · Apply to hear edits'))
        hints={'Select':'Drag a stroke to move its connected shape. Drag empty space to box-select. Right-click for Copy/Cut/Paste. Shift constrains movement.',
            'Line':'Click the two endpoints.','Polyline':'Click points · Enter finishes · click the first point to close.',
            'Arc':'Arc: click start → point on curve → end. Next: '+('start','point on curve','end')[min(2,len(self.pending))]+'.',
            'Semicircle':'Click the two diameter endpoints. Select the semicircle and use Flip side to reverse its bulge.',
            'Circle':'Click the center, then a point on the circumference. Select the circle to edit its radius.',
            'Spline':'Click the points the smooth curve passes through. Enter finishes; click the first point to close.',
            'Text':'Type text and set its size, then click to place it. Letters become ordinary strokes: select them to move, bold or extrude.'}
        self.hint.setText(hints[self.tool])
        if self.tool=='Select' and matching and any(i not in {n for g in matching for n in g.nodes} for i in nodes):
            self.hint.setText('Select follower shapes without their motion guide to assign motion. Shift-click adds shapes; a box selects whole connected shapes.')
        for label in self.tools:self.tools[label].setChecked(self.tool==label)
        self.canvas.update();self._refreshing=False

    def commit(self,drawing):
        try:
            drawing=reconcile(drawing)
            drawing.validate()
            if drawing.playable:compiled(drawing)
            if drawing.motion_path and drawing.playable:guide_route(drawing)
            validate_routes(drawing)
        except ValueError as error:self.notice.setText(str(error));return False
        if drawing!=self.drawing:
            self.past.append(self.drawing);self.past=self.past[-100:];self.future=[];self.drawing=drawing
        self.notice.setText('');self.refresh()
        if self.live.isChecked():self.apply()
        return True

    def rename(self):
        if not self._refreshing:self.commit(replace(self.drawing,name=self.name.text().strip()))

    def apply(self):
        try:
            if self.pending or self.drag_drawing is not None:raise ValueError('Finish or cancel the current gesture before applying.')
            if not self.drawing.playable:raise ValueError('No playable segments. The scene keeps its last applied shape until you Apply a completed drawing.')
            compiled(self.drawing) # bounded compilation on the GUI thread, cached for audio
        except ValueError as error:self.notice.setText(str(error));return
        self.apply_succeeded=True;self.applied.emit(self.drawing)
        if not self.apply_succeeded:return
        self.last_applied=self.drawing
        self.notice.setText('Applied to scene. Play audio or use Live MIDI to audition.')

    def audition_changed(self,enabled):
        if enabled and self.drawing.playable and not self.pending:self.apply()
        self.refresh()

    def undo(self):
        self.cancel_gesture()
        if not self.past:return
        self.selected_segments=frozenset()
        self.future.append(self.drawing);self.drawing=self.past.pop();self.selected=None;self.selected_edge=None;self.refresh()
        if self.live.isChecked():self.apply()

    def redo(self):
        self.cancel_gesture()
        if not self.future:return
        self.selected_segments=frozenset();self.selected=None;self.selected_edge=None
        self.past.append(self.drawing);self.drawing=self.future.pop();self.refresh()
        if self.live.isChecked():self.apply()

    def nearest(self,p,modifiers,exclude=None):
        if not self.snap.isChecked() or modifiers & Qt.KeyboardModifier.AltModifier:return None
        candidates=[(float(np.linalg.norm(self.canvas.pixel((x,y))-p)),i) for i,x,y in self.drawing.nodes if i!=exclude]
        # Include provisional polyline vertices, so closing a stroke is natural.
        candidates.extend((float(np.linalg.norm(self.canvas.pixel(n[1:])-p)),n[0]) for n in self.pending if n[0]!=exclude)
        if not candidates:return None
        distance,node=min(candidates)
        return node if distance<=12 else None

    def constrained(self,point,anchor,modifiers):
        if not modifiers & Qt.KeyboardModifier.ShiftModifier:self.axis_lock=None;return point
        delta=np.asarray(point)-anchor
        if self.axis_lock is None and np.linalg.norm(delta)*self.canvas.scale>2:self.axis_lock=1 if abs(delta[0])>=abs(delta[1]) else 0
        delta=axis_delta(delta) if self.axis_lock is None else delta.copy()
        if self.axis_lock is not None:delta[self.axis_lock]=0
        return np.asarray(anchor)+delta

    def point(self,p,modifiers,exclude=None,anchor=None):
        position=self.canvas.world(p)
        locked=anchor is not None and bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        if anchor is not None:position=self.constrained(position,np.asarray(anchor),modifiers)
        node=self.nearest(self.canvas.pixel(position),modifiers,exclude)
        if node is not None:
            target=np.array(next(n[1:] for n in (*self.drawing.nodes,*self.pending) if n[0]==node))
            axis=self.axis_lock if self.axis_lock is not None else (1 if abs(position[0]-anchor[0])>=abs(position[1]-anchor[1]) else 0) if locked else None
            if not locked or abs(target[axis]-anchor[axis])<1e-9:position=target
            else:node=None
        self.snap_target=node
        return node,position

    def pick_segment(self,p):
        distances=[]
        for k,path in enumerate(paths(self.drawing)):
            points=self.canvas.pixel(path);a=points[:-1];v=np.diff(points,axis=0)
            t=np.clip(np.sum((p-a)*v,axis=1)/np.maximum(1e-12,np.sum(v*v,axis=1)),0,1)
            distances.append((float(np.linalg.norm(p-(a+t[:,None]*v),axis=1).min()),k))
        return min(distances)[1] if distances and min(distances)[0]<=8 else None

    def press(self,p,modifiers):
        self.notice.setText('')
        if self.tool=='Select':
            self.axis_lock=None
            candidates=[(float(np.linalg.norm(self.canvas.pixel((x,y))-p)),i) for i,x,y in self.drawing.nodes]
            distance,node=min(candidates) if candidates else (100,None)
            self.selected=node if distance<=10 else None;self.selected_edge=None
            if self.selected is not None:
                self.selected_segments=frozenset();self.drag_origin=self.drawing;self.drag_segments=frozenset()
            else:
                hit=self.pick_segment(p)
                if hit is not None:
                    component=self.drawing.connected((hit,))
                    if modifiers & Qt.KeyboardModifier.ShiftModifier:self.selected_segments=self.selected_segments|component
                    elif hit not in self.selected_segments:self.selected_segments=component
                    self.selected_edge=hit;self.drag_origin=self.drawing;self.drag_segments=self.selected_segments;self.drag_start=self.canvas.world(p)
                else:
                    self.box_base=self.selected_segments if modifiers & Qt.KeyboardModifier.ShiftModifier else frozenset()
                    self.selected_segments=self.box_base;self.box=(p.copy(),p.copy())
            self.refresh();return
        if self.tool=='Text':self.place_text(self.canvas.world(p));return
        node,position=self.point(p,modifiers,anchor=self.pending[-1][1:] if self.pending else None)
        if node is None:
            if len({n[0] for n in (*self.drawing.nodes,*self.pending)})>=MAX_NODES:
                self.notice.setText(f'Limit reached: {MAX_NODES} nodes. Finish or simplify this shape.');return
            node=max((n[0] for n in (*self.drawing.nodes,*self.pending)),default=-1)+1
        needed=len(self.pending) if self.tool=='Polyline' else 1
        if needed+len(self.drawing.segments)>MAX_EDGES:
            self.notice.setText(f'Limit reached: {MAX_EDGES} segments. Finish or simplify this shape.');return
        if self.pending and node==self.pending[-1][0]:return
        if self.pending and np.linalg.norm(position-np.array(self.pending[-1][1:]))<1e-8:return
        self.pending.append((node,float(position[0]),float(position[1])))
        self.axis_lock=None
        complete=(len(self.pending)==3 if self.tool=='Arc' else len(self.pending)==2 if self.tool in ('Line','Semicircle','Circle') else len(self.pending)>1 and node==self.pending[0][0])
        if complete:
            if not self.finish_stroke():self.pending.pop();self.refresh()
        else:self.refresh()

    def hover(self,p,modifiers):
        if self.drag_origin is None:
            _,self.hover_point=self.point(p,modifiers,anchor=self.pending[-1][1:] if self.pending else None);self.canvas.update()

    def move(self,p,modifiers):
        if self.box is not None:
            self.box=(self.box[0],p.copy())
            hits={k for k,path in enumerate(paths(self.drawing)) if intersects_box(self.canvas.pixel(path),*self.box)}
            self.selected_segments=self.box_base|self.drawing.connected(hits);self.canvas.update();return
        if self.drag_origin is None:self.hover(p,modifiers);return
        try:
            if self.drag_segments:
                point=self.constrained(self.canvas.world(p),self.drag_start,modifiers)
                self.drag_drawing=self.drag_origin.translate(self.drag_segments,point-self.drag_start)
            else:
                anchor=next(n[1:] for n in self.drag_origin.nodes if n[0]==self.selected)
                node,position=self.point(p,modifiers,self.selected,anchor)
                self.drag_drawing=self.drag_origin.move(self.selected,*position,join=node)
        except ValueError as error:self.drag_drawing=None;self.notice.setText(str(error))
        self.canvas.update()

    def release(self):
        if self.box is not None:self.box=None;self.refresh();return
        if self.drag_origin is None:return
        result=self.drag_drawing;self.drag_drawing=None;self.drag_origin=None
        group=bool(self.drag_segments);self.drag_segments=frozenset();self.drag_start=None;self.axis_lock=None
        if result is not None:
            if self.snap_target is not None and not group:self.selected=self.snap_target
            self.commit(result)
        self.snap_target=None;self.canvas.update()

    def finish_stroke(self):
        minimum=3 if self.tool=='Arc' else 2
        if len(self.pending)<minimum:return False
        nodes={n[0]:n for n in self.drawing.nodes};nodes.update((n[0],n) for n in self.pending)
        edges=list(self.drawing.edges);keys={tuple(sorted(e)) for e in edges}
        curves=self.drawing.curves
        if self.tool in ('Arc','Semicircle','Spline','Circle'):
            curves=(*curves,Curve(self.tool,tuple(n[0] for n in self.pending)))
        else:
            for a,b in zip(self.pending,self.pending[1:]):
                edge=(a[0],b[0]);key=tuple(sorted(edge))
                if edge[0]!=edge[1] and key not in keys:edges.append(edge);keys.add(key)
        drawing=replace(self.drawing,nodes=tuple(nodes.values()),edges=tuple(edges),curves=curves)
        pending=self.pending
        self.pending=[];self.hover_point=None;self.snap_target=None
        if self.commit(drawing):
            self.selected=None
            self.selected_edge=len(drawing.segments)-1 if self.tool in ('Arc','Semicircle','Spline','Circle') else None
            self.selected_segments=self.drawing.connected((self.selected_edge,)) if self.selected_edge is not None else frozenset()
            self.refresh();return True
        self.pending=pending;self.refresh();return False

    def place_text(self,position):
        """Add the typed text, centred on position, as a new selected group of strokes."""
        try:
            letters=text_drawing(self.text_entry.text(),self.text_size.value())
            result,selection=self.drawing.paste(letters,np.asarray(position,dtype=float))
        except ValueError as error:self.notice.setText(str(error));return False
        if self.commit(result):
            self.selected=None;self.selected_edge=None;self.selected_segments=selection;self.refresh()
            self.notice.setText('Placed text as strokes. Select it to move, bold or extrude.');return True
        return False

    def edit_style(self,key):
        if self._refreshing or not self.selected_segments:return
        value=self.style_spins[key].value()
        try:drawing=set_style(self.drawing,self.selected_segments,**{key:value})
        except ValueError as error:self.notice.setText(str(error));self.refresh();return
        selection=self.selected_segments
        if self.commit(drawing):self.selected_segments=selection;self.refresh()

    def cancel_gesture(self):
        if self.box is not None:self.selected_segments=self.box_base
        self.box=None;self.drag_segments=frozenset();self.drag_start=None;self.axis_lock=None
        self.pending=[];self.hover_point=None;self.snap_target=None;self.drag_drawing=None;self.drag_origin=None
        self.refresh()

    def numeric_move(self):
        if self._refreshing or self.selected is None:return
        try:self.commit(self.drawing.move(self.selected,*(s.value() for s in self.coordinates)))
        except ValueError as error:self.notice.setText(str(error));self.refresh()

    def detach(self):
        if self.selected is None or self.incident.currentData() is None:return
        drawing,node=self.drawing.detach(self.selected,self.incident.currentData());self.selected=node;self.commit(drawing)

    def flip(self):
        if self.selected_edge is not None:self.commit(self.drawing.flip(self.selected_edge))

    def change_motion_role(self,enabled):
        try:
            drawing=set_role(self.drawing,self.selected_segments,enabled)
        except ValueError as error:
            self.refresh();QMessageBox.information(self,'Edit motion path',str(error));return
        self.commit(drawing)

    def show_motion(self):
        if not self.selected_segments:return
        if self.motion_dialog is not None:self.motion_dialog.raise_();return
        from phosphor.editor.drawing_motion_dialog import BuilderMotionDialog
        self.motion_dialog=BuilderMotionDialog(self);self.motion_dialog.finished.connect(lambda result:setattr(self,'motion_dialog',None));self.motion_dialog.show()

    def edit_radius(self):
        if self._refreshing or self.selected_edge is None or self.segment_kind(self.selected_edge)!='Circle':return
        curve=self.drawing.curves[self.selected_edge-len(self.drawing.edges)]
        coords={i:np.array((x,y)) for i,x,y in self.drawing.nodes};center=coords[curve.nodes[0]];delta=coords[curve.nodes[1]]-center
        point=center+delta/max(1e-12,np.linalg.norm(delta))*self.radius.value()
        self.commit(self.drawing.move(curve.nodes[1],*point))

    def delete_selection(self):
        self.cancel_gesture()
        if self.selected is not None:
            drawing=self.drawing.delete(self.selected);self.selected=None
        elif self.selected_segments:
            drawing=self.drawing.without(self.selected_segments);self.selected_edge=None;self.selected_segments=frozenset()
        else:return
        self.commit(drawing)

    def clipboard_drawing(self):
        mime=QApplication.clipboard().mimeData()
        if mime is None or not mime.hasFormat(CLIPBOARD_TYPE):return None
        try:
            raw=bytes(mime.data(CLIPBOARD_TYPE))
            if len(raw)>200000:return None
            value=json.loads(raw)
            if value.get('format')!='phosphor-geometry-selection' or type(value.get('version')) is not int or value.get('version')!=1:return None
            drawing=Drawing.read(value['drawing']);compiled(drawing);return drawing
        except (ValueError,TypeError,KeyError,AttributeError):return None

    def copy_selection(self):
        if not self.selected_segments:self.notice.setText('Select a shape or drag a selection box first.');return False
        drawing=self.drawing.subset(self.selected_segments,keep_motion=True)
        value=json.dumps(dict(format='phosphor-geometry-selection',version=1,drawing=drawing.document()))
        mime=QMimeData();mime.setData(CLIPBOARD_TYPE,value.encode('utf-8'));mime.setText(value)
        QApplication.clipboard().setMimeData(mime);self.paste_count=0
        self.notice.setText('Copied selection. Paste creates an independent copy.');return True

    def cut_selection(self):
        if self.copy_selection():self.selected=None;self.delete_selection()

    def paste_selection(self,position=None):
        drawing=self.clipboard_drawing()
        if drawing is None:self.notice.setText('The clipboard has no phosphor geometry.');return
        if self.pending or self.drag_origin is not None or self.box is not None:self.notice.setText('Finish or cancel the gesture before pasting.');return
        points=np.array([n[1:] for n in drawing.nodes])
        delta=np.array([.12,-.12])*(self.paste_count+1) if position is None else np.asarray(position)-(points.min(axis=0)+points.max(axis=0))/2
        try:result,selection=self.drawing.paste(drawing,delta)
        except ValueError as error:self.notice.setText(str(error));return
        if self.commit(result):
            self.paste_count+=1;self.selected=None;self.selected_edge=None;self.selected_segments=selection;self.refresh()

    def context_menu(self,p):
        if self.tool!='Select':return
        if self.pending or self.drag_origin is not None or self.box is not None:self.cancel_gesture()
        hit=self.pick_segment(p)
        if hit is not None and hit not in self.selected_segments:
            self.selected_segments=self.drawing.connected((hit,));self.selected_edge=hit
        self.selected=None;self.refresh()
        menu=QMenu(self);actions={}
        for text,key,callback,enabled in (
            ('Copy',QKeySequence.StandardKey.Copy,self.copy_selection,bool(self.selected_segments)),
            ('Cut',QKeySequence.StandardKey.Cut,self.cut_selection,bool(self.selected_segments)),
            ('Paste',QKeySequence.StandardKey.Paste,lambda:self.paste_selection(self.canvas.world(p)),self.clipboard_drawing() is not None)):
            action=menu.addAction(text);action.setShortcut(QKeySequence(key));action.setEnabled(enabled);actions[action]=callback
        self.context_popup=menu
        try:chosen=menu.exec(self.view.mapToGlobal(QPoint(*map(round,p))))
        finally:self.context_popup=None;menu.deleteLater()
        if chosen in actions:actions[chosen]()

    def fit(self):
        if self.drawing.nodes:
            points=np.concatenate((np.array([n[1:] for n in self.drawing.nodes]),*paths(self.drawing)));center=(points.min(axis=0)+points.max(axis=0))/2
            self.canvas.zoom=float(np.clip(1.8/max(.5,np.ptp(points,axis=0).max()),.005,12))
            self.canvas.pan=-center*[self.canvas.scale,-self.canvas.scale]
        else:self.canvas.zoom=1.;self.canvas.pan=np.zeros(2)
        self.canvas.update()

    def save_shape(self):
        if self.pending:self.notice.setText('Finish or cancel the stroke before saving.');return
        path,_=QFileDialog.getSaveFileName(self,'Save reusable shape',self.drawing.name+'.shape.json','Shape JSON (*.json)')
        if not path:return
        if not path.lower().endswith('.json'):path+='.shape.json'
        try:atomic_json(path,dict(format='phosphor-line-shape',version=5 if self.drawing.styles else 4 if self.drawing.guides or self.drawing.attachments else 3 if self.drawing.motion_path or any(c.kind=='Circle' for c in self.drawing.curves) else 2 if self.drawing.curves else 1,drawing=self.drawing.document()))
        except (OSError,ValueError) as error:self.notice.setText(str(error))

    def load_shape(self):
        path,_=QFileDialog.getOpenFileName(self,'Load reusable shape','','Shape JSON (*.json)')
        if not path:return
        try:
            if Path(path).stat().st_size>200000:raise ValueError('Shape file is too large.')
            value=json.loads(Path(path).read_text())
            if value.get('format')!='phosphor-line-shape' or type(value.get('version')) is not int or value.get('version') not in (1,2,3,4,5):raise ValueError('Unsupported shape file.')
            drawing=Drawing.read(value['drawing'])
            self.cancel_gesture();self.selected=None;self.selected_edge=None;self.selected_segments=frozenset()
            if self.commit(drawing):self.fit()
        except (OSError,ValueError,KeyError,TypeError,AttributeError) as error:self.notice.setText(str(error))
