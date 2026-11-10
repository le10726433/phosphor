"""Measure audio publication cadence with a paced fake output; no sound/device use.

Run on an otherwise idle machine. Timing is observational, not a CI threshold.
This does not measure Metal frame rate or CoreAudio buffering.
"""
import json
import threading
import time
from audio_preview import AudioPreview
from signal_engine import Settings
from score_source import SCALE_NAME


def measure(block_frames):
    player = AudioPreview()
    player.BLOCK_FRAMES = block_frames
    player.parameters = (Settings(source=SCALE_NAME), 'mixed')

    class PacedStream:
        def __init__(self): self.deadline = time.perf_counter()
        def write(self, block):
            self.deadline += len(block) / 48000
            time.sleep(max(0, self.deadline - time.perf_counter()))
            return False
        def close(self): pass

    player.thread = threading.Thread(target=player._run, args=(PacedStream(),))
    player.thread.start()
    ticks = []
    try:
        started = time.perf_counter()
        for tick in range(150):
            time.sleep(max(0, started + (tick + 1) / 60 - time.perf_counter()))
            ticks.append(player.frames)
            player.samples(1920)
    finally:
        player.stop()
    if player.error: raise RuntimeError(player.error)
    return dict(block_frames=block_frames, viewport_ticks=len(ticks),
                distinct_audio_windows=len(set(ticks)),
                repeated_windows=sum(a == b for a, b in zip(ticks, ticks[1:])))


if __name__ == '__main__':
    print(json.dumps(dict(note='Simulated output timing; not Metal/CoreAudio FPS.',
                         before=measure(1024), after=measure(512)), indent=2))
