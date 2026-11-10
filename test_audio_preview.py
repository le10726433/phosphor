import unittest
import numpy as np
from audio_preview import AudioPreview
from signal_engine import Settings, render

# Tests patch sys.platform; load PortAudio now so it resolves for the real platform.
try:
    import sounddevice  # noqa: F401
except (ImportError, OSError):
    pass


class PreviewTests(unittest.TestCase):
    def windows_start(self, failures=()):
        """Start Live MIDI as Windows with fake host APIs; failures name refused labels."""
        from dataclasses import replace
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        from midi_synth import MIDI_SOURCE
        player=AudioPreview();opened=[]
        def output(**options):
            exclusive='extra_settings' in options;device=options.get('device')
            label='WASAPI exclusive' if exclusive else 'WASAPI shared' if device is not None else 'System default'
            opened.append((label,options))
            if label in failures:raise RuntimeError(f'{label} refused')
            return MagicMock(latency=.006)
        hostapis=[dict(name='MME',default_output_device=5),dict(name='Windows WASAPI',default_output_device=28)]
        with patch('audio_preview.sys.platform','win32'),patch('sounddevice.OutputStream',side_effect=output),\
                patch('sounddevice.query_hostapis',return_value=hostapis),\
                patch('sounddevice.WasapiSettings',return_value=SimpleNamespace(exclusive=True),create=True),\
                patch.object(player,'_run'),patch.object(player,'_wait_for_callback_stream'):
            player.start(replace(Settings(),source=MIDI_SOURCE),'mixed')
            player.thread.join(timeout=1)
        return player,opened

    def test_windows_live_midi_prefers_exclusive_callback(self):
        player,opened=self.windows_start()
        self.assertEqual([label for label,_ in opened],['WASAPI exclusive'])
        options=opened[0][1]
        self.assertEqual((options['device'],options['blocksize'],options['latency']),(28,128,'low'))
        self.assertEqual(options['callback'],player._callback)
        self.assertEqual((player.output_mode,player.block_frames,player.output_latency),('WASAPI exclusive',128,.006))
        self.assertTrue(player.midi.accepting)

    def test_windows_live_midi_falls_back_when_device_refuses(self):
        player,opened=self.windows_start({'WASAPI exclusive'})
        self.assertEqual([label for label,_ in opened],['WASAPI exclusive','WASAPI shared'])
        self.assertNotIn('callback',opened[1][1])
        self.assertEqual(player.output_mode,'WASAPI shared')
        player,opened=self.windows_start({'WASAPI exclusive','WASAPI shared'})
        self.assertEqual((player.output_mode,player.block_frames),('System default',512))
        self.assertEqual(opened[-1][1]['latency'],.03)
        with self.assertRaises(RuntimeError):
            self.windows_start({'WASAPI exclusive','WASAPI shared','System default'})

    def test_callback_output_matches_worker_output(self):
        from dataclasses import replace
        from types import SimpleNamespace
        import mido
        from midi_synth import MIDI_SOURCE
        settings=replace(Settings(),source=MIDI_SOURCE)
        def prepared():
            player=AudioPreview();player.live=True;player.block_frames=128
            player.history=__import__('collections').deque(maxlen=16)
            player.parameters=(settings,'mixed');player.midi.reset(48000)
            player.midi.receive(mido.Message('note_on',note=60,velocity=100))
            return player
        worker=prepared();written=[]
        class Stream:
            def write(self,block):
                written.append(block.copy())
                if len(written)==8:worker.stop_event.set()
                return False
            def close(self):pass
        worker._run(Stream())
        player=prepared();status=SimpleNamespace(output_underflow=False);delivered=[]
        for index in range(8):
            output=np.ones((128,2),'float32')
            player._callback(output,128,None,SimpleNamespace(output_underflow=index==3))
            delivered.append(output)
        np.testing.assert_array_equal(np.concatenate(delivered),np.concatenate(written))
        self.assertEqual((player.frames,player.underruns,player.error),(1024,1,''))
        np.testing.assert_array_equal(player.samples(1024),np.concatenate(written))

    def test_callback_failure_outputs_silence_and_stops(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock, patch
        player=AudioPreview();output=np.ones((128,2),'float32')
        player.parameters=(Settings(),'mixed')
        with patch.object(player,'_next_block',side_effect=RuntimeError('synthesis failed')):
            player._callback(output,128,None,SimpleNamespace(output_underflow=False))
        self.assertFalse(output.any())
        self.assertEqual(player.error,'synthesis failed')
        stream=MagicMock(active=True)
        player._wait_for_callback_stream(stream)
        stream.stop.assert_called_once();stream.close.assert_called_once()

    def test_callback_stream_reports_unexpected_device_stop(self):
        from unittest.mock import MagicMock
        player=AudioPreview();stream=MagicMock(active=False)
        player._wait_for_callback_stream(stream)
        self.assertEqual(player.error,'Audio device stopped unexpectedly.')
        stream.close.assert_called_once()

    def test_builtin_stream_profiles_are_unchanged(self):
        from dataclasses import replace
        from unittest.mock import patch
        from midi_synth import MIDI_SOURCE
        for platform,source,frames,latency in (('darwin',Settings().source,512,.1),('win32',Settings().source,512,.1)):
            player=AudioPreview()
            with patch('audio_preview.sys.platform',platform),patch('sounddevice.OutputStream') as output,patch.object(player,'_run'):
                output.return_value.latency=.012
                player.start(replace(Settings(),source=source),'mixed')
                player.thread.join(timeout=1)
                self.assertEqual(output.call_args.kwargs['blocksize'],frames)
                self.assertEqual(output.call_args.kwargs['latency'],latency)
                self.assertEqual(player.playback.latency,.012)
                self.assertGreaterEqual(player.history.maxlen*frames,1920)

    def test_mac_live_midi_prefers_callback_and_falls_back_to_blocking(self):
        from dataclasses import replace
        from unittest.mock import MagicMock,patch
        from midi_synth import MIDI_SOURCE
        settings=replace(Settings(),source=MIDI_SOURCE)
        player=AudioPreview()
        with patch('audio_preview.sys.platform','darwin'),patch('sounddevice.OutputStream') as output,patch.object(player,'_wait_for_callback_stream'):
            output.return_value.latency=.019
            player.start(settings,'mixed')
            player.thread.join(timeout=1)
            self.assertEqual(output.call_args.kwargs['callback'],player._callback)
            self.assertEqual((output.call_args.kwargs['blocksize'],output.call_args.kwargs['latency']),(128,'low'))
            self.assertEqual(player.output_mode,'Core Audio callback')
        player=AudioPreview()
        blocking=MagicMock(latency=.062)
        with patch('audio_preview.sys.platform','darwin'),patch('sounddevice.OutputStream',side_effect=[RuntimeError('busy'),blocking]) as output,patch.object(player,'_run'):
            player.start(settings,'mixed')
            player.thread.join(timeout=1)
            self.assertNotIn('callback',output.call_args.kwargs)
            self.assertEqual(player.output_mode,'Core Audio blocking')

    def test_small_live_blocks_apply_events_and_retain_full_trace(self):
        import mido
        from dataclasses import replace
        from midi_synth import MIDI_SOURCE
        player=AudioPreview();player.live=True;player.block_frames=128
        player.history=__import__('collections').deque(maxlen=16)
        player.parameters=(replace(Settings(),source=MIDI_SOURCE),'drawing')
        player.midi.reset(48000)
        blocks=[]
        class Stream:
            def write(self,block):
                blocks.append(block.copy())
                if len(blocks)==1:player.midi.receive(mido.Message('note_on',note=60,velocity=100))
                if len(blocks)==20:player.stop_event.set()
                return False
            def close(self):pass
        player._run(Stream())
        self.assertEqual(player.error,'')
        self.assertEqual(player.frames,2560)
        self.assertFalse(np.any(blocks[0]))
        self.assertTrue(np.any(blocks[1]))
        np.testing.assert_array_equal(player.samples(1920),np.concatenate(blocks)[-1920:])

    def test_worker_uses_contiguous_samples_in_all_modes(self):
        for mode in ('mixed', 'drawing', 'music'):
            with self.subTest(mode=mode):
                player = AudioPreview()
                settings = Settings()
                player.parameters = (settings, mode)

                class Stream:
                    closed = False
                    blocks = []

                    def write(self, block):
                        self.blocks.append(block.copy())
                        if len(self.blocks) == 3:
                            player.stop_event.set()
                        return len(self.blocks) == 2

                    def close(self):
                        self.closed = True

                stream = Stream()
                player._run(stream)
                self.assertTrue(stream.closed)
                self.assertEqual(player.frames, 1536)
                self.assertEqual(player.underruns, 1)
                drawing, music, mixed = render(np.arange(1536) / 48000, settings)
                expected = {'mixed': mixed, 'drawing': drawing * settings.drawing_gain,
                            'music': music * settings.drawing_gain}[mode]
                actual = np.concatenate(stream.blocks)
                np.testing.assert_allclose(actual[240:], expected[240:], atol=3e-8)
                np.testing.assert_array_equal(player.samples(200), actual[-200:])
                self.assertEqual(actual[0, 0], 0)

    def test_generated_playback_publishes_faster_than_viewport(self):
        # Device and synthesis blocks must both fit within a 60 Hz frame interval;
        # a timer alone cannot animate fresh submitted samples any faster.
        from unittest.mock import patch
        player = AudioPreview()
        with patch('sounddevice.OutputStream') as output, patch.object(player, '_run'):
            player.start(Settings(), 'mixed')
            player.thread.join(timeout=1)
            self.assertEqual(output.call_args.kwargs['blocksize'], player.BLOCK_FRAMES)
            self.assertLess(player.BLOCK_FRAMES / 48000, 1 / 60)
        # Five retained blocks must still hold the entire 40 ms scope window.
        self.assertGreaterEqual(player.history.maxlen * player.BLOCK_FRAMES, 1920)

    def test_device_failure_is_reported_and_closed(self):
        player = AudioPreview()
        player.parameters = (Settings(), 'mixed')

        class BrokenStream:
            closed = False

            def write(self, block):
                raise RuntimeError('device disconnected')

            def close(self):
                self.closed = True

        stream = BrokenStream()
        player._run(stream)
        self.assertTrue(stream.closed)
        self.assertEqual(player.error, 'device disconnected')


if __name__ == '__main__':
    unittest.main()
