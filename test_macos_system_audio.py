"""Native capture wire format, platform routing and input-only lifecycle."""
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
import numpy as np
from macos_system_audio import PacketDecoder,HEADER,MAC_SOURCE,capture_native
from system_audio import SystemAudio


def packet(samples,rate=48000,drops=0):
    samples=np.asarray(samples,dtype='<f4').reshape(-1,2)
    return HEADER.pack(b'OSCA',len(samples),rate,2,drops)+samples.tobytes()


class MacAudioTests(unittest.TestCase):
    def test_split_packets_preserve_samples_and_native_rate(self):
        original=np.array([[1.2,-1.3],[.125,-.25]],dtype=np.float32)
        wire=packet([],44100)+packet(original,44100,3)
        decoder=PacketDecoder();result=[]
        for byte in wire:result.extend(decoder.feed(bytes([byte])))
        self.assertEqual(len(result),2)
        self.assertEqual(result[0][1].shape,(0,2))
        self.assertEqual((result[1][0],result[1][2]),(44100,3))
        np.testing.assert_array_equal(result[1][1],original)
        self.assertFalse(decoder.buffer)

    def test_invalid_headers_samples_and_unbounded_input_rejected(self):
        for wire in (HEADER.pack(b'NOPE',0,48000,2,0),HEADER.pack(b'OSCA',5000,48000,2,0),
                     HEADER.pack(b'OSCA',1,48000,1,0),packet([[np.nan,0]]),b'x'*(1024*1024+1)):
            with self.subTest(wire_length=len(wire)),self.assertRaises(ValueError):PacketDecoder().feed(wire)

    def test_macos_routes_to_native_capture_without_windows_import(self):
        capture=SystemAudio()
        with patch('system_audio.sys.platform','darwin'),patch('macos_system_audio.capture_native') as native:
            self.assertEqual(SystemAudio.devices(),[MAC_SOURCE])
            capture._run(None);native.assert_called_once_with(capture,None)
        self.assertFalse(capture.ready)

    def test_cancelled_start_never_opens_process(self):
        capture=SystemAudio();capture.stop_event.set()
        with (patch('macos_system_audio.platform.mac_ver',return_value=('26.6.2','','')),
             patch('build_macos_capture.build_helper',return_value=Path('/test/helper')),
             patch('macos_system_audio.subprocess.Popen') as process):
            capture_native(capture,None);process.assert_not_called()

    @unittest.skipIf(sys.platform == "win32", "Core Audio transport uses POSIX pipe select; Windows uses WASAPI")
    def test_transport_termination_and_resource_cleanup(self):
        capture=SystemAudio()
        class Process:
            def __init__(self,*args,**kwargs):
                self.exit=None; self.terminated=False
                stdout,writer=os.pipe();stderr,errwriter=os.pipe()
                self.stdout=os.fdopen(stdout,'rb',buffering=0)
                self.stderr=os.fdopen(stderr,'rb',buffering=0)
                os.write(writer,packet([[.2,-.3]],44100,2));os.close(writer)
                os.close(errwriter)
            def poll(self):return self.exit
            def terminate(self):self.terminated=True;self.exit=0
            def wait(self,timeout=None):return self.exit
        processes=[]
        def spawn(*args,**kwargs):
            p=Process();processes.append(p);return p
        accept=capture.accept_samples
        def receive(samples):
            accept(samples);capture.stop_event.set()
        with (patch('macos_system_audio.platform.mac_ver',return_value=('26.6.2','','')),
              patch('build_macos_capture.build_helper',return_value=Path('/test/helper')),
              patch('macos_system_audio.subprocess.Popen',side_effect=spawn),
              patch.object(capture,'accept_samples',side_effect=receive)):
            capture_native(capture,None)
        self.assertEqual(capture.frames,1)
        self.assertEqual(capture.sample_rate,44100)
        self.assertEqual(capture.status_events,2)
        self.assertTrue(processes[0].terminated)
        self.assertTrue(processes[0].stdout.closed and processes[0].stderr.closed)
        np.testing.assert_array_equal(capture.samples(1),np.array([[.2,-.3]],dtype=np.float32))

    @unittest.skipUnless(sys.platform=='darwin','Native executable check requires macOS')
    def test_compiled_callback_and_packet_self_test_without_capture_permission(self):
        from build_macos_capture import build_helper
        result=subprocess.run([str(build_helper()),'--self-test'],capture_output=True,check=True,timeout=5)
        packets=PacketDecoder().feed(result.stdout)
        self.assertEqual(len(packets),1)
        np.testing.assert_array_equal(packets[0][1],[[.125,-.25],[-.5,.75],[.25,-.75],[1.25,-1.5]])
        self.assertFalse(result.stderr)
