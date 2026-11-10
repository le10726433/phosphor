"""MIDI input, audio-worker commands, and sample-aligned performance capture."""
from dataclasses import asdict
import json
from pathlib import Path
import queue
import threading
import sys
import wave
import numpy as np
from midi_synth import MidiSynth

local = Path(__file__).parent / '.audio-deps'
if local.is_dir() and str(local) not in sys.path:
    sys.path.append(str(local))


def mido_module():
    try:
        import mido
        mido.set_backend('mido.backends.rtmidi')
        return mido
    except ImportError as error:
        raise RuntimeError('Install MIDI support: python -m pip install -r requirements.txt') from error


class Capture:
    def __init__(self, path, frame, settings, mode, synth):
        self.mido = mido_module()
        self.prefix = Path(path).with_suffix('')
        self.paths = [self.prefix.parent / (self.prefix.name + extension) for extension in ('.wav', '.mid', '.json')]
        if any(path.exists() for path in self.paths):
            raise ValueError('A recording with that name already exists. Choose a new name.')
        self.start = frame
        self.rate = settings.sample_rate
        self.bpm = settings.bpm
        self.events = []
        self.changes = []
        self.previous = None
        self.frames = 0
        self.initial_voices = [asdict(voice) for voice in synth.voices]
        self.raw = self.paths[0].open('xb')
        self.wav = wave.open(self.raw, 'wb')
        self.wav.setnchannels(2)
        self.wav.setsampwidth(2)
        self.wav.setframerate(self.rate)
        self.snapshot(frame, settings, mode)
        for channel, state in enumerate(synth.channels):
            for control, value in ((7, state['volume']), (1, state['modulation']), (64, 127 if state['sustain'] else 0)):
                self.event(frame, self.mido.Message('control_change', channel=channel, control=control, value=value))
            self.event(frame, self.mido.Message('pitchwheel', channel=channel, pitch=state['bend']))
        # MIDI notes already sounding at the start are seeded at time zero.
        for voice in synth.voices:
            self.event(frame, self.mido.Message('note_on', channel=voice.channel, note=voice.note, velocity=voice.velocity))
        for channel, state in enumerate(synth.channels):
            self.event(frame, self.mido.Message('control_change', channel=channel, control=66, value=127 if state['sostenuto'] else 0))
        for voice in synth.voices:
            if not voice.held:
                self.event(frame, self.mido.Message('note_off', channel=voice.channel, note=voice.note))

    def event(self, frame, message):
        if not message.is_realtime and not message.is_meta:
            self.events.append((max(0, frame - self.start), message.copy(time=0)))

    def snapshot(self, frame, settings, mode):
        value = dict(settings=asdict(settings), listening_mode=mode)
        if value != self.previous:
            self.changes.append(dict(frame=frame - self.start, **value))
            self.previous = value

    def write(self, frame, signal, settings, mode):
        self.snapshot(frame, settings, mode)
        if not np.isfinite(signal).all() or np.max(abs(signal)) > 1.000001:
            raise ValueError('Recording signal exceeded audio headroom.')
        self.wav.writeframesraw(np.rint(np.clip(signal, -1, 1) * 32767).astype('<i2').tobytes())
        self.frames += len(signal)

    def close(self):
        self.wav.close()
        self.raw.close()
        end = self.start + self.frames
        for channel in range(16):
            for control in (64, 66, 120):
                self.event(end, self.mido.Message('control_change', channel=channel, control=control, value=0))
        midi = self.mido.MidiFile(ticks_per_beat=960)
        track = self.mido.MidiTrack()
        midi.tracks.append(track)
        track.append(self.mido.MetaMessage('set_tempo', tempo=self.mido.bpm2tempo(self.bpm), time=0))
        previous = 0
        for frame, message in sorted(self.events, key=lambda event: event[0]):
            tick = round(frame / self.rate * self.bpm / 60 * 960)
            track.append(message.copy(time=tick - previous))
            previous = tick
        final_tick = round(self.frames / self.rate * self.bpm / 60 * 960)
        track.append(self.mido.MetaMessage('end_of_track', time=max(0, final_tick - previous)))
        with self.paths[1].open('xb') as target:
            midi.save(file=target)
        metadata = dict(sample_rate=self.rate, frames=self.frames, bpm=self.bpm,
                        timing='MIDI events timestamped at the audio block where applied; no quantization to beats.',
                        initial_voices=self.initial_voices, changes=self.changes,
                        events=[dict(frame=frame, message=message.dict()) for frame, message in self.events])
        with self.paths[2].open('x', encoding='utf-8') as target:
            json.dump(metadata, target, indent=2)


