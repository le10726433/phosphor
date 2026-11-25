"""Verify readable control layout and text editing on native Qt or --headless."""
import _path  # noqa: F401  (makes the phosphor package importable)
import os
import sys
if '--headless' in sys.argv:os.environ['QT_QPA_PLATFORM']='offscreen'
from types import SimpleNamespace
from unittest.mock import patch
from PySide6.QtWidgets import QApplication,QLabel,QPushButton
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt, QPointF
from dataclasses import replace
from phosphor.editor import qt_app as q
from phosphor.engine.audio_clips import AudioTrack
from phosphor.engine.composition import COMPOSER
from phosphor.engine.track_effects import EFFECTS, effect_value, new_effect, with_lane
from phosphor.editor.composer_view import AUDIO_TRACK
from _screen import check_label_fits
q.configure_graphics();app=QApplication([])


def check_panels(style, mode):
    w=q.Window(SimpleNamespace(scene=None,ready_file=None,workspace='Sandbox'))
    w.apply_preferences(dict(w.preferences, style=style, style_mode=mode))
    w.show();QTest.qWait(100)
    w.add_object('Tilting circle');w.transform_panel.add('Map')  # Its three formula rows show in SCENE › Transform.
    w.scene.saved=w.scene.document()  # New scenes are empty; start from one circle.
    # Native clicks go to the top child, so the text field must not cover either arrow.
    from PySide6.QtWidgets import QDoubleSpinBox,QStyle,QStyleOptionSpinBox
    probe=QDoubleSpinBox(w);probe.setRange(0,9);probe.setValue(4);probe.setGeometry(0,0,200,32);probe.show();QTest.qWait(30)
    option=QStyleOptionSpinBox();probe.initStyleOption(option)
    for control in (QStyle.SubControl.SC_SpinBoxUp,QStyle.SubControl.SC_SpinBoxDown):
        arrow=probe.style().subControlRect(QStyle.ComplexControl.CC_SpinBox,option,control,probe)
        assert arrow.isValid() and not probe.lineEdit().geometry().intersects(arrow),(control,arrow,probe.lineEdit().geometry())
    probe.close();probe.deleteLater()
    # An unwritable temp folder must degrade the arrows, never block startup.
    working=w._arrow_directory;w._arrow_directory=SimpleNamespace(filePath=lambda name:str(q.HERE/'.missing-arrow-folder'/name));w._styled_theme=None
    w.apply_preferences(w.preferences);assert 'image:url' not in w.styleSheet() and 'up-arrow' in w.styleSheet()
    if style=='Modern':  # No image-less indicator or chevron rules: Qt paints its native checkmark and arrows.
        sheet=w.styleSheet();assert '::indicator' not in sheet and 'QComboBox::down-arrow' not in sheet and '::menu-arrow' not in sheet,sheet
    w._arrow_directory=working;w._styled_theme=None;w.apply_preferences(w.preferences);assert 'image:url' in w.styleSheet()
    def check_fits(*where):
        QTest.qWait(30)
        scroll=w.panels[w.current_panel]
        assert scroll.horizontalScrollBar().maximum()==0,(style,mode,*where,'horizontal')
        for label in scroll.findChildren(QLabel):
            if label.isVisible():
                needed=label.heightForWidth(label.width())
                assert needed<=label.height()+1,(style,mode,*where,label.text(),needed,label.height())
    from tests.example_midi import example_midi
    w.scene.change('midi_file',str(example_midi('a_long_example_melody_file_name.mid')))
    from phosphor.editor.layout_metrics import PANEL_RANGE,PANEL_DEFAULT
    for width,height,panel in ((1000,700,PANEL_RANGE[0]),(1000,700,PANEL_DEFAULT),(1220,840,PANEL_DEFAULT),(1600,1000,PANEL_DEFAULT),(1600,1000,PANEL_RANGE[1])):
        w.resize(width,height);QTest.qWait(50);w.set_panel_width(panel);QTest.qWait(50)  # UI2: the narrowest and widest side panel.
        for name in q.PANELS:
            w.show_panel(name)
            nested=w.scene_sections if name==q.PANEL_SCENE else w.appearance_editor.tabs if name==q.PANEL_APPEARANCE else None
            for section in range(nested.count() if nested else 1):
                if nested:nested.setCurrentIndex(section)
                check_fits(width,height,panel,name,section)
            if name==q.PANEL_INPUT:
                with patch.object(w,'refresh_ports'):
                    for source in (*q.SOURCES,q.MIDI_SOURCE):
                        w.edit('source',source);check_fits(width,height,panel,name,source)
                        assert w.tempo_panel.isVisible()==(source in (q.SCALE_NAME,q.MIDI_FILE_SOURCE)),source
                        assert w.live_panel.isVisible()==(source==q.MIDI_SOURCE) and w.tone_note.isVisible()==(source==q.TONE_SOURCE) and not w.tone_panel.isVisible()
                w.edit('source',q.SCALE_NAME)
    # UI1: the equation and recurrence help lines (and T5's Variables row) are never clipped.
    for kind,panel in (('Parametric equation',w.function_panel),(q.RECURRENCE_OBJECT,w.recurrence_panel)):
        w.resize(1000,700);w.show_panel(q.PANEL_SCENE);w.scene_sections.setCurrentIndex(0)
        next(a for a in w.add_button.menu().actions() if a.text()==kind).trigger();QTest.qWait(50)
        for width in (None,PANEL_RANGE[0],340,520,PANEL_RANGE[1]):  # The default, the narrowest, UI1's 340 (it scrolled sideways before UI2), wide, widest.
            if width:w.set_panel_width(width)
            check_fits(1000,700,kind,width)
            QTest.qWait(30)
            check_label_fits(panel.findChild(QLabel,'function_help'),kind,width,viewport=w.panels[q.PANEL_SCENE].viewport())
        w.set_panel_width(PANEL_DEFAULT)
        w.scene_sections.setCurrentIndex(1);w.transform_panel.add_variable();QTest.qWait(50)
        check_fits(1000,700,kind,'Variables')
        w.shape_action('remove');QTest.qWait(30)
    w.inspect_cycle(True)  # INSPECT CYCLE has no bar button: View › Inspect one cycle opens it.
    for width,height in ((1000,700),(1220,840),(1600,1000)):
        w.resize(width,height);QTest.qWait(80);assert w.inspector.inspection is not None,w.inspector.error;check_fits(width,height,q.PANEL_INSPECT)
    w.inspector.pin();w.resize(1000,700);QTest.qWait(80);check_fits(1000,700,q.PANEL_INSPECT,'pinned')
    w.inspect_cycle(False);w.inspector.clear_reference()
    w.resize(1000,700);w.show_panel(q.PANEL_SCENE);w.scene_sections.setCurrentIndex(1);QTest.qWait(50)
    assert w.transform_panel.editors['Map'][0].isVisible() and w.transform_panel.map_presets.isVisible();check_fits(1000,700,'Map')
    assert all(f.caption.width()>=f.caption.sizeHint().width() for f in w.transform_panel.editors['Map'][1])  # X′ Y′ Z′ unclipped.
    w.edit('source',q.TONE_SOURCE);w.show_panel(q.PANEL_SCENE);w.scene_sections.setCurrentIndex(0)
    assert w.tone_panel.isVisible() and w.parameters['tone_frequency'].isVisible()  # The scene's pitch lives with its objects.
    tone=w.parameters['tone_frequency'];assert isinstance(tone,q.SliderInput)
    tone.entry.setText('220');tone.typed();assert w.scene.settings.tone_frequency==220.
    tone.slider.setValue(tone.slider.maximum());assert abs(w.scene.settings.tone_frequency-q.TONE_RANGE[1])<1e-6  # Log slider spans the range.
    assert w.shape_list.mapTo(w.sidebar,w.shape_list.rect().topLeft()).y()<w.tone_panel.mapTo(w.sidebar,w.tone_panel.rect().topLeft()).y()  # Object list stays on top.
    w.resize(1000,700);QTest.qWait(50);check_fits(1000,700,q.PANEL_SCENE,'tone')
    w.edit('source',q.SCALE_NAME);assert not w.tone_panel.isVisible()
    add=next(a for a in w.add_button.menu().actions() if a.text()=='Text');add.trigger()
    assert w.scene.shape.solid=='Text' and not w.scene.shape.spin_z
    assert w.text_panel.isVisible()
    w.text_entry.setText('HELLO 2026!');QTest.keyClick(w.text_entry,Qt.Key.Key_Return)
    assert w.scene.shape.text=='HELLO 2026!' and w.scene.dirty
    assert 'HELLO 2026!' in w.shape_list.currentItem().text()
    w.resize(1000,700);QTest.qWait(50)
    assert w.panels[q.PANEL_SCENE].horizontalScrollBar().maximum()==0  # Panels may scroll vertically, never sideways.
    for width in (PANEL_RANGE[0],340,520,PANEL_RANGE[1]):
        w.resize(1220,840);w.set_panel_width(width);QTest.qWait(40)
        assert w.panels[q.PANEL_SCENE].horizontalScrollBar().maximum()==0,(width,w.panels[q.PANEL_SCENE].viewport().width(),w.panels[q.PANEL_SCENE].widget().minimumSizeHint().width(),w.sidebar.width())
    w.show_panel(q.PANEL_INPUT);w.monitor.setCurrentIndex(q.MONITOR_SYSTEM);w.monitor_changed(q.MONITOR_SYSTEM);QTest.qWait(50)
    assert w.panels[q.PANEL_INPUT].horizontalScrollBar().maximum()==0
    w.monitor.setCurrentIndex(q.MONITOR_GENERATED);w.monitor_changed(q.MONITOR_GENERATED);w.show_panel(q.PANEL_SCENE)
    w.shape_action('duplicate');w.text_entry.setText('SECOND');QTest.keyClick(w.text_entry,Qt.Key.Key_Return)
    assert w.scene.settings.shapes[-2].text=='HELLO 2026!'
    w.select_shape(0);assert not w.text_panel.isVisible()
    w.resize(1220,840);w.select_shape(1);QTest.qWait(50)
    if style == 'Classic':
        (q.HERE/'.user').mkdir(exist_ok=True);w.grab().save(str(q.HERE/'.user/responsive-text-controls.png'))
    w.timer.stop();w.shutdown_audio();w.release_views();w.force_close=True;w.close();app.processEvents()
    return w  # Kept, never deleted: deleting a Window before exit crashes Qt's teardown (also in 0.90.0).


