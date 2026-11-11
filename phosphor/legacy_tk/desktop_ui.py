"""Menus, scene lifecycle, persistent appearance, and application information."""
from pathlib import Path
import subprocess
import sys
import time
import uuid
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import numpy as np
from phosphor.storage.app_files import (APP_DIR, PREFERENCES_PATH, DEFAULT_PREFERENCES, read_preferences,
                       validated_preferences, atomic_json, scene_document, read_scene)
from phosphor.storage.build_info import VERSION, PROJECT_DATE, BUILD_DATE, TRANSPARENCY, INSTRUCTIONS

from phosphor.display.crt_theme import PALETTES
from phosphor.display.branding import logo_path
from phosphor.display.glow_trail import VISUAL_DEFAULTS


class DesktopUI:
    def initialize_desktop(self, preferences_path=None):
        self.preferences_path = Path(preferences_path) if preferences_path else PREFERENCES_PATH
        self.scene_path = None
        self.preferences_dialog = self.about_dialog = None
        self.launch_pending = None
        warning = ''
        try:
            self.preferences = read_preferences(self.preferences_path)
        except (OSError, ValueError) as error:
            self.preferences = dict(DEFAULT_PREFERENCES)
            warning = f'Appearance preferences could not be loaded; using defaults.\n{error}'
        self.menu_bar = tk.Menu(self.root)
        self.file_menu = tk.Menu(self.menu_bar, tearoff=False)
        self.file_menu.add_command(label='New instance…', accelerator='Ctrl+N', command=self.new_instance)
        self.file_menu.add_command(label='Open scene…', accelerator='Ctrl+O', command=self.open_scene)
        self.file_menu.add_command(label='Save scene', accelerator='Ctrl+S', command=self.save_scene)
        self.file_menu.add_command(label='Save scene as…', accelerator='Ctrl+Shift+S', command=lambda: self.save_scene(save_as=True))
        self.file_menu.add_separator()
        self.file_menu.add_command(label='Export source WAV…', command=lambda: self.export(source_only=True))
        self.file_menu.add_command(label='Export processed WAV…', command=self.export)
        self.file_menu.add_separator()
        self.file_menu.add_command(label='Record MIDI performance…', command=self.record_midi)
        self.file_menu.add_command(label='Stop recording', command=self.audio.midi.stop_record)
        self.file_menu.add_separator()
        self.file_menu.add_command(label='Exit', command=self.close)
        self.menu_bar.add_cascade(label='File', menu=self.file_menu)
        self.preferences_menu = tk.Menu(self.menu_bar, tearoff=False)
        self.preferences_menu.add_command(label='Appearance…', command=self.show_preferences)
        self.menu_bar.add_cascade(label='Preferences', menu=self.preferences_menu)
        self.about_menu = tk.Menu(self.menu_bar, tearoff=False)
        self.about_menu.add_command(label='About phosphor', command=self.show_about)
        self.about_menu.add_command(label='How to use', command=lambda: self.show_about(instructions=True))
        self.menu_bar.add_cascade(label='About', menu=self.about_menu)
        # In-window menus share the phosphor skin instead of the native white bar.
        for label, menu in (('File', self.file_menu), ('Preferences', self.preferences_menu), ('About', self.about_menu)):
            ttk.Menubutton(self.header, text=label, menu=menu).pack(side='left', padx=(10, 0))
        for sequence, action in (('<Control-n>', self.new_instance), ('<Control-o>', self.open_scene),
                                 ('<Control-s>', self.save_scene), ('<Control-Shift-S>', lambda: self.save_scene(save_as=True))):
            self.root.bind(sequence, lambda event, action=action: self.keyboard_action(action))
        self.apply_preferences(self.preferences)
        self.saved_document = self.scene_snapshot()
        self.update_document_title()
        if warning:
            self.root.after_idle(lambda: messagebox.showwarning('Preferences', warning, parent=self.root))

    @staticmethod
    def keyboard_action(action):
        action()
        return 'break'

    def scene_snapshot(self):
        return scene_document(self.settings(), self.mode.get(), self.midi_device.get())

    def is_dirty(self):
        try:
            return self.scene_snapshot() != self.saved_document
        except (ValueError, TypeError):
            return True

    def update_document_title(self, force=False):
        if not force and not self._document_dirty:
            return
        name = self.scene_path.name if self.scene_path else 'Untitled scene'
        title = f'{"* " if self.is_dirty() else ""}{name} — phosphor'
        if title != getattr(self, '_last_title', None):
            self.root.title(title)
            self._last_title = title
        self._document_dirty = False

    @staticmethod
    def set_text(widget, value):
        if widget.cget('text') != value:
            widget.configure(text=value)

    def save_scene(self, save_as=False):
        try:
            document = self.scene_snapshot()
            path = self.scene_path
            if path is None or save_as:
                value = filedialog.asksaveasfilename(parent=self.root, title='Save scene',
                            defaultextension='.scene.json', filetypes=[('Scene JSON', '*.scene.json'), ('JSON', '*.json')],
                            initialfile=path.name if path else 'Untitled.scene.json')
                if not value:
                    return False
                path = Path(value)
            atomic_json(path, document)
            self.scene_path, self.saved_document = path, document
            self.update_document_title(force=True)
            return True
        except (OSError, ValueError, TypeError) as error:
            messagebox.showerror('Save scene', str(error), parent=self.root)
            return False

    def confirm_save(self, action, force=False):
        if not force and not self.is_dirty():
            return True
        choice = messagebox.askyesnocancel('Save current scene?',
                         f'Save the current scene before {action}?\n\nYes: save and continue. No: continue without saving. Cancel: stay here.',
                         parent=self.root)
        if choice is None:
            return False
        return self.save_scene() if choice else True

    def open_scene(self):
        value = filedialog.askopenfilename(parent=self.root, title='Open scene',
                       filetypes=[('Scene JSON', '*.scene.json'), ('JSON', '*.json')])
        if not value:
            return
        try:
            settings, mode, device = read_scene(value)  # Validate before touching this session.
            if not self.confirm_save('opening another scene'):
                return
            # Saving may have updated the file we are about to open.
            settings, mode, device = read_scene(value)
            self.audio.stop()
            self.audio.midi.disconnect()
            self.instances = list(settings.shapes)
            self.selected = 0
            shape = self.instances[0]
            self.position = np.array([shape.position_x, shape.position_y])
            self.shape_size = shape.shape_size
            self.solid.set(shape.solid)
            self.source.set(settings.source)
            self.mode.set(mode)
            self.midi_device.set(device)
            self.midi_preset.set(settings.midi_preset)
            self.midi_channel.set('All' if settings.midi_channel < 0 else str(settings.midi_channel + 1))
            self.midi_gain.set(str(settings.midi_gain))
            self.travel_mode.set(settings.travel_mode)
            self.travel_speed.set(str(settings.travel_speed))
            for key, variable in self.controls.items():
                variable.set(str(getattr(settings, key)))
            self.load_orientation(shape)
            self.drag = None
            self.start = time.perf_counter()
            self.refresh_shape_list()
            self.scene_path = Path(value)
            self.saved_document = self.scene_snapshot()
            self.update_document_title(force=True)
        except (OSError, ValueError, TypeError) as error:
            messagebox.showerror('Open scene', str(error), parent=self.root)

    def shutdown_audio(self):
        self.audio.stop()  # Finalizes any active performance recording.
        self.audio.midi.disconnect()

    def new_instance(self):
        if self.launch_pending or not self.confirm_save('opening a new instance and closing this one', force=True):
            return
        try:
            self.shutdown_audio()
            ready = APP_DIR / '.user' / f'launch-{uuid.uuid4().hex}.ready'
            ready.parent.mkdir(parents=True, exist_ok=True)
            process = subprocess.Popen([sys.executable, str(APP_DIR / 'app.py'), '--cpu', '--ready-file', str(ready)],
                                       cwd=str(APP_DIR), creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.launch_pending = (process, ready, time.monotonic() + 15)
            self.poll_new_instance()
        except (OSError, RuntimeError) as error:
            messagebox.showerror('New instance', f'The current window will stay open.\n{error}', parent=self.root)

    def poll_new_instance(self):
        if not self.launch_pending:
            return
        process, ready, deadline = self.launch_pending
        if ready.exists():
            ready.unlink()
            self.launch_pending = None
            self.root.destroy()
        elif process.poll() is not None or time.monotonic() > deadline:
            if process.poll() is None:
                process.terminate()
            self.launch_pending = None
            messagebox.showerror('New instance', 'The new window did not become ready. Your current scene is still open.', parent=self.root)
        else:
            self.root.after(100, self.poll_new_instance)

    def apply_preferences(self, preferences):
        self.preferences = validated_preferences(preferences)
        self.colors = self.palette = dict(PALETTES[self.preferences['palette']])
        p = self.colors
        style = ttk.Style(self.root)
        if style.theme_use() != 'clam':
            style.theme_use('clam')
        style.configure('.', background=p['bg'], foreground=p['fg'], fieldbackground=p['field'], font=(self.font_family, 10))
        style.configure('Brand.TLabel', foreground=p['accent'], font=(self.font_family, 15, 'bold'))
        style.configure('Muted.TLabel', foreground=p['muted'], font=(self.font_family, 9))
        style.configure('TEntry', insertcolor=p['accent'], foreground=p['accent'], bordercolor=p['border'], lightcolor=p['border'], darkcolor=p['border'])
        style.configure('TCombobox', arrowsize=14, arrowcolor=p['accent'], bordercolor=p['border'], lightcolor=p['border'], darkcolor=p['border'])
        style.map('TCombobox', fieldbackground=[('readonly', p['field'])], foreground=[('readonly', p['fg'])])
        style.configure('TSpinbox', arrowcolor=p['accent'], foreground=p['accent'], bordercolor=p['border'], lightcolor=p['border'], darkcolor=p['border'])
        style.configure('TButton', foreground=p['accent'], bordercolor=p['border'], lightcolor=p['border'], darkcolor=p['border'], relief='raised', borderwidth=1, padding=(8, 6))
        style.configure('TMenubutton', background=p['bg'], foreground=p['accent'], arrowcolor=p['accent'], padding=(8, 6), relief='flat')
        style.map('TMenubutton', background=[('active', p['active'])])
        style.configure('Transport.TButton', font=(self.font_family, 11, 'bold'), padding=(10, 9))
        style.configure('TLabelframe', bordercolor=p['border'], lightcolor=p['border'], darkcolor=p['border'], borderwidth=1, relief='solid')
        style.configure('TLabelframe.Label', foreground=p['accent'], font=(self.font_family, 10, 'bold'))
        style.map('TButton', background=[('active', p['active']), ('pressed', p['field'])])
        style.map('TCheckbutton', background=[('active', p['bg'])])
        style.map('TRadiobutton', background=[('active', p['bg'])], indicatorcolor=[('selected', p['accent']), ('!selected', p['field'])])
        style.configure('TNotebook', bordercolor=p['border'], lightcolor=p['border'], darkcolor=p['border'], tabmargins=(0, 0, 0, 0))
        style.configure('TNotebook.Tab', foreground=p['muted'], padding=(16, 9), font=(self.font_family, 10, 'bold'), bordercolor=p['border'], lightcolor=p['border'], darkcolor=p['border'])
        style.map('TNotebook.Tab', background=[('selected', p['active']), ('!selected', p['bg'])], foreground=[('selected', p['accent'])])
        style.configure('TScale', troughcolor=p['field'], background=p['accent'], bordercolor=p['border'], lightcolor=p['accent'], darkcolor=p['accent'])
        style.configure('Vertical.TScrollbar', troughcolor=p['bg'], background=p['field'], arrowcolor=p['fg'])
        self.root.option_add('*TCombobox*Listbox.background', p['field'])
        self.root.option_add('*TCombobox*Listbox.foreground', p['fg'])

        def classic(widget):
            if isinstance(widget, (tk.Tk, tk.Toplevel, tk.Canvas)):
                widget.configure(background=p['bg'])
            elif isinstance(widget, tk.Listbox):
                widget.configure(background=p['field'], foreground=p['fg'], selectbackground=p['active'], selectforeground=p['accent'], font=(self.font_family, 10), relief='solid', borderwidth=1, highlightbackground=p['border'])
            elif isinstance(widget, tk.Text):
                widget.configure(background=p['field'], foreground=p['fg'], insertbackground=p['fg'], font=(self.font_family, 10))
            for child in widget.winfo_children():
                classic(child)
        classic(self.root)
        self.canvas.config(background=p['background'], highlightbackground=p['border'], highlightcolor=p['accent'])
        self._logo_source = tk.PhotoImage(file=str(logo_path(self.preferences['palette'])))
        self._header_logo = self._logo_source.subsample(9,9)
        self.brand_logo.config(image=self._header_logo)
        ttk.Style(self.root).configure('SoundLab.TLabel',font=('Courier',27),foreground=p['trace'],background=p['bg'])
        self.root.iconphoto(True,self._logo_source)
        for menu in (self.menu_bar, self.file_menu, self.preferences_menu, self.about_menu):
            menu.config(background=p['bg'], foreground=p['fg'], activebackground=p['active'], activeforeground=p['accent'], font=(self.font_family, 10))

    def show_preferences(self):
        if self.preferences_dialog and self.preferences_dialog.winfo_exists():
            self.preferences_dialog.lift()
            return
        dialog = self.preferences_dialog = tk.Toplevel(self.root)
        dialog.title('Preferences — Appearance')
        dialog.transient(self.root)
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=18)
        body.pack(fill='both', expand=True)
        baseline = dict(self.preferences)
        values = {key: tk.BooleanVar(value=value) if key in ('crt_scanlines', 'crt_glow') else tk.StringVar(value=str(value))
                  for key, value in self.preferences.items()}

        def current():
            result = {key: variable.get() for key, variable in values.items()}
            result['grid_divisions'] = int(result['grid_divisions'])
            for key in VISUAL_DEFAULTS: result[key] = float(result[key])
            return validated_preferences(result)

        def preview(*args):
            try:
                self.apply_preferences(current())
            except (ValueError, tk.TclError):
                pass

        ttk.Label(body, text='CRT PALETTE', style='Brand.TLabel').grid(row=0, column=0, columnspan=2, sticky='w', pady=(0, 14))
        ttk.Label(body, text='Color preset').grid(row=1, column=0, sticky='w', padx=(0, 16), pady=8)
        ttk.Combobox(body, textvariable=values['palette'], values=tuple(PALETTES), state='readonly', width=23).grid(row=1, column=1)
        ttk.Label(body, text='Grid divisions per axis').grid(row=2, column=0, sticky='w', pady=8)
        ttk.Spinbox(body, from_=2, to=64, textvariable=values['grid_divisions'], width=10, command=preview).grid(row=2, column=1, sticky='w')
        ttk.Checkbutton(body, text='CRT scanlines', variable=values['crt_scanlines'], command=preview).grid(row=3, column=0, columnspan=2, sticky='w', pady=8)
        ttk.Checkbutton(body, text='Trace glow (lightweight)', variable=values['crt_glow'], command=preview).grid(row=4, column=0, columnspan=2, sticky='w', pady=8)
        ttk.Label(body, text='Palettes style the entire interface.\nAppearance never changes the audio output.', style='Muted.TLabel').grid(row=5, column=0, columnspan=2, sticky='w', pady=8)
        for key in ('palette', 'grid_divisions'):
            values[key].trace_add('write', preview)

        def apply():
            nonlocal baseline
            try:
                result = current()
                atomic_json(self.preferences_path, result)
                baseline = result
                self.apply_preferences(result)
                return True
            except (OSError, ValueError) as error:
                messagebox.showerror('Preferences', str(error), parent=dialog)
                return False

        def cancel():
            self.apply_preferences(baseline)
            dialog.destroy()
            self.preferences_dialog = None

        def defaults():
            for key, value in DEFAULT_PREFERENCES.items():
                values[key].set(value)
            preview()

        buttons = ttk.Frame(body)
        buttons.grid(row=6, column=0, columnspan=2, pady=(15, 0), sticky='e')
        ttk.Button(buttons, text='Defaults', command=defaults).pack(side='left', padx=4)
        ttk.Button(buttons, text='Cancel', command=cancel).pack(side='left', padx=4)
        ttk.Button(buttons, text='Apply', command=apply).pack(side='left', padx=4)
        ttk.Button(buttons, text='OK', command=lambda: cancel() if apply() else None).pack(side='left', padx=4)
        dialog.protocol('WM_DELETE_WINDOW', cancel)
        dialog.bind('<Escape>', lambda event: cancel())
        self.apply_preferences(self.preferences)
        dialog.grab_set()

    def show_about(self, instructions=False):
        if self.about_dialog and self.about_dialog.winfo_exists():
            self.about_dialog.lift()
            self.about_notebook.select(1 if instructions else 0)
            return
        dialog = self.about_dialog = tk.Toplevel(self.root)
        dialog.title('About phosphor')
        dialog.transient(self.root)
        dialog.geometry('720x580')
        notebook = self.about_notebook = ttk.Notebook(dialog)
        notebook.pack(fill='both', expand=True, padx=12, pady=12)
        for title, text in (('About', f'phosphor {VERSION}\n\nMade by Mike Le\n\nProject date: {PROJECT_DATE}\nBuild date: {BUILD_DATE}\n\nAI transparency\n\n{TRANSPARENCY}'),
                            ('How to use', INSTRUCTIONS)):
            frame = ttk.Frame(notebook)
            notebook.add(frame, text=title)
            area = tk.Text(frame, wrap='word', padx=18, pady=18, font=(self.font_family, 10), borderwidth=0)
            scroll = ttk.Scrollbar(frame, orient='vertical', command=area.yview)
            scroll.pack(side='right', fill='y')
            area.pack(fill='both', expand=True)
            area.config(yscrollcommand=scroll.set)
            area.insert('1.0', text)
            area.config(state='disabled')
        notebook.select(1 if instructions else 0)
        ttk.Button(dialog, text='Close', command=dialog.destroy).pack(pady=(0, 12))
        self.apply_preferences(self.preferences)
