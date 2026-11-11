"""Native Core Audio tap subprocess transport. Captured audio never leaves memory."""
import os
import platform
import select
import struct
import subprocess
import numpy as np

MAC_SOURCE = 'All system audio (Core Audio)'
HEADER = struct.Struct('<4sIIII')
PERMISSION_HELP = ('Allow phosphor Audio Capture (or the launching app) in '
    'System Settings > Privacy & Security > Screen & System Audio Recording, '
    'then stop and restart visualization. Microphone permission is not needed.')


class PacketDecoder:
    def __init__(self): self.buffer = bytearray()
    def feed(self, data):
        self.buffer.extend(data)
        if len(self.buffer) > 1024*1024: raise ValueError('Capture helper exceeded its bounded input buffer.')
        result = []
        while len(self.buffer) >= HEADER.size:
            magic,frames,rate,channels,drops = HEADER.unpack_from(self.buffer)
            if magic != b'OSCA' or frames > 4096 or not 8000 <= rate <= 384000 or channels != 2:
                raise ValueError('Invalid native audio capture packet.')
            size = HEADER.size+frames*channels*4
            if len(self.buffer) < size: break
            samples = np.frombuffer(bytes(self.buffer[HEADER.size:size]),dtype='<f4').reshape(frames,channels)
            if not np.isfinite(samples).all(): raise ValueError('Capture returned invalid audio samples.')
            result.append((rate,samples,drops))
            del self.buffer[:size]
        return result


def capture_native(capture, name):
    if name not in (None,MAC_SOURCE): raise ValueError('Select All system audio (Core Audio).')
    if tuple(int(p) for p in platform.mac_ver()[0].split('.')[:2]) < (14,2):
        raise RuntimeError('System audio capture requires macOS 14.2 or later.')
    from phosphor.audio.build_macos_capture import build_helper
    helper = build_helper()
    if capture.stop_event.is_set(): return
    process = subprocess.Popen([str(helper)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0)
    decoder = PacketDecoder(); errors = bytearray()
    try:
        streams = [process.stdout,process.stderr]
        while not capture.stop_event.is_set():
            ready,_,_ = select.select(streams,[],[],.05) if streams else ([],[],[])
            for stream in ready:
                chunk = os.read(stream.fileno(),65536)
                if not chunk:
                    streams.remove(stream)
                    continue
                if stream is process.stderr:
                    errors.extend(chunk); del errors[:-8192]
                else:
                    for rate,samples,drops in decoder.feed(chunk):
                        capture.sample_rate, capture.channels = rate,2
                        capture.device_name = MAC_SOURCE
                        capture.ready = True
                        capture.status_events = drops
                        if len(samples): capture.accept_samples(samples)
            if not streams and process.poll() is None: capture.stop_event.wait(.05)
            if process.poll() is not None and not streams:
                detail=errors.decode(errors='replace').strip()
                raise RuntimeError((detail or 'Native system-audio capture stopped unexpectedly.')+' '+PERMISSION_HELP)
    finally:
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=1)
        for stream in (process.stdout,process.stderr): stream.close()
