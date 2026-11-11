"""Linked sliders and optional typed values; stopped rotation uses JSON-safe None."""
import math
import tkinter as tk
from tkinter import ttk

from phosphor.editor.control_values import (MIN_ROTATION, MAX_SLIDER_ROTATION, FINITE_END, STOP_START,
                            integer_degrees, parse_rotation, format_rotation, rotation_position, rotation_value)


class SliderInput(ttk.Frame):
    def __init__(self, parent, variable, rotation=False, minimum=0., maximum=.95, integer=False):
        super().__init__(parent)
        self.variable = variable
        self.default = variable.get()
        self.rotation = rotation
        self.integer = integer
        self.minimum, self.maximum = minimum, maximum
        self.syncing = False
        self.position = tk.DoubleVar()
        self.scale = ttk.Scale(self, from_=0 if rotation else minimum, to=1 if rotation else maximum,
                               variable=self.position, command=self.moved, length=135)
        self.scale.pack(side='left', fill='x', expand=True)
        self.scale.bind('<Double-Button-1>', self.reset_default)
        self.entry = ttk.Entry(self, textvariable=variable, width=11)
        self.entry.pack(side='right', padx=(5, 0))
        if integer:
            self.entry.bind('<Return>', self.commit)
            self.entry.bind('<FocusOut>', self.commit)
        self.trace = variable.trace_add('write', self.typed)
        self.typed()

    def typed(self, *args):
        if self.syncing:
            return
        try:
            value = parse_rotation(self.variable.get()) if self.rotation else float(self.variable.get())
            if not self.rotation and (not math.isfinite(value) or not self.minimum <= value <= self.maximum):
                return
            self.syncing = True
            if self.integer:
                value = integer_degrees(value)
            self.position.set(rotation_position(value) if self.rotation else value)
        except ValueError:
            pass  # Let users finish typing; the app reports invalid values.
        finally:
            self.syncing = False

    def moved(self, position):
        if self.syncing:
            return
        self.syncing = True
        try:
            value = rotation_value(float(position)) if self.rotation else float(position)
            if self.integer:
                value = integer_degrees(value)
                self.position.set(value)
            self.variable.set(str(value) if self.integer else format_rotation(value) if self.rotation else f'{value:.4f}')
        finally:
            self.syncing = False

    def commit(self, event=None):
        try:
            value = integer_degrees(self.variable.get())
            if self.minimum <= value <= self.maximum:
                self.variable.set(str(value))
        except ValueError:
            pass

    def reset_default(self, event=None):
        if event is not None and 'slider' not in self.scale.identify(event.x, event.y).lower():
            return
        self.variable.set(self.default)
        return 'break'
