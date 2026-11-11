"""Bounded, time-based scheduling for GPU phosphor afterimages."""
VISUAL_DEFAULTS = dict(trace_thickness=1., glow_size=3., glow_intensity=1., glow_decay=0., beam_brightness=1., beam_intensity=5.)
VISUAL_RANGES = dict(trace_thickness=(.5,6.),glow_size=(0.,24.),glow_intensity=(0.,3.),glow_decay=(0.,2.),beam_brightness=(0.,1.),beam_intensity=(.1,64.))


class GlowTrail:
    CAPACITY = 8

    def __init__(self): self.reset()

    def reset(self):
        self.stamps = [None]*self.CAPACITY
        self.next_slot = 0
        self.last_emission = float('-inf')

    def update(self, now, duration, enabled=True):
        if not enabled or duration <= 0:
            self.reset()
            return None,[0.]*self.CAPACITY
        slot = None
        if now-self.last_emission >= max(1/30,duration/self.CAPACITY):
            slot = self.next_slot
            self.stamps[slot] = now
            self.next_slot = (slot+1)%self.CAPACITY
            self.last_emission = now
        weights = [max(0.,1-(now-stamp)/duration)**2 if stamp is not None else 0. for stamp in self.stamps]
        return slot,weights
