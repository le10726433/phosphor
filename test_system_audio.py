"""Input-only loopback contract, raw channel preservation and resource cleanup."""
from types import SimpleNamespace
import time
import unittest
import numpy as np
from system_audio import SystemAudio


class Manager:
    def __init__(self, channels=2, loopback=True):
        self.info = dict(name='Test speakers [Loopback]',index=7,defaultSampleRate=44100,
                         maxInputChannels=channels,isLoopbackDevice=loopback)
        self.opened = None
        self.closed = False

    def __enter__(self): return self
    def __exit__(self,*args): self.closed = True
    def get_default_wasapi_loopback(self): return self.info
    def get_loopback_device_info_generator(self): return iter([self.info])
    def open(self, **options): self.opened = options; return Stream()


class Stream:
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def is_active(self): return True


class SystemAudioTests(unittest.TestCase):
    def start_capture(self, channels=2, loopback=True, name=None):
        manager = Manager(channels,loopback)
        module = SimpleNamespace(PyAudio=lambda:manager,paFloat32=1,paContinue=0,paAbort=2)
        capture = SystemAudio(module,clock=lambda:1.)
        self.addCleanup(capture.stop)
        capture.start(name)
        deadline = time.monotonic()+1
        while not capture.ready and capture.running and time.monotonic()<deadline: time.sleep(.001)
        return capture,manager

    def feed(self, capture, manager, block, status=0):
        data = np.asarray(block,dtype=np.float32)
        return manager.opened['stream_callback'](data.tobytes(),len(data),{},status)

    def test_native_rate_input_only_and_unmodified_stereo(self):
        capture,manager = self.start_capture()
        options = manager.opened
        self.assertTrue(options['input']); self.assertFalse(options['output'])
        self.assertNotIn('output_device_index',options)
        self.assertEqual(options['rate'],44100)
        values = np.array([[1.2,-1.3],[.125,-.25],[0,0]],dtype=np.float32)
        self.assertEqual(self.feed(capture,manager,values),(None,0))
        np.testing.assert_array_equal(capture.samples(3),values)
        capture.stop(); self.assertTrue(manager.closed)
        np.testing.assert_array_equal(capture.samples(3),np.zeros((3,2)))

    def test_mono_surround_and_stale_silence(self):
        for channels in (1,6):
            capture,manager = self.start_capture(channels)
            values = np.arange(4*channels,dtype=np.float32).reshape(4,channels)/10
            self.feed(capture,manager,values,4)
            expected = np.repeat(values,2,axis=1) if channels == 1 else values[:,:2]
            np.testing.assert_array_equal(capture.samples(4),expected)
            self.assertEqual(capture.status_events,1)
            capture.clock = lambda:2.
            np.testing.assert_array_equal(capture.samples(4),np.zeros((4,2)))
            capture.stop()

    def test_microphones_and_missing_devices_never_open(self):
        for loopback,name in ((False,None),(True,'Unplugged speakers')):
            capture,manager = self.start_capture(loopback=loopback,name=name)
            capture.stop()
            self.assertIsNone(manager.opened)
            self.assertTrue(capture.error)

    def test_buffer_is_bounded_and_invalid_audio_aborts(self):
        capture,manager = self.start_capture()
        for _ in range(40): self.feed(capture,manager,[[.2,.3]])
        self.assertEqual(len(capture.history),16)
        self.assertEqual(self.feed(capture,manager,[[np.nan,0]]),(None,2))
        self.assertIn('invalid',capture.error)


if __name__ == '__main__': unittest.main()
