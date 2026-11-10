"""Run with python app.py. Preview plots the actual generated stereo samples."""
if __name__ == '__main__':
    import sys
    if '--cpu' not in sys.argv:
        from qt_app import main
        raise SystemExit(main())
    sys.argv.remove('--cpu')

import time
from dataclasses import replace
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import numpy as np
from signal_engine import Settings, Shape, render, export_wav, select_signal, source_duration, projected_solid, validate
from score_source import SOURCES, SCALE_NAME, MIDI_FILE_SOURCE
from solids import SOLIDS
from audio_preview import AudioPreview
from midi_synth import MIDI_SOURCE, PRESETS
from scene_path import TRAVEL_MODES
from slider_input import SliderInput, parse_rotation, format_rotation, integer_degrees
from desktop_ui import DesktopUI
from crt_theme import prepare_fonts
from viewport import Viewport, RetainedOverlay


class App(DesktopUI):
    def __init__(self, root, preferences_path=None):
        self.root = root
        self.font_family = prepare_fonts(root)
        root.title('phosphor — Audio to Shape')
        root.minsize(1000, 680)
        root.geometry('1180x820')
        self.audio = AudioPreview()
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.start = time.perf_counter()
        self.controls = {}
        self.sliders = {}
        self.position = np.zeros(2)
        self.shape_size = 1.0
        self.drag = None
        self.handle_points = None
        self.instances = [Shape()]
        self.selected = 0
        self.all_handles = []
        self.last_midi_check = 0.0
        self._settings_dirty = self._document_dirty = True
        self._settings_cache = None
        self._next_frame = time.perf_counter()
        self._last_status = 0.0
        header = self.header = ttk.Frame(root, padding=(16, 12))
        header.pack(side='top', fill='x')
        self.brand_logo = ttk.Label(header)
        self.brand_logo.pack(side='left')
        self.brand_text = ttk.Label(header,text='SOUND LAB',style='SoundLab.TLabel')
        self.brand_text.pack(side='left',padx=(12,0))
        footer = ttk.Frame(root, padding=(16, 8))
        footer.pack(side='bottom', fill='x')
        self.status = ttk.Label(footer, text='', wraplength=1120, style='Muted.TLabel')
        self.status.pack(fill='x')
        body = ttk.Frame(root, padding=(12, 0, 12, 4))
        body.pack(fill='both', expand=True)
        sidebar = ttk.Frame(body, width=350)
        sidebar.pack(side='left', fill='y')
        transport = ttk.Frame(sidebar, padding=(8, 10))
        transport.pack(side='bottom', fill='x')
        self.control_tabs = ttk.Notebook(sidebar)
        self.control_tabs.pack(fill='both', expand=True, padx=(0, 10))
        self.tab_frames = {}

        def tab_panel(name):
            tab = ttk.Frame(self.control_tabs)
            self.tab_frames[name] = tab
            self.control_tabs.add(tab, text=name)
            scroll = tk.Canvas(tab, width=340, height=540, highlightthickness=0)
            scrollbar = ttk.Scrollbar(tab, orient='vertical', command=scroll.yview)
            scrollbar.pack(side='right', fill='y')
            scroll.pack(fill='both', expand=True)
            scroll.configure(yscrollcommand=scrollbar.set)
            inner = ttk.Frame(scroll, padding=12)
            window = scroll.create_window((0, 0), window=inner, anchor='nw')
            scroll.bind('<Configure>', lambda event: scroll.itemconfigure(window, width=event.width))
            inner.bind('<Configure>', lambda event: scroll.configure(scrollregion=scroll.bbox('all')))
            scroll.bind('<MouseWheel>', lambda event: scroll.yview_scroll(-int(event.delta/120), 'units'))
            return inner

        self.input_panel = tab_panel('INPUT')
        self.scene_panel = tab_panel('SCENE')
        self.mix_panel = tab_panel('MIX')
        panel = self.input_panel
        ttk.Label(panel, text='Audio source').pack(anchor='w')
        self.source = tk.StringVar(value=SCALE_NAME)
        source_menu = ttk.Combobox(panel, textvariable=self.source, values=(*(s for s in SOURCES if s != MIDI_FILE_SOURCE), MIDI_SOURCE), state='readonly', width=25)  # the Tk fallback has no file picker
        source_menu.pack(anchor='w')
        source_menu.bind('<<ComboboxSelected>>', self.source_changed)
        midi_panel = ttk.LabelFrame(panel, text='Live MIDI', padding=6)
        midi_panel.pack(fill='x', pady=8)
        self.midi_device = tk.StringVar()
        self.midi_device_menu = ttk.Combobox(midi_panel, textvariable=self.midi_device, state='readonly', width=23)
        self.midi_device_menu.pack(fill='x')
        midi_buttons = ttk.Frame(midi_panel)
        midi_buttons.pack(pady=3)
        ttk.Button(midi_buttons, text='Refresh', width=7, command=self.refresh_midi_ports).pack(side='left')
        self.connect_button = ttk.Button(midi_buttons, text='Connect', width=8, command=self.connect_midi)
        self.connect_button.pack(side='left')
        ttk.Button(midi_buttons, text='Disconnect', width=10, command=self.disconnect_midi).pack(side='left')
        self.midi_channel = tk.StringVar(value='All')
        ttk.Label(midi_panel, text='MIDI channel').pack(anchor='w')
        ttk.Combobox(midi_panel, textvariable=self.midi_channel, values=('All', *map(str, range(1, 17))), state='readonly', width=23).pack(fill='x')
        self.midi_preset = tk.StringVar(value='Sine (long)')
        ttk.Label(midi_panel, text='Synth preset (new notes)').pack(anchor='w')
        ttk.Combobox(midi_panel, textvariable=self.midi_preset, values=PRESETS, state='readonly', width=23).pack(fill='x')
        ttk.Label(midi_panel, text='MIDI input gain (×)').pack(anchor='w', pady=(5, 0))
        self.midi_gain = tk.StringVar(value='1.0')
        ttk.Spinbox(midi_panel, from_=0, to=16, increment=.5, textvariable=self.midi_gain, width=23).pack(fill='x')
        ttk.Label(midi_panel, text='Try 4×–8× for larger shapes.\nHigh gain can clip chords.', wraplength=215).pack(anchor='w')
        ttk.Label(midi_panel, text='8 voices · bend ±2 semitones\nCC1: octave alternation').pack(anchor='w', pady=3)
        ttk.Button(midi_panel, text='Panic / All notes off', command=self.audio.midi.panic).pack(fill='x')
        ttk.Button(midi_panel, text='Record performance…', command=self.record_midi).pack(fill='x', pady=(4, 0))
        ttk.Button(midi_panel, text='Stop recording', command=self.audio.midi.stop_record).pack(fill='x')
        self.midi_status = ttk.Label(midi_panel, text='No MIDI device connected.', wraplength=285, style='Muted.TLabel')
        self.midi_status.pack(anchor='w', pady=4)
        panel = self.scene_panel
        ttk.Label(panel, text='Shapes — select one to edit').pack(anchor='w', pady=(8, 0))
        self.shape_list = tk.Listbox(panel, height=4, width=27, exportselection=False)
        self.shape_list.pack(anchor='w')
        self.shape_list.bind('<<ListboxSelect>>', self.select_from_list)
        buttons = ttk.Frame(panel)
        buttons.pack(anchor='w', pady=4)
        ttk.Button(buttons, text='Add', width=7, command=self.add_shape).pack(side='left')
        ttk.Button(buttons, text='Duplicate', width=9, command=self.duplicate_shape).pack(side='left')
        ttk.Button(buttons, text='Remove', width=8, command=self.remove_shape).pack(side='left')
        ttk.Label(panel, text='Selected shape (3D or 2D)').pack(anchor='w')
        self.solid = tk.StringVar(value='Cube')
        solid_menu = ttk.Combobox(panel, textvariable=self.solid, values=SOLIDS, state='readonly', width=25)
        solid_menu.pack(anchor='w')
        solid_menu.bind('<<ComboboxSelected>>', lambda event: self.refresh_shape_list())
        self.orientation_controls = {}
        for axis in ('x', 'y', 'z'):
            ttk.Label(panel, text=f'Selected: {axis.upper()} view rotation (°)').pack(anchor='w', pady=(5, 0))
            variable = tk.StringVar(value='0')
            self.orientation_controls[axis] = variable
            self.sliders[f'rotation_{axis}'] = SliderInput(panel, variable, minimum=-180, maximum=180, integer=True)
            self.sliders[f'rotation_{axis}'].pack(fill='x')
        ttk.Button(panel, text='Reset selected view rotation', command=self.reset_orientation).pack(pady=4)
        panel = self.mix_panel
        ttk.Label(panel, text='Travel between shapes').pack(anchor='w', pady=(8, 0))
        self.travel_mode = tk.StringVar(value='No changes')
        ttk.Combobox(panel, textvariable=self.travel_mode, values=TRAVEL_MODES, state='readonly', width=25).pack(anchor='w')
        ttk.Label(panel, text='Faster travel: speed multiplier (×)').pack(anchor='w')
        self.travel_speed = tk.StringVar(value='8')
        ttk.Spinbox(panel, from_=1, to=64, increment=1, textvariable=self.travel_speed, width=25).pack(anchor='w')
        ttk.Label(panel, text='Stereo travel strategies; no true blanking.', wraplength=215).pack(anchor='w')
        for label, name, value in [('Rotation updates (Hz)', 'drawing_hz', 60),
                                   ('Selected: seconds per revolution', 'rotation_seconds', 5),
                                   ('Tempo (BPM, quarter notes)', 'bpm', 120),
                                   ('Output level (0–0.95)', 'drawing_gain', .55),
                                   ('Original blend (0–1)', 'music_gain', 0)]:
            panel = self.scene_panel if name == 'rotation_seconds' else self.input_panel if name == 'bpm' else self.mix_panel
            ttk.Label(panel, text=label).pack(anchor='w', pady=(12, 0))
            variable = tk.StringVar(value=str(value))
            self.controls[name] = variable
            if name in ('rotation_seconds', 'drawing_gain'):
                self.sliders[name] = SliderInput(panel, variable, rotation=name == 'rotation_seconds')
                self.sliders[name].pack(fill='x')
                if name == 'rotation_seconds':
                    ttk.Label(panel, text='0.01 s → slower → ∞ No rotation', wraplength=215).pack(anchor='w')
            else:
                ttk.Entry(panel, textvariable=variable, width=25).pack(anchor='w')
        panel = self.mix_panel
        ttk.Label(panel, text='Audio phase drives the wireframe.\nBlend 0 = shaped · 1 = original\nLeft = X · Right = Y', style='Muted.TLabel').pack(pady=8)
        self.mode = tk.StringVar(value='mixed')
        for text, value in [('Processed output', 'mixed'), ('Fully shaped audio', 'drawing'),
                            ('Original audio (compare)', 'music')]:
            ttk.Radiobutton(panel, text=text, variable=self.mode, value=value).pack(anchor='w')
        self.play_button = ttk.Button(transport, text='▶ PLAY AUDIO', style='Transport.TButton', command=self.toggle_audio)
        self.play_button.pack(fill='x')
        ttk.Label(panel, text='View selection also selects what you hear.\nPlayback starts from the beginning.', wraplength=210).pack(pady=6)
        ttk.Button(panel, text='Export processed WAV…', command=self.export).pack(pady=(8, 3))
        ttk.Button(panel, text='Export source WAV…', command=lambda: self.export(source_only=True)).pack(pady=(0, 8))
        record_row = ttk.Frame(transport)
        record_row.pack(fill='x', pady=(6, 0))
        ttk.Button(record_row, text='● RECORD', command=self.record_midi).pack(side='left', fill='x', expand=True)
        ttk.Button(record_row, text='END TAKE', command=self.audio.midi.stop_record).pack(side='left', fill='x', expand=True, padx=(6, 0))
        ttk.Label(self.scene_panel, text='Drag center to move; corner to resize.', wraplength=285, style='Muted.TLabel').pack(pady=8)
        ttk.Button(self.scene_panel, text='Reset selected position/size', command=self.reset_placement).pack(pady=4)
        self.canvas = tk.Canvas(body, width=740, height=640, bg='#020a06', highlightthickness=1)
        self.canvas.pack(side='right', fill='both', expand=True)
        self.viewport = Viewport(self.canvas)
        self.overlays = RetainedOverlay(self.canvas)
        self.canvas.bind('<ButtonPress-1>', self.begin_drag)
        self.canvas.bind('<B1-Motion>', self.drag_placement)
        self.canvas.bind('<ButtonRelease-1>', self.end_drag)
        self.control_tabs.select(self.tab_frames['SCENE'])
        self.refresh_shape_list()
        self.refresh_midi_ports()
        for variable in (*self.controls.values(), *self.orientation_controls.values(), self.source, self.mode,
                         self.midi_device, self.midi_preset, self.midi_channel, self.midi_gain, self.travel_mode, self.travel_speed):
            variable.trace_add('write', self.invalidate_settings)
        self.initialize_desktop(preferences_path)
        self.tick()

    def invalidate_settings(self, *args):
        self._settings_dirty = self._document_dirty = True

    def source_changed(self, event=None):
        try:
            self.audio.stop()
        except RuntimeError as error:
            messagebox.showerror('Audio preview', str(error))

    def refresh_midi_ports(self):
        try:
            names = self.audio.midi.names()
            self.midi_device_menu.config(values=names)
            if self.midi_device.get() not in names:
                self.midi_device.set(names[0] if names else '')
            if not names and not self.audio.midi.port_name:
                self.audio.midi.notice = 'No MIDI inputs found. Connect the controller and Refresh.'
                self.midi_status.config(text=self.audio.midi.notice)
        except Exception as error:
            self.audio.midi.notice = str(error)
            self.midi_status.config(text=str(error))

    def connect_midi(self):
        try:
            if not self.midi_device.get():
                raise ValueError('Select a MIDI input first.')
            self.source_changed()
            self.audio.midi.connect(self.midi_device.get())
            self.source.set(MIDI_SOURCE)
        except Exception as error:
            messagebox.showerror('MIDI input', str(error))

    def disconnect_midi(self):
        try:
            if self.audio.live:
                self.audio.stop()
            self.audio.midi.disconnect()
            self.audio.midi.notice = 'MIDI disconnected.'
        except Exception as error:
            messagebox.showerror('MIDI input', str(error))

    def record_midi(self):
        if not self.audio.running or not self.audio.live:
            messagebox.showinfo('Recording', 'Connect a MIDI device and start Live MIDI audio first.')
            return
        if self.audio.midi.capture:
            return
        path = filedialog.asksaveasfilename(title='Record stereo WAV + MIDI + scene metadata',
                                          defaultextension='.wav', filetypes=[('Performance WAV', '*.wav')])
        if path:
            self.audio.midi.record(path)

    def store_selected(self):
        self.instances[self.selected] = Shape(self.solid.get(), float(self.position[0]),
                                             float(self.position[1]), self.shape_size,
                                             parse_rotation(self.controls['rotation_seconds'].get()),
                                             *[integer_degrees(self.orientation_controls[axis].get()) for axis in ('x', 'y', 'z')])

    def load_orientation(self, shape):
        for axis in ('x', 'y', 'z'):
            self.orientation_controls[axis].set(str(integer_degrees(getattr(shape, 'rotation_' + axis))))

    def reset_orientation(self):
        for variable in self.orientation_controls.values():
            variable.set('0')

    def refresh_shape_list(self):
        self.store_selected()
        self.invalidate_settings()
        self.shape_list.delete(0, 'end')
        for index, shape in enumerate(self.instances):
            self.shape_list.insert('end', f'{index + 1}. {shape.solid}')
        self.shape_list.selection_set(self.selected)
        self.shape_list.see(self.selected)

    def select_shape(self, index):
        self.store_selected()
        self.selected = index
        shape = self.instances[index]
        self.position = np.array([shape.position_x, shape.position_y])
        self.shape_size = shape.shape_size
        self.solid.set(shape.solid)
        self.controls['rotation_seconds'].set(format_rotation(shape.rotation_seconds))
        self.load_orientation(shape)
        self.drag = None
        self.shape_list.selection_clear(0, 'end')
        self.shape_list.selection_set(index)
        self.shape_list.see(index)

    def select_from_list(self, event):
        selection = self.shape_list.curselection()
        if selection:
            try:
                self.select_shape(selection[0])
            except ValueError:
                self.status.config(text='Enter a valid rotation period before changing selection.')

    def add_instance(self, shape):
        self.store_selected()
        self.instances.append(shape)
        self.select_shape(len(self.instances) - 1)
        self.refresh_shape_list()

    def add_shape(self):
        try:
            self.add_instance(Shape(shape_size=.6, position_x=.5))
        except ValueError as error:
            self.status.config(text=str(error))

    def duplicate_shape(self):
        try:
            self.store_selected()
            shape = self.instances[self.selected]
            self.add_instance(replace(shape, position_x=shape.position_x + .15))
        except ValueError as error:
            self.status.config(text=str(error))

    def remove_shape(self):
        if len(self.instances) <= 1:
            self.status.config(text='Keep at least one shape in the scene.')
            return
        # Load the remaining selection without writing the removed shape back.
        self.instances.pop(self.selected)
        self.selected = min(self.selected, len(self.instances) - 1)
        shape = self.instances[self.selected]
        self.position = np.array([shape.position_x, shape.position_y])
        self.shape_size = shape.shape_size
        self.solid.set(shape.solid)
        self.controls['rotation_seconds'].set(format_rotation(shape.rotation_seconds))
        self.load_orientation(shape)
        self.drag = None
        self.refresh_shape_list()

    def settings(self):
        if not self._settings_dirty and self._settings_cache is not None:
            return self._settings_cache
        self.store_selected()
        result = Settings(solid=self.solid.get(), source=self.source.get(), position_x=float(self.position[0]),
                        position_y=float(self.position[1]), shape_size=self.shape_size,
                        shapes=tuple(self.instances),
                        midi_preset=self.midi_preset.get(),
                        midi_channel=-1 if self.midi_channel.get() == 'All' else int(self.midi_channel.get()) - 1,
                        midi_gain=float(self.midi_gain.get()),
                        travel_mode=self.travel_mode.get(), travel_speed=float(self.travel_speed.get()),
                        **{f'rotation_{axis}': integer_degrees(value.get()) for axis, value in self.orientation_controls.items()},
                        **{key: parse_rotation(value.get()) if key == 'rotation_seconds' else float(value.get())
                           for key, value in self.controls.items()})
        self._settings_cache = result
        self._settings_dirty = False
        return result

    def reset_placement(self):
        self.position[:] = 0
        self.shape_size = 1.0
        self.drag = None
        self.invalidate_settings()

    def draw_handles(self, settings, width, height, scale):
        self.handle_points = None
        self.all_handles = []
        if self.mode.get() == 'music':
            return
        for index, shape in enumerate(settings.shapes):
            center = np.array([width / 2, height / 2]) + settings.drawing_gain * scale * np.array([shape.position_x, -shape.position_y])
            radius = settings.drawing_gain * shape.shape_size * scale
            corner = center + [radius, -radius]
            # Keep handles reachable when the geometry extends outside the canvas.
            center_handle = np.clip(center, [8, 8], [max(8, width-8), max(8, height-8)])
            corner_handle = np.clip(corner, [24, 24], [max(24, width-24), max(24, height-24)])
            self.all_handles.append((index, center_handle, corner_handle))
            x, y = center
            active = index == self.selected
            if active:
                self.handle_points = (center_handle, corner_handle)
                self.overlays.create_rectangle(x-radius, y-radius, x+radius, y+radius, outline=self.colors['border'], dash=(4, 4))
                cx, cy = corner_handle
                self.overlays.create_rectangle(cx-6, cy-6, cx+6, cy+6, fill=self.colors['accent'], outline=self.colors['fg'])
            x, y = center_handle
            self.overlays.create_oval(x-6, y-6, x+6, y+6, fill=self.colors['accent'] if active else self.colors['muted'], outline=self.colors['fg'])
            self.overlays.create_text(x+11, y+11, text=str(index+1), fill=self.colors['fg'], anchor='nw', font=(self.font_family, 9))

    def begin_drag(self, event):
        if self.handle_points is None:
            return
        point = np.array([event.x, event.y])
        center, corner = self.handle_points
        kind = 'size' if np.max(abs(point-corner)) <= 12 else None
        if kind is None:
            candidates = sorted(self.all_handles, key=lambda item: item[0] != self.selected)
            for index, center, _ in candidates:
                if np.linalg.norm(point-center) <= 12:
                    try:
                        self.select_shape(index)
                    except ValueError:
                        return
                    kind = 'move'
                    break
        if kind:
            try:
                settings = self.settings()
                scale = .45 * min(self.canvas.winfo_width(), self.canvas.winfo_height())
                if settings.drawing_gain <= 0:
                    return
                self.drag = (kind, point, self.position.copy(), self.shape_size,
                             scale * settings.drawing_gain)
                self.canvas.config(cursor='fleur' if kind == 'move' else 'sizing')
            except ValueError:
                return

    def draw_axes(self, settings, seconds, width, height):
        """Screen-only orientation key: never supplied to the signal renderer."""
        shape = settings.shapes[self.selected]
        pose_time = np.floor(seconds * settings.drawing_hz) / settings.drawing_hz
        axes = projected_solid(pose_time, shape, vertices=np.eye(3), include_depth=True)
        origin = np.array([65., max(65., height - 65.)])
        self.overlays.create_rectangle(8, origin[1]-55, 140, origin[1]+53,
                                     fill=self.colors['background'], outline=self.colors['grid'], tags='axes_overlay')
        for index in np.argsort(axes[:, 2]):
            axis = axes[index]
            vector = axis[:2] * [1, -1] * 39
            end = origin + vector
            color = ('#ff7979', '#8ee78e', '#80b8ff')[index]
            label = 'XYZ'[index]
            if np.linalg.norm(vector) < 4:
                x, y = origin
                self.overlays.create_oval(x-5, y-5, x+5, y+5, outline=color, tags='axes_overlay')
                if axis[2] >= 0:
                    self.overlays.create_oval(x-1, y-1, x+1, y+1, fill=color, outline=color, tags='axes_overlay')
                else:
                    self.overlays.create_line(x-3, y-3, x+3, y+3, fill=color, tags='axes_overlay')
                    self.overlays.create_line(x-3, y+3, x+3, y-3, fill=color, tags='axes_overlay')
                end = origin + [12, -12]
            else:
                back = origin - vector * .4
                self.overlays.create_line(*back, *origin, fill=color, dash=(2, 3), tags='axes_overlay')
                self.overlays.create_line(*origin, *end, fill=color, arrow='last', width=2, tags='axes_overlay')
                end = end + vector / np.linalg.norm(vector) * 10
            self.overlays.create_text(*end, text=label, fill=color, tags='axes_overlay')
        self.overlays.create_text(74, origin[1]+40, text=f'XYZ / SHAPE {self.selected+1}',
                                fill=self.colors['muted'], font=(self.font_family, 8), tags='axes_overlay')

    def drag_placement(self, event):
        if self.drag is None:
            return
        kind, start, position, size, pixels = self.drag
        delta = np.array([event.x, event.y]) - start
        try:
            if kind == 'move':
                self.position = position + delta * [1, -1] / pixels
            else:
                self.shape_size = float(max(.05, size + (delta[0] - delta[1]) / (2 * pixels)))
            self.invalidate_settings()
        except ValueError:
            return

    def end_drag(self, event):
        self.drag = None
        self.canvas.config(cursor='')

    def tick(self):
        now = time.perf_counter()
        minimized = self.root.state() == 'iconic'
        try:
            settings = self.settings()
            if self.audio.running and self.audio.live != (settings.source == MIDI_SOURCE):
                self.audio.stop()
            if self.audio.running:
                previous_settings, previous_mode = self.audio.parameters
                if previous_settings is not settings or previous_mode != self.mode.get():
                    validate(settings)
                    self.audio.parameters = (settings, self.mode.get())
            if not minimized:
                count = round(.04 * settings.sample_rate)
                elapsed = now - self.start
                if self.audio.running:
                    # Display actual output; never synthesize another preview copy.
                    signal = self.audio.samples(count)
                    if signal is None:
                        signal = np.zeros((count, 2))
                else:
                    end = int(elapsed * settings.sample_rate)
                    times = np.arange(max(0, end-count), max(0, end-count)+count) / settings.sample_rate
                    drawing, music, mixed = render(times, settings)
                    signal = select_signal(drawing, music, mixed, settings, self.mode.get())
                width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
                scale = .45 * min(width, height)
                self.viewport.draw(signal, width, height, self.colors, self.preferences, now)
                self.overlays.begin()
                self.draw_handles(settings, width, height, scale)
                axes_time = self.audio.frames / settings.sample_rate if self.audio.running else elapsed
                self.draw_axes(settings, axes_time, width, height)
                self.overlays.finish()
            self.update_status(settings, now)
            midi = self.audio.midi
            result = midi.poll_port_check()
            if result and result[0] == midi.port_name and result[2] == midi.port_epoch and result[1]:
                self.disconnect_midi()
                midi.notice = result[1]
                self._last_status = 0
            if midi.port_name and now-self.last_midi_check > 2:
                self.last_midi_check = now
                midi.check_port_async()
        except (ValueError, ZeroDivisionError, OverflowError) as error:
            self.set_text(self.status, str(error))
        self.update_document_title()
        # Fixed cadence; skip missed deadlines instead of queuing catch-up frames.
        current = time.perf_counter()
        if minimized:
            delay = 200
            self._next_frame = current
        else:
            self._next_frame += 1 / 30
            if self._next_frame <= current:
                self._next_frame = current + 1 / 30
            delay = max(1, round((self._next_frame-current)*1000))
        self.root.after(delay, self.tick)

    def update_status(self, settings, now):
        if now-self._last_status < .2:
            return
        self._last_status = now
        playback = f'Playing {self.audio.frames / settings.sample_rate:.1f} s' if self.audio.running else 'Audio stopped'
        if self.audio.underruns:
            playback += f' | Underruns: {self.audio.underruns}'
        if self.audio.clipped_samples:
            playback += f' | Clipped samples: {self.audio.clipped_samples}'
        if self.audio.error:
            playback += f' | {self.audio.error}'
        self.set_text(self.play_button, '■ STOP AUDIO' if self.audio.running else '▶ PLAY AUDIO')
        duration = 'Live input' if settings.source == MIDI_SOURCE else f'Source loop: {source_duration(settings):g} s'
        self.set_text(self.status, f'48 kHz  |  STEREO XY  |  {duration}  |  {playback}')
        midi = self.audio.midi
        recording = 'RECORDING\n' if midi.capture else ''
        self.set_text(self.midi_status, f'{recording}{midi.port_name or "Not connected"}\nVoices: {midi.voice_count}/8\n{midi.notice}\n{midi.last_message}')

    def toggle_audio(self):
        try:
            if self.audio.running:
                self.audio.stop()
            else:
                if self.source.get() == MIDI_SOURCE and self.audio.midi.port is None:
                    raise ValueError('Connect a MIDI input before starting Live MIDI audio.')
                self.audio.start(self.settings(), self.mode.get())
        except Exception as error:
            messagebox.showerror('Audio preview', str(error))

    def close(self):
        if self.launch_pending or not self.confirm_save('exiting'):
            return
        try:
            self.shutdown_audio()
        except Exception as error:
            messagebox.showerror('Audio preview', str(error))
            return
        self.root.destroy()

    def export(self, source_only=False):
        if self.source.get() == MIDI_SOURCE:
            messagebox.showinfo('Live MIDI export', 'Use Record performance to capture live stereo audio and MIDI events.')
            return
        try:
            settings = self.settings()
            path = filedialog.asksaveasfilename(defaultextension='.wav', filetypes=[('Stereo WAV', '*.wav')])
            if path:
                export_wav(path, settings, duration=source_duration(settings), source_only=source_only)
                messagebox.showinfo('Export complete', path)
        except (ValueError, OSError) as error:
            messagebox.showerror('Export failed', str(error))


if __name__ == '__main__':
    import argparse
    from pathlib import Path
    from app_files import APP_DIR
    parser = argparse.ArgumentParser()
    parser.add_argument('--ready-file')
    arguments = parser.parse_args()
    root = tk.Tk()
    App(root)
    if arguments.ready_file:
        ready = Path(arguments.ready_file).resolve()
        if ready.parent != (APP_DIR / '.user').resolve():
            raise ValueError('Startup readiness file must be in the app user directory.')
        root.after_idle(lambda: ready.write_text('ready', encoding='utf-8'))
    root.mainloop()
