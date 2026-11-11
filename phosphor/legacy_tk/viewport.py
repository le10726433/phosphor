"""Retained preview drawing with cached backgrounds and inexpensive raster glow.

Only presentation lives here: the sharp trace receives every preview sample.
Glow is reduced-resolution and rate-limited; audio generation never uses it.
"""
import time
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageTk
from phosphor.display.crt_theme import blend


class Viewport:
    GLOW_INTERVAL = 1 / 15
    GLOW_REDUCTION = 3

    def __init__(self, canvas):
        self.canvas = canvas
        self.background_key = None
        self.background_image = None
        self.background_photo = self.glow_photo = None
        self.background_builds = 0
        self.glow_builds = 0
        self.last_glow = float('-inf')
        self.coordinates = None
        self.glow_coordinates = None
        self.glow_enabled = False
        self.background_id = canvas.create_image(0, 0, anchor='nw', tags='viewport_background')
        self.glow_id = canvas.create_image(0, 0, anchor='nw', state='hidden', tags='trace_glow')
        self.trace_id = canvas.create_line(0, 0, 0, 0, width=1, tags='viewport_trace')

    def prepare_background(self, width, height, colors, preferences):
        key = (width, height, preferences['palette'], preferences['grid_divisions'], preferences['crt_scanlines'])
        if key == self.background_key:
            return False
        image = Image.new('RGB', (width, height), colors['background'])
        drawing = ImageDraw.Draw(image)
        if preferences['crt_scanlines']:
            color = blend(colors['background'], colors['grid'], .25)
            for y in range(3, height, 4):
                drawing.line((0, y, width, y), fill=color)
        divisions = preferences['grid_divisions']
        for index in range(1, divisions):
            x, y = round(width * index / divisions), round(height * index / divisions)
            drawing.line((x, 0, x, height), fill=colors['grid'])
            drawing.line((0, y, width, y), fill=colors['grid'])
        for x, y, sx, sy in ((7, 7, 1, 1), (width-8, 7, -1, 1), (7, height-8, 1, -1), (width-8, height-8, -1, -1)):
            drawing.line((x+16*sx, y, x, y, x, y+16*sy), fill=colors['border'])
        self.background_image = image
        self.background_photo = ImageTk.PhotoImage(image, master=self.canvas)
        tags = ('viewport_background', 'crt_scanline') if preferences['crt_scanlines'] else ('viewport_background',)
        self.canvas.itemconfigure(self.background_id, image=self.background_photo, tags=tags)
        self.background_key = key
        self.background_builds += 1
        self.last_glow = float('-inf')
        self.canvas.itemconfigure(self.trace_id, fill=colors['trace'])
        return True

    def draw(self, signal, width, height, colors, preferences, now=None):
        now = time.perf_counter() if now is None else now
        width, height = max(1, width), max(1, height)
        background_changed = self.prepare_background(width, height, colors, preferences)
        coordinates = np.empty((len(signal), 2))
        scale = .45 * min(width, height)
        coordinates[:, 0] = width/2 + signal[:, 0] * scale
        coordinates[:, 1] = height/2 - signal[:, 1] * scale
        if self.coordinates is None or not np.array_equal(coordinates, self.coordinates):
            self.canvas.coords(self.trace_id, *coordinates.ravel().tolist())
            self.coordinates = coordinates
        enabled = preferences['crt_glow']
        if enabled != self.glow_enabled:
            self.canvas.itemconfigure(self.glow_id, state='normal' if enabled else 'hidden')
            self.glow_enabled = enabled
            self.last_glow = float('-inf')
        glow_changed = self.glow_coordinates is None or not np.array_equal(coordinates, self.glow_coordinates)
        if enabled and (background_changed or now-self.last_glow >= self.GLOW_INTERVAL) and (glow_changed or background_changed):
            reduction = self.GLOW_REDUCTION
            small = (max(1, (width+reduction-1)//reduction), max(1, (height+reduction-1)//reduction))
            mask = Image.new('L', small, 0)
            points = [tuple(point) for point in np.rint(coordinates / reduction).astype(int).tolist()]
            ImageDraw.Draw(mask).line(points, fill=255, width=1)
            mask = mask.filter(ImageFilter.GaussianBlur(.75))
            mask = mask.resize((width, height), Image.Resampling.BILINEAR).point(lambda value: round(value*.3))
            color = Image.new('RGB', (width, height), colors['trace'])
            composite = Image.composite(color, self.background_image, mask)
            self.glow_photo = ImageTk.PhotoImage(composite, master=self.canvas)
            self.canvas.itemconfigure(self.glow_id, image=self.glow_photo)
            self.last_glow = now
            self.glow_coordinates = coordinates.copy()
            self.glow_builds += 1


class RetainedOverlay:
    """Canvas-like primitives that update existing handles/axes instead of rebuilding."""
    def __init__(self, canvas):
        self.canvas = canvas
        self.items = {}
        self.counts = {}
        self.used = set()
        self.created = False

    def begin(self):
        self.counts.clear()
        self.used.clear()
        self.created = False

    def create(self, kind, coordinates, options):
        index = self.counts.get(kind, 0)
        self.counts[kind] = index + 1
        key = (kind, index)
        self.used.add(key)
        options = dict(options)
        tags = options.get('tags', ())
        options['tags'] = ((tags,) if isinstance(tags, str) else tuple(tags)) + ('preview_overlay',)
        options['state'] = 'normal'
        cached = self.items.get(key)
        if cached is None:
            item = getattr(self.canvas, 'create_' + kind)(*coordinates, **options)
            self.items[key] = [item, options, True]
            self.created = True
        else:
            item, previous, visible = cached
            self.canvas.coords(item, *coordinates)
            if previous != options or not visible:
                self.canvas.itemconfigure(item, **options)
                cached[1] = options
            cached[2] = True
        return self.items[key][0]

    def create_line(self, *coordinates, **options):
        return self.create('line', coordinates, options)

    def create_rectangle(self, *coordinates, **options):
        return self.create('rectangle', coordinates, options)

    def create_oval(self, *coordinates, **options):
        return self.create('oval', coordinates, options)

    def create_text(self, *coordinates, **options):
        return self.create('text', coordinates, options)

    def finish(self):
        for key, cached in self.items.items():
            if key not in self.used and cached[2]:
                self.canvas.itemconfigure(cached[0], state='hidden')
                cached[2] = False
        if self.created:
            self.canvas.tag_raise('preview_overlay')
