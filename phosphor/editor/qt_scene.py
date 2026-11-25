"""Editor state shared by Qt controls, scene files and the audio worker."""
from dataclasses import replace
from phosphor.storage.app_files import scene_document, scene_state, read_scene, atomic_json
from phosphor.engine.signal_engine import Settings, Shape, validate
from phosphor.engine.object_replace import new_shape
import uuid


class Scene:
    """A new scene has no objects; selected is -1 whenever the scene is empty."""
    def __init__(self):
        self.settings = replace(Settings(), travel_mode='Faster travel', travel_speed=64.)
        self.mode, self.device = 'drawing', ''
        self.selected = -1
        self.path = None
        self.mark_saved()

    def document(self): return scene_document(self.settings, self.mode, self.device)

    def state(self):
        """Compares equal exactly when document() does, without serializing 3D models."""
        return scene_state(self.settings, self.mode, self.device)

    def mark_saved(self):
        """The current scene is what is on disk (or a clean new scene); saved = None
        marks a scene that is always unsaved."""
        self.saved = self.state()

    @property
    def dirty(self): return self.state() != self.saved

    @property
    def shape(self):
        """The selected object, or None in an empty scene."""
        return self.settings.shapes[self.selected] if 0 <= self.selected < len(self.settings.shapes) else None

    def change(self, key, value):
        if key in vars(Shape()):
            if self.shape is None: raise ValueError('Add an object to the scene first.')
            shapes = list(self.settings.shapes)
            shapes[self.selected] = replace(self.shape, **{key:value})
            candidate = replace(self.settings, shapes=tuple(shapes))
        else:
            candidate = replace(self.settings, **{key:value})
        validate(candidate)
        self.settings = candidate

    def change_object(self, values):
        """Change several fields of the selected object in one validated step ({field: value})."""
        if self.shape is None: raise ValueError('Add an object to the scene first.')
        shapes = list(self.settings.shapes)
        shapes[self.selected] = replace(self.shape, **values)
        candidate = replace(self.settings, shapes=tuple(shapes))
        validate(candidate)
        self.settings = candidate

    def append(self, shape):
        candidate = replace(self.settings, shapes=(*self.settings.shapes, shape))
        validate(candidate)
        self.settings = candidate
        self.selected = len(candidate.shapes)-1

    def add(self, solid='Cube'):
        """Add a new object of a built-in kind: a solid, text or parametric equations."""
        self.append(new_shape(solid))  # Object Replace gives a new type the same defaults.

    def duplicate(self):
        if self.shape is None: return
        added = self.shape
        if added.uid: added = replace(added, uid=uuid.uuid4().hex)
        self.append(added)

    def add_drawing(self, drawing):
        self.append(Shape(solid='Custom shape', custom=drawing))

    def add_model(self, model):
        self.append(Shape(solid='3D model', model=model))

    def remove(self):
        if self.shape is None: return
        shapes = list(self.settings.shapes); shapes.pop(self.selected)
        ids = {s.uid for s in shapes if s.uid}
        shapes = [replace(s, motion=None) if s.motion is not None and s.motion.guide_id not in ids else s for s in shapes]
        self.settings = replace(self.settings, shapes=tuple(shapes))
        self.selected = min(self.selected, len(shapes)-1)

    def save(self, path):
        document = self.document()
        atomic_json(path, document)
        self.path = path
        self.mark_saved()

    def load(self, path, notices=None):
        settings, mode, device = read_scene(path, notices)  # notices: variables renamed for p, if any.
        self.settings = settings
        self.mode, self.device, self.path = mode, device, path
        self.selected = 0 if settings.shapes else -1
        self.mark_saved()