class LiveMidi:
    def __init__(self):
        self.synth = MidiSynth()
        self.commands = queue.SimpleQueue()
        self.port = None
        self.port_name = ''
        self.accepting = False
        self.capture = None
        self.notice = ''
        self.last_message = ''
        self.voice_count = 0
        self.port_lock = threading.RLock()
        self.probe_thread = None
        self.probe_results = queue.SimpleQueue()
        self.port_epoch = 0

    def names(self):
        with self.port_lock:
            return mido_module().get_input_names()

    def connect(self, name):
        with self.port_lock:
            self.disconnect()
            self.port = mido_module().open_input(name, callback=self.receive)
            self.port_name = name
            self.notice = 'MIDI connected. Start audio to play.'

    def disconnect(self):
        with self.port_lock:
            self.port_epoch += 1
            if self.port is not None:
                self.port.close()
            self.port = None
            self.port_name = ''
            self.panic()

    def check_port_async(self):
        if not self.port_name or (self.probe_thread and self.probe_thread.is_alive()):
            return
        name = self.port_name
        epoch = self.port_epoch

        def check():
            try:
                error = None if name in self.names() else 'MIDI device disappeared. Recording stopped; reconnect to resume.'
            except Exception as exception:
                error = f'MIDI device check failed: {exception}'
            self.probe_results.put((name, error, epoch))
        self.probe_thread = threading.Thread(target=check, daemon=True)
        self.probe_thread.start()

    def poll_port_check(self):
        try:
            return self.probe_results.get_nowait()
        except queue.Empty:
            return None

    def receive(self, message):
        self.last_message = str(message)
        if self.accepting:
            self.commands.put(('message', message.copy()))

    def panic(self):
        self.commands.put(('panic', None))

    def reset(self, rate):
        self.accepting = False
        while not self.commands.empty():
            self.commands.get()
        self.synth = MidiSynth(rate)
        self.voice_count = 0
        self.accepting = True

    def record(self, path):
        self.commands.put(('record', path))

    def stop_record(self):
        self.commands.put(('stop_record', None))

    def finish_record(self):
        if self.capture is not None:
            capture, self.capture = self.capture, None
            capture.close()
            self.notice = f'Saved {capture.prefix.name}.wav / .mid / .json'

    def send_panic(self, frame):
        self.synth.panic()
        if self.capture:
            mido = mido_module()
            for channel in range(16):
                for control in (64, 66, 120):
                    self.capture.event(frame, mido.Message('control_change', channel=channel, control=control, value=0))

    def process(self, frame, settings, mode):
        if self.synth.channel != settings.midi_channel:
            self.send_panic(frame)
        self.synth.set_options(settings.midi_preset, settings.midi_channel)
        while not self.commands.empty():
            kind, value = self.commands.get()
            if kind == 'message':
                if self.synth.handle(value) and self.capture:
                    self.capture.event(frame, value)
            elif kind == 'panic':
                self.send_panic(frame)
            elif kind == 'record':
                if self.capture is None:
                    try:
                        self.capture = Capture(value, frame, settings, mode, self.synth)
                        self.notice = 'Recording MIDI events and stereo WAV.'
                    except Exception as error:
                        self.notice = f'Recording failed: {error}'
            elif kind == 'stop_record':
                self.finish_record()

    def render(self, times, settings):
        result = self.synth.render(times, settings)
        # Stolen voices still fading out do not count against the limit.
        self.voice_count = sum(v.stolen_age is None for v in self.synth.voices)
        return result