def composer_window(style):
    w=q.Window(SimpleNamespace(scene=None,ready_file=None,workspace=COMPOSER))
    w.apply_preferences(dict(w.preferences,style=style,style_mode='Dark'))
    effect=new_effect(next(iter(EFFECTS)))
    tracks=tuple(AudioTrack(f'Track {i+1}',effects=(effect,)) for i in range(6))
    w.set_project(replace(w.project,audio_tracks=tracks));w.commit_history()
    w.resize(1000,700);w.show();QTest.qWait(100);w.timer.stop()
    w.composer_panel.timeline.set_roll_option('roll_audio_gain_lane',False)
    return w


def check_effects_panel(w):
    w.resize(1000, 700)
    w.hide_panel()  # Give the left splitter room to exercise its full width.
    fx = w.open_track_effects(0)
    fx.set_effects((new_effect('delay'), new_effect('compressor')), select=1)
    fx.rows['release'].spin.setValue(125.5)
    assert effect_value(fx.track_data().effects[1], 'release') == 125.5
    automated = []
    for effect in fx.track_data().effects:
        for field in EFFECTS[effect.kind].fields:
            if field.choices is None: effect = with_lane(effect, field.name, ((0., .5, 'hold'),))
        automated.append(effect)
    fx.set_effects(automated, select=0)
    for style, mode in (('Classic', 'System'), ('Modern', 'Dark'), ('Modern', 'Light')):
        w.apply_preferences(dict(w.preferences, style=style, style_mode=mode))
        for width in (220, 280, 640):
            total = sum(w.browser_split.sizes())
            w.browser_split.setSizes([width, total - width]); QTest.qWait(60)
            where = (style, mode, width)
            assert w.width() == 1000 and w.height() == 700 and w.left_panel.width() == width, where
            assert fx.scroll.horizontalScrollBar().maximum() == 0, where
            viewport = fx.scroll.viewport()
            for index, kind in enumerate(('delay', 'compressor')):
                fx.chain.setCurrentRow(index); app.processEvents()
                assert fx.shown_kind == kind
                if kind == 'compressor':
                    assert fx.rows['release'].spin.value() == 125.5
                for row in fx.rows.values():
                    rects = []
                    for control in (row.label, row.slider, row.spin, row.lane):
                        rect = control.rect().translated(control.mapTo(row, control.rect().topLeft()))
                        assert row.rect().contains(rect) and rect.width() >= control.sizeHint().width(), (where, control, rect)
                        assert rect.height() >= control.sizeHint().height(), (where, control, rect)
                        left = control.mapTo(viewport, control.rect().topLeft()).x()
                        assert 0 <= left <= viewport.width() - control.width(), (where, control)
                        assert all(not rect.intersects(other) for other in rects), (where, rect, rects)
                        rects.append(rect)
                    assert rects[2].top() <= rects[3].center().y() <= rects[2].bottom(), where
                last = list(fx.rows.values())[-1]
                assert last.lane.text() == 'Lane ~'
                bar = fx.scroll.verticalScrollBar()
                bar.setValue(bar.maximum()); app.processEvents()
                rect = last.rect().translated(last.mapTo(viewport, last.rect().topLeft()))
                assert viewport.rect().contains(rect), (where, kind, rect, viewport.rect())
                bar.setValue(0)


