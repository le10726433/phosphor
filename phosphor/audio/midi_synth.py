"""Eight-voice live synthesizer. All state belongs to the audio worker."""
from dataclasses import dataclass
from functools import lru_cache
import numpy as np

MIDI_SOURCE = 'Live MIDI'
FAMILIES = ('Sine', 'Sawtooth', 'Square', 'Triangle', 'Variable triangle', 'Pulse')
PRESETS = tuple(f'{family} ({length})' for family in FAMILIES for length in ('short', 'long'))
MAX_VOICES = 8
STEAL_FADE_SECONDS = .005  # A stolen voice fades instead of stopping mid-cycle, which clicks.


@lru_cache(maxsize=None)
def coefficients(family):
    phase = np.arange(4096) / 4096
    if family == 'Sine':
        wave = np.sin(2 * np.pi * phase)
    elif family == 'Sawtooth':
        wave = 2 * phase - 1
    elif family == 'Square':
        wave = np.where(phase < .5, 1., -1.)
    elif family == 'Pulse':
        wave = np.where(phase < .25, 1., -1.)
    else:
        width = .35 if family == 'Variable triangle' else .5
        wave = np.where(phase < width, 2 * phase / width - 1, 1 - 2 * (phase - width) / (1 - width))
    coeff = np.fft.rfft(wave)[1:33] / len(wave)
    coeff[abs(coeff) < 1e-6] = 0
    return coeff / max(2 * np.sum(abs(coeff)), 1e-12)


def harmonic_series(coeff, phase):
    """2 Re(sum of coeff[h-1] z^h) with z = e^(2 pi i phase), by Horner's rule.

    One complex exponential per voice replaces one per harmonic, which was most of the
    cost of a held chord at 96 kHz.
    """
    used = np.flatnonzero(coeff)
    if not len(used):
        return np.zeros(len(phase))
    z = np.exp(2j * np.pi * phase)
    total = np.full(len(phase), coeff[used[-1]], dtype=complex)
    for value in coeff[used[-1] - 1::-1] if used[-1] else ():
        total *= z
        total += value
    total *= z
    return 2 * total.real


@dataclass
class Voice:
    channel: int
    note: int
    velocity: int
    preset: str
    age: int = 0
    phase: float = 0.0
    held: bool = True
    sostenuto: bool = False
    release_age: int | None = None
    release_level: float = 0.0
    stolen_age: int | None = None


HARMONIC_CEILING = 24000.  # Hz; the band a 48 kHz device reproduces.


