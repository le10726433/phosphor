"""Triggered X(t)/Y(t) sweep for the system-audio monitor; display only, Qt-free.

Like a dual-trace oscilloscope: the beam sweeps left to right across one scan
window, then waits for a zero crossing on the trigger channel (rising or falling)
to start the next sweep, so repeating waveforms stand still. Without a trigger it
free-runs after an auto timeout. Both traces share one time axis; separation lifts
X above the centre line and lowers Y below it. Triggering on both channels uses two
sweeps, one per trace, which no longer share a time base.
"""
import numpy as np

WINDOW_RANGE_MS = (.1, 2000.)
DEFAULT_WINDOW_MS = 20.
SEPARATION_RANGE = (0., 1.)
TRIGGER_HYSTERESIS = .002  # The trigger channel must pass this far beyond zero to arm.
TRIGGER_X, TRIGGER_Y, TRIGGER_BOTH = 'X', 'Y', 'Both'
TRIGGERS = (TRIGGER_X, TRIGGER_Y, TRIGGER_BOTH)
EDGE_RISING, EDGE_FALLING = 'Rising', 'Falling'
EDGES = (EDGE_RISING, EDGE_FALLING)
AUTO_SECONDS = .1  # Free-run if no trigger arrives within this, or one window if longer.
SCOPE_SCALE = .45  # Matches GpuScope.screen_points: full scale is 0.45 of the short side.


class TimeSweep:
    def __init__(self, channel=0):
        self.window_ms = DEFAULT_WINDOW_MS
        self.rate = 48000
        self.channel = channel  # Trigger channel: 0 = X (left), 1 = Y (right).
        self.falling = False
        self.reset()

    @property
    def length(self):
        return max(2, round(self.window_ms/1000*self.rate))

    def reset(self):
        self.buffer = np.zeros((self.length, 2), dtype=np.float32)
        self.filled = 0  # Valid samples in the buffer, for the line-trace view.
        self.position = 0
        self.sweeping = False
        self.armed = False
        self.waited = 0
        self.previous = None  # Last drawn sample of the current sweep, to join chunks.

    def configure(self, window_ms, rate, falling=None):
        window_ms = min(WINDOW_RANGE_MS[1], max(WINDOW_RANGE_MS[0], float(window_ms)))
        if window_ms != self.window_ms or rate != self.rate:
            self.window_ms, self.rate = window_ms, int(rate)
            self.reset()
        if falling is not None and bool(falling) != self.falling:
            # A new edge applies from the next sweep; the current one finishes.
            self.falling, self.armed = bool(falling), False

    def _start(self):
        self.sweeping, self.position, self.armed, self.waited, self.previous = True, 0, False, 0, None

    def feed(self, samples):
        """Advance by new stereo samples; return the sweep pieces they draw.

        Each piece is (positions, values): sample positions within the sweep and the
        raw (n, 2) samples, joined to the previous chunk of the same sweep.
        """
        samples = np.asarray(samples, dtype=np.float32).reshape(-1, 2)
        pieces = []
        start, total, length = 0, len(samples), self.length
        auto = max(round(AUTO_SECONDS*self.rate), length)
        while start < total:
            if self.sweeping:
                count = min(length-self.position, total-start)
                chunk = samples[start:start+count]
                positions = np.arange(self.position, self.position+count)
                if self.previous is not None:
                    positions = np.r_[self.position-1, positions]
                    chunk = np.vstack((self.previous, chunk))
                pieces.append((positions, chunk))
                self.buffer[self.position:self.position+count] = samples[start:start+count]
                self.filled = max(self.filled, self.position+count)
                self.position += count
                self.previous = samples[start+count-1:start+count].copy()
                start += count
                if self.position >= length:
                    self.sweeping, self.armed, self.waited, self.previous = False, False, 0, None
                continue
            # Retrace done: wait for a zero crossing on the trigger channel, or free-run.
            # A falling edge is a rising edge of the negated signal.
            x = -samples[start:, self.channel] if self.falling else samples[start:, self.channel]
            index = 0
            if not self.armed:
                below = np.flatnonzero(x < -TRIGGER_HYSTERESIS)
                index = int(below[0]) if len(below) else len(x)
                self.armed = bool(len(below))
            trigger = None
            if self.armed:
                rising = np.flatnonzero(x[index:] >= 0)
                if len(rising):
                    trigger = index+int(rising[0])
            timeout = max(0, auto-self.waited)
            begin = min(trigger if trigger is not None else len(x), timeout)
            if begin >= len(x):
                self.waited += len(x)
                break
            start += begin
            self._start()
        return pieces

    def screen_points(self, positions, values, width, height, gain, separation, channels=(0, 1)):
        """Polylines in GpuScope screen pixels for one sweep piece, one per channel."""
        scale = SCOPE_SCALE*min(width, height)
        x = positions.astype(np.float64)/(self.length-1)*width
        traces = []
        for channel in channels:
            sign = 1. if channel == 0 else -1.
            y = height/2-(values[:, channel].astype(np.float64)*gain+sign*separation)*scale
            traces.append(np.column_stack((x, y)))
        return traces

    def display_polylines(self, width, height, gain, separation, channels=(0, 1)):
        """The whole buffer as X and Y polylines for the line-trace view.

        Long windows are reduced to the minimum and maximum of each pixel column,
        so drawing cost follows the view width rather than the window length.
        """
        count = self.filled
        if count < 2:
            return []
        values = self.buffer[:count]
        columns = max(2, int(width))
        if count > 2*columns:
            edges = np.linspace(0, count, columns+1).astype(int)
            low = np.minimum.reduceat(values, edges[:-1], axis=0)
            high = np.maximum.reduceat(values, edges[:-1], axis=0)
            centres = (edges[:-1]+edges[1:]-1)/2
            positions = np.repeat(centres, 2)
            values = np.empty((2*columns, 2), dtype=np.float32)
            values[0::2], values[1::2] = low, high
        else:
            positions = np.arange(count, dtype=np.float64)
        return self.screen_points(positions, values, width, height, gain, separation, channels)


