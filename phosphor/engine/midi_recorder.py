"""Device-free, immutable-project MIDI take accumulation (no editor mutations)."""
from dataclasses import replace
import math

from phosphor.audio.live_midi import quantise
from phosphor.engine.composition import Note, MAX_NOTES, MAX_BEATS, PITCH_RANGE, validate_project
from phosphor.engine.note_edit import cut_at_onsets


class MidiRecorder:
    def __init__(self, project, mode='overdub', snap=0., latency=0., chords=None):
        if mode not in ('replace', 'overdub'):
            raise ValueError('Choose Replace or Overdub.')
        if not all(math.isfinite(v) and v >= 0 for v in (snap, latency)):
            raise ValueError('Snap and latency must be finite and nonnegative.')
        self.project, self.mode, self.snap, self.latency = project, mode, snap, latency
        self.notes = []
        self.held = None
        self.chords = project.chords if chords is None else chords
        self.keys = {}
        self.last_beat = None
        self.last_cycle = 0
        self.finished = None

    def _position(self, seconds):
        if not math.isfinite(seconds):
            raise ValueError('Song time must be finite.')
        beat = max(0., float(self.project.beats(seconds - self.latency)))
        cycle = 0
        if self.project.loop and beat >= self.project.loop_end:
            cycle = 1 + int((beat - self.project.loop_end) // (self.project.loop_end - self.project.loop_start))
            beat = self.project.loop_start + (beat - self.project.loop_end) % (self.project.loop_end - self.project.loop_start)
        beat = min(MAX_BEATS, max(0., quantise(beat, self.snap)))
        if self.project.loop and (cycle > self.last_cycle or
                                 (self.last_beat is not None and beat < self.last_beat)):
            if self.chords:
                held = self.keys.copy()
                self._end_keys(self.project.loop_end)
                self.keys = {key: (self.project.loop_start, pitch, velocity)
                             for key, (_, pitch, velocity) in held.items()}
            else:
                held = self.held
                self._end(self.project.loop_end)
                if held is not None:
                    key, _, pitch, velocity = held
                    self.held = (key, self.project.loop_start, pitch, velocity)
        self.last_beat = beat
        self.last_cycle = cycle
        return beat

    def _end(self, beat):
        if self.held is None:
            return
        key, start, pitch, velocity = self.held
        self.held = None
        if beat > start and len(self.notes) < MAX_NOTES:
            self.notes.append(Note(start, min(MAX_BEATS, beat - start), pitch, velocity))

    def feed(self, message, song_seconds):
        if self.finished is not None:
            return
        beat = self._position(song_seconds)
        kind = message.type
        if kind not in ('note_on', 'note_off'):
            return
        key = (getattr(message, 'channel', 0), message.note)
        if self.chords:
            self._end_key(key, beat)
            if kind == 'note_on' and message.velocity > 0 and len(self.notes)+len(self.keys) < MAX_NOTES:
                self.keys[key] = (beat, min(PITCH_RANGE[1], max(PITCH_RANGE[0], int(message.note))),
                                  min(1., max(0., message.velocity / 127.)))
            return
        if kind == 'note_on' and message.velocity > 0:
            self._end(beat)
            if len(self.notes) < MAX_NOTES:
                self.held = (key, beat, min(PITCH_RANGE[1], max(PITCH_RANGE[0], int(message.note))),
                             min(1., max(0., message.velocity / 127.)))
        elif self.held is not None and self.held[0] == key:
            self._end(beat)

    def stop(self, song_seconds, project=None):
        if self.finished is not None:
            return self.finished
        beat = self._position(song_seconds)
        if self.chords: self._end_keys(beat)
        else: self._end(beat)
        project = project or self.project
        if not self.notes:
            self.finished = project
            return project
        first = min(n.start for n in self.notes)
        last = max(n.end for n in self.notes)
        existing = project.notes
        if self.mode == 'replace':
            existing = tuple(n for n in existing if n.end <= first or n.start >= last)
        # Reserve room for existing notes: the safety cap must not evict unrelated
        # notes at the end of the song during Overdub.
        accepted = self.notes[:max(0, MAX_NOTES - len(existing))]
        if not accepted:
            self.finished = project
            return project
        notes = tuple(sorted((*existing, *accepted), key=lambda n: (n.start, n.pitch))) if self.chords else cut_at_onsets(accepted, existing)
        self.finished = validate_project(replace(project, notes=notes))
        return self.finished

    def _end_key(self, key, beat):
        held = self.keys.pop(key, None)
        if held is not None:
            start, pitch, velocity = held
            if beat > start and len(self.notes) < MAX_NOTES:
                self.notes.append(Note(start, min(MAX_BEATS, beat-start), pitch, velocity))

    def _end_keys(self, beat):
        for key in list(self.keys): self._end_key(key, beat)