def transport_geometry(w):
    panel=w.composer_panel
    return panel.clip_row_wrapped,[(x.objectName() or type(x).__name__,x.geometry().getRect(),x.minimumWidth()) for x in panel.transport_widgets()]


def check_header_targets(w,style,where):
    """Scroll the tracks so the last visible one shows 30 to 42 px (an effects track, partly
    visible), then compare the painted header rectangles with the hit targets."""
    view=w.composer_panel.timeline
    rows=view.track_rows();total,shown=view.track_extent;assert total>shown,(total,shown)
    for index in range(len(rows)):
        scroll=index*AUDIO_TRACK+36-shown
        if 0<=scroll<=total-shown:break
    view.track_scroll=scroll;view.header_painted.clear();view.update();view.repaint();QTest.qWait(20)  # update(): a new static layer.
    rows=view.track_rows();compressed=0
    for row in rows:
        visible=view.audio_top+view.track_extent[1]-row.top
        if row.top>=view.audio_end or row.top+row.height<=view.audio_top:continue
        if style=='Modern' and 30<=visible<43:compressed+=1
        drawn=view.header_painted.get(row.index);assert drawn is not None,(style,where,row.index)
        targets=view.header_hit_targets(row);kinds=dict(targets)
        for kind,rect in drawn.items():
            if rect is None:continue
            if kind=='fx':assert kinds[kind].contains(rect),(style,where,kind,kinds[kind],rect)  # A pixel of slack, clipped.
            else:assert kinds[kind]==rect,(style,where,kind,kinds[kind],rect)
        for i,(a,ra) in enumerate(targets):
            for b,rb in targets[i+1:]:
                assert not ra.intersects(rb),(style,where,row.index,a,ra,b,rb)
            centre=ra.center()
            assert view.header_target(row,centre)==a,(style,where,a,centre)
            tip=view.header_tooltip(row,centre.x(),centre.y())
            words={'mute':'Mute','solo':'Solo','fx':'Effects','gain':'Track gain','pan':'Pan','name':row.track.name,'lamp':row.track.name+' meter'}
            if a=='phase':assert 'Drag up or down' in tip,tip
            else:assert tip.startswith(words[a]),(a,tip)
    if style=='Modern':assert compressed,(where,'no partly visible track')


