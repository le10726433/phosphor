"""Editor state shared by Qt controls, scene files and the audio worker."""
from dataclasses import replace
from app_files import scene_document, read_scene, atomic_json
from signal_engine import Settings, Shape, scene_shapes, validate
import uuid


class Scene:
    def __init__(self):
        self.settings = replace(Settings(), travel_mode='Faster travel', travel_speed=64.,
                                shapes=(Shape(solid='Tilting circle',rotation_seconds=None),))
        self.mode, self.device = 'mixed', ''
        self.selected = 0
        self.path = None
        self.saved = self.document()

    def document(self): return scene_document(self.settings, self.mode, self.device)

    @property
    def dirty(self): return self.document() != self.saved

    @property
    def shape(self): return self.settings.shapes[self.selected]

    def change(self, key, value):
        if key in vars(self.shape):
            shapes = list(self.settings.shapes)
            shapes[self.selected] = replace(self.shape, **{key:value})
            candidate = replace(self.settings, shapes=tuple(shapes))
        else:
            candidate = replace(self.settings, **{key:value})
        validate(candidate)
        self.settings = candidate

    def add(self, duplicate=False, text=False):
        added = self.shape if duplicate else Shape(solid='Text',text='HELLO',rotation_seconds=None) if text else Shape()
        if duplicate and added.uid:added=replace(added,uid=uuid.uuid4().hex)
        self.settings = replace(self.settings, shapes=(*self.settings.shapes, added))
        self.selected = len(self.settings.shapes)-1

    def add_drawing(self, drawing):
        candidate = replace(self.settings, shapes=(*self.settings.shapes, Shape(solid='Custom shape', custom=drawing, rotation_seconds=None)))
        validate(candidate)
        self.settings = candidate
        self.selected = len(candidate.shapes)-1

    def add_model(self, model, replace_selected=False):
        shape = Shape(solid='3D model', model=model)
        shapes = list(self.settings.shapes)
        if replace_selected: shapes[self.selected] = replace(self.shape, solid='3D model', model=model)
        else: shapes.append(shape)
        candidate = replace(self.settings, shapes=tuple(shapes))
        validate(candidate)
        self.settings = candidate
        if not replace_selected: self.selected = len(shapes)-1

    def remove(self):
        if len(self.settings.shapes) > 1:
            shapes = list(self.settings.shapes); shapes.pop(self.selected)
            ids={s.uid for s in shapes if s.uid}
            shapes=[replace(s,motion=None) if s.motion is not None and s.motion.guide_id not in ids else s for s in shapes]
            self.settings = replace(self.settings, shapes=tuple(shapes))
            self.selected = min(self.selected, len(shapes)-1)

    def save(self, path):
        document = self.document()
        atomic_json(path, document)
        self.path, self.saved = path, document

    def load(self, path):
        settings, mode, device = read_scene(path)
        self.settings = replace(settings, shapes=tuple(scene_shapes(settings)))
        self.mode, self.device, self.path, self.selected = mode, device, path, 0
        self.saved = self.document()