class DualSweep:
    """The trigger choice as one shared sweep, or one sweep per trace for Both."""

    def __init__(self):
        self.trigger = TRIGGER_X
        self.sweeps = [(TimeSweep(0), (0, 1))]

    def configure(self, trigger, edge, window_ms, rate):
        if trigger != self.trigger:
            self.trigger = trigger
            self.sweeps = ([(TimeSweep(0), (0,)), (TimeSweep(1), (1,))] if trigger == TRIGGER_BOTH
                           else [(TimeSweep(1 if trigger == TRIGGER_Y else 0), (0, 1))])
        for sweep, _ in self.sweeps:
            sweep.configure(window_ms, rate, edge == EDGE_FALLING)

    def reset(self):
        for sweep, _ in self.sweeps:
            sweep.reset()

    @property
    def length(self):
        return self.sweeps[0][0].length

    def traces(self, samples, width, height, gain, separation):
        """Advance every sweep; return (points, drawn) per trace for the new samples."""
        traces = []
        for sweep, channels in self.sweeps:
            pieces = sweep.feed(samples)
            for index, channel in enumerate(channels):
                parts, drawn = [], []
                for positions, values in pieces:
                    points = sweep.screen_points(positions, values, width, height, gain, separation, (channel,))[0]
                    if parts:
                        drawn.append(False)
                    parts.append(points)
                    drawn.extend([True]*(len(points)-1))
                if parts:
                    traces.append((np.concatenate(parts), np.array(drawn, dtype=bool)))
        return traces

    def display_polylines(self, width, height, gain, separation):
        return [line for sweep, channels in self.sweeps
                for line in sweep.display_polylines(width, height, gain, separation, channels)]