class MidiSynth:
    def __init__(self, sample_rate=48000):
        self.sample_rate = sample_rate
        self.voices = []
        self.channels = [dict(volume=127, bend=0, modulation=0, sustain=False, sostenuto=False) for _ in range(16)]
        self.preset = 'Sine (long)'
        self.channel = -1
        self.stolen = 0

    def envelope(self, voice, ages):
        t = np.asarray(ages) / self.sample_rate
        attack, hold, decay = .0105, .1, .3
        sustain = 0. if voice.preset.endswith('(short)') else 1.
        base = np.where(t < attack, t / attack,
                        np.where(t < attack + hold, 1.,
                                 np.maximum(sustain, 1 - (1 - sustain) * (t - attack - hold) / decay)))
        if voice.release_age is not None:
            base = voice.release_level * np.maximum(0, 1 - (np.asarray(ages) - voice.release_age) / (.3 * self.sample_rate))
        if voice.stolen_age is not None:
            base = base * np.maximum(0, 1 - (np.asarray(ages) - voice.stolen_age) / (STEAL_FADE_SECONDS * self.sample_rate))
        return np.clip(base, 0, 1)

    def release(self, voice):
        if voice.release_age is None:
            voice.release_level = float(self.envelope(voice, voice.age))
            voice.release_age = voice.age

    def release_unheld(self, channel):
        for voice in self.voices:
            if voice.channel == channel and not voice.held and not voice.sostenuto and not self.channels[channel]['sustain']:
                self.release(voice)

    def set_options(self, preset, channel):
        if preset not in PRESETS or channel not in range(-1, 16):
            raise ValueError('Invalid MIDI preset or channel.')
        if channel != self.channel:
            self.panic()
            self.channel = channel
        self.preset = preset

    def panic(self):
        self.voices.clear()
        for state in self.channels:
            state.update(sustain=False, sostenuto=False)

    def handle(self, message):
        channel = getattr(message, 'channel', None)
        if channel is None or (self.channel >= 0 and channel != self.channel):
            return False
        state = self.channels[channel]
        kind = message.type
        if kind == 'note_on' and message.velocity > 0:
            active = [v for v in self.voices if v.stolen_age is None]
            if len(active) >= MAX_VOICES:
                victim = next((v for v in active if v.release_age is not None), active[0])
                victim.stolen_age, victim.held = victim.age, False
                self.stolen += 1
            self.voices.append(Voice(channel, message.note, message.velocity, self.preset))
        elif kind == 'note_off' or kind == 'note_on':
            voice = next((v for v in self.voices if v.channel == channel and v.note == message.note and v.held and v.stolen_age is None), None)
            if voice:
                voice.held = False
                self.release_unheld(channel)
        elif kind == 'pitchwheel':
            state['bend'] = message.pitch
        elif kind == 'control_change':
            cc, value = message.control, message.value
            if cc == 7:
                state['volume'] = value
            elif cc == 1:
                state['modulation'] = value
            elif cc == 64:
                state['sustain'] = value >= 64
                self.release_unheld(channel)
            elif cc == 66:
                on = value >= 64
                for voice in self.voices:
                    if voice.channel == channel:
                        if on and not state['sostenuto']:
                            voice.sostenuto = voice.held
                        elif not on:
                            voice.sostenuto = False
                state['sostenuto'] = on
                self.release_unheld(channel)
            elif cc == 120:
                self.voices[:] = [v for v in self.voices if v.channel != channel]
            elif cc == 123:
                for voice in self.voices:
                    if voice.channel == channel:
                        voice.held = False
                self.release_unheld(channel)
            elif cc == 121:
                state.update(volume=127, bend=0, modulation=0, sustain=False, sostenuto=False)
                for voice in self.voices:
                    if voice.channel == channel:
                        voice.sostenuto = False
                self.release_unheld(channel)
        # Program/bank messages are recorded, but presets are explicitly selected in the GUI.
        return True

    def render(self, times, settings):
        from phosphor.engine.signal_engine import shape_audio
        drawing = np.zeros((len(times), 2))
        original = np.zeros_like(drawing)
        for voice in self.voices:
            state = self.channels[voice.channel]
            frequency = 440 * 2 ** ((voice.note - 69 + 2 * state['bend'] / 8192) / 12)
            # The sketch switches on each 25 ms control tick: 20 full cycles/s.
            octave = np.where((np.floor(times / .025).astype(np.int64) % 2) == 0, .5, 1.) if state['modulation'] else np.ones(len(times))
            steps = frequency * octave / self.sample_rate
            phase = voice.phase + np.cumsum(steps) - steps
            voice.phase = float((voice.phase + np.sum(steps)) % 1)
            ages = voice.age + np.arange(len(times))
            amplitude = (self.envelope(voice, ages) * voice.velocity / 127
                         * state['volume'] / 127 * settings.midi_gain / MAX_VOICES)
            coeff = coefficients(voice.preset.split(' (')[0])
            # Harmonics stop at 24 kHz, the 48 kHz Nyquist limit, at any device rate: higher
            # ones are inaudible and doubled synthesis cost at 96 kHz.
            ceiling = min(self.sample_rate / 2, HARMONIC_CEILING)
            audible = np.count_nonzero(np.arange(1, len(coeff) + 1) * frequency < ceiling)
            waveform = harmonic_series(coeff[:audible], phase)
            original += (waveform * amplitude)[:, None]
            drawing += shape_audio(times, phase, np.ones(len(times)), settings,frequency*octave) * amplitude[:, None]
            voice.age += len(times)
        self.voices[:] = [v for v in self.voices if not (
            (v.release_age is not None and v.age - v.release_age >= .3 * self.sample_rate)
            or (v.stolen_age is not None and v.age - v.stolen_age >= STEAL_FADE_SECONDS * self.sample_rate)
            or (v.preset.endswith('(short)') and v.age >= .4105 * self.sample_rate and not v.held))]
        output = settings.drawing_gain * ((1 - settings.music_gain) * drawing + settings.music_gain * original)
        return drawing, original, output