def check_composer_round_trip():
    """Classic -> Modern -> Classic in one window gives a fresh Classic window's transport."""
    with patch.object(q,'write_workspace',lambda *args:None),patch.object(q.Window,'remember_file',lambda *args:None):
        fresh,trip=composer_window('Classic'),composer_window('Classic')
        panel=fresh.composer_panel;margins=panel.layout().contentsMargins()
        threshold=(panel._preferred_width(panel.transport_row,panel.clip_controls)+panel.transport_row.spacing()
                   +panel._preferred_width(panel.clip_controls.layout())+margins.left()+margins.right())
        edge=fresh.width()-panel.width()+threshold
        for width in (1000,edge+1,edge-1):
            for w in (fresh,trip):w.resize(width,700)
            QTest.qWait(80)
            check_header_targets(fresh,'Classic',width)
            for style in ('Modern','Classic'):
                trip.apply_preferences(dict(trip.preferences,style=style));QTest.qWait(80)
                check_header_targets(trip,style,width)
            assert trip.width()==fresh.width()==max(width,trip.minimumWidth()),(width,trip.width(),fresh.width())
            assert transport_geometry(trip)==transport_geometry(fresh),(width,transport_geometry(trip),transport_geometry(fresh))
            if width!=1000:assert fresh.width()==width and fresh.composer_panel.clip_row_wrapped==(width<edge),(width,edge,fresh.width())  # Either side of the wrap.
        check_effects_panel(trip)
        for w in (fresh,trip):
            w.timer.stop();w.shutdown_audio();w.release_views();w.force_close=True;w.close()
        app.processEvents()
        return fresh,trip


windows=[check_panels(style, mode) for style, mode in (('Classic', 'System'), ('Modern', 'Dark'), ('Modern', 'Light'))]
windows+=check_composer_round_trip()
print('Responsive controls and text object integration passed.')
