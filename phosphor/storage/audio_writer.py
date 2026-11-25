"""Streaming stereo audio export, with a dependency-free integer WAV fallback."""
import wave
import numpy as np

try:
    import soundfile
except (ImportError, OSError):
    soundfile = None

# The playback output-rate choices (signal_preferences.OUTPUT_RATES), capped at 192 kHz.
RATES = (44100, 48000, 88200, 96000, 176400, 192000)
DEPTHS = (16, 24, 32)  # 32 means IEEE float, WAV only.


def validate_options(format, depth, rate):
    if format not in ('WAV', 'FLAC') or type(depth) is not int or depth not in DEPTHS:
        raise ValueError('Select WAV or FLAC and a supported bit depth.')
    if type(rate) is not int or rate not in RATES:
        raise ValueError('Select 44.1, 48, 88.2, 96, 176.4 or 192 kHz.')
    if format == 'FLAC' and depth == 32:
        raise ValueError('FLAC supports 16 or 24 bits.')


def integer_samples(block, depth):
    return np.rint(np.clip(block, -1, 1) * (2 ** (depth - 1) - 1)).astype('<i4' if depth == 24 else '<i2')


class AudioWriter:
    def __init__(self, path, rate=48000, format='WAV', depth=16):
        validate_options(format, depth, rate)
        self.depth = depth
        self.native = soundfile is not None
        if self.native:
            self.output = soundfile.SoundFile(str(path), 'w', samplerate=rate, channels=2,
                format=format, subtype='FLOAT' if depth == 32 else f'PCM_{depth}')
        else:
            if format != 'WAV' or depth == 32:
                raise ValueError('Install soundfile for FLAC and 32-bit float WAV export.')
            self.output = wave.open(str(path), 'wb')
            self.output.setnchannels(2); self.output.setsampwidth(depth // 8); self.output.setframerate(rate)

    def write(self, block):
        block = np.asarray(block)
        if block.ndim != 2 or block.shape[1] != 2 or not np.isfinite(block).all():
            raise ValueError('Audio blocks must contain finite stereo samples.')
        if self.depth == 32:
            self.output.write(block.astype('float32'))
            return
        samples = integer_samples(block, self.depth)
        if self.native:
            self.output.write(samples if self.depth == 16 else samples << 8)
        elif self.depth == 16:
            self.output.writeframes(samples.tobytes())
        else:
            self.output.writeframes(samples.view('uint8').reshape(-1, 4)[:, :3].tobytes())

    def close(self): self.output.close()
    def __enter__(self): return self
    def __exit__(self, *args): self.close()


def open(path, rate=48000, format='WAV', depth=16):
    return AudioWriter(path, rate, format, depth)
