"""S2: the output device choice, the device list, following the system default and moving
open playback to another device without stopping."""
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from phosphor.audio import output_devices
from phosphor.audio.audio_preview import AudioPreview, windows_midi_profiles
from phosphor.audio.simulated_output import SimulatedDevice
from phosphor.engine.signal_engine import Settings, Shape
from phosphor.storage.signal_preferences import (DEFAULT_SIGNAL_PREFERENCES, SYSTEM_DEFAULT_DEVICE,
                                                 validated_signal_preferences)

HOSTAPIS = ({'name': 'MME'}, {'name': 'Windows WASAPI'}, {'name': 'Core Audio'})
DEVICES = [
    dict(index=0, name='Speakers (MME)', hostapi=0, max_output_channels=2),
    dict(index=1, name='Microphone', hostapi=1, max_output_channels=0),
    dict(index=2, name='Speakers', hostapi=1, max_output_channels=2),
    dict(index=3, name='Headphones', hostapi=1, max_output_channels=2),
    dict(index=4, name='Headphones', hostapi=1, max_output_channels=2),
    dict(index=5, name='MacBook Pro Speakers', hostapi=2, max_output_channels=2),
]


def fake_sd(devices=DEVICES, default=2):
    calls = []
    def query_devices(device=None, kind=None):
        if device is None and kind is None: return list(devices)
        if device is None: device = default
        return dict(next(d for d in devices if d['index'] == device), default_samplerate=48000.)
    return SimpleNamespace(query_hostapis=lambda: HOSTAPIS, query_devices=query_devices, calls=calls,
                           _terminate=lambda: calls.append('terminate'), _initialize=lambda: calls.append('initialize'))


class DeviceListTests(unittest.TestCase):
    def test_lists_one_host_apis_outputs_once_each(self):
        sd = fake_sd()
        with patch('phosphor.audio.output_devices.sys.platform', 'win32'):
            self.assertEqual(output_devices.outputs(sd), [(2, 'Speakers'), (3, 'Headphones')])  # WASAPI only.
            self.assertEqual(output_devices.find(sd, 'Headphones'), 3)
            self.assertIsNone(output_devices.find(sd, 'Speakers (MME)'))
        with patch('phosphor.audio.output_devices.sys.platform', 'darwin'):
            self.assertEqual(output_devices.names(sd), ['MacBook Pro Speakers'])
        with patch('phosphor.audio.output_devices.sys.platform', 'linux'):
            self.assertEqual(output_devices.names(sd), ['Speakers (MME)', 'Speakers', 'Headphones', 'MacBook Pro Speakers'])
        self.assertEqual(output_devices.device_name(sd), 'Speakers')
        self.assertEqual(output_devices.device_name(sd, 5), 'MacBook Pro Speakers')

    def test_refresh_reinitialises_portaudio_and_leaves_stand_ins_alone(self):
        sd = fake_sd()
        output_devices.refresh(sd)
        self.assertEqual(sd.calls, ['terminate', 'initialize'])
        output_devices.refresh(SimulatedDevice(48000))  # No PortAudio: nothing to do, no error.
        self.assertEqual(output_devices.names(SimulatedDevice(48000, name='Sim')), ['Sim'])

    def test_platform_default_can_be_read_or_is_unknown(self):
        token = output_devices.default_token()
        self.assertTrue(token is None or isinstance(token, (int, str)))
        with patch('phosphor.audio.output_devices.sys.platform', 'linux'):
            self.assertIsNone(output_devices.default_token())
            self.assertIsNone(output_devices.present('Speakers'))


class PreferenceTests(unittest.TestCase):
    def test_system_default_by_default_and_older_files_keep_it(self):
        self.assertEqual(DEFAULT_SIGNAL_PREFERENCES['output_device'], SYSTEM_DEFAULT_DEVICE)
        older = {k: v for k, v in DEFAULT_SIGNAL_PREFERENCES.items() if k != 'output_device'}
        self.assertEqual(validated_signal_preferences(older)['output_device'], SYSTEM_DEFAULT_DEVICE)
        oldest = {k: v for k, v in older.items() if k != 'output_rate'}
        self.assertEqual(validated_signal_preferences(oldest)['output_device'], SYSTEM_DEFAULT_DEVICE)
        self.assertEqual(validated_signal_preferences(dict(DEFAULT_SIGNAL_PREFERENCES, output_device='Headphones'))['output_device'], 'Headphones')
        for bad in (None, 3, 'x' * 300):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validated_signal_preferences(dict(DEFAULT_SIGNAL_PREFERENCES, output_device=bad))


class ChoiceTests(unittest.TestCase):
    def test_chosen_device_is_resolved_or_falls_back_with_a_notice(self):
        sd = fake_sd(); player = AudioPreview()
        with patch('phosphor.audio.output_devices.sys.platform', 'win32'):
            self.assertEqual(player._resolve_device(sd), (None, '', True))
            player.output_device = 'Headphones'
            self.assertEqual(player._resolve_device(sd), (3, '', False))
            player.output_device = 'USB DAC'
            self.assertEqual(player._resolve_device(sd), (None, 'USB DAC is not connected; playing on the system default.', True))

    def test_profiles_carry_the_chosen_device(self):
        player = AudioPreview()
        self.assertEqual(player._stream_profiles(None, 7)[0]['device'], 7)
        self.assertNotIn('device', player._stream_profiles(None)[0])
        player.live = True
        with patch('phosphor.audio.audio_preview.sys.platform', 'darwin'):
            self.assertEqual([p['device'] for p in player._stream_profiles(None, 7)], [7, 7])
        sd = SimpleNamespace(query_hostapis=lambda: ({'name': 'Windows WASAPI', 'default_output_device': 2},),
                             WasapiSettings=lambda **k: SimpleNamespace(**k))
        self.assertEqual([p.get('device') for p in windows_midi_profiles(sd, 9)], [9, 9, None])
        self.assertEqual([p.get('device') for p in windows_midi_profiles(sd)], [2, 2, None])

    def test_watcher_asks_for_a_reopen_only_when_the_output_changed(self):
        player = AudioPreview()
        with patch.object(AudioPreview, 'running', True), patch('phosphor.audio.output_devices.default_token', return_value=5):
            player._stream_state = ('', False, True, 5)
            self.assertFalse(player.check_output())
            player._stream_state = ('', False, True, 4)  # System default, and the default changed.
            self.assertTrue(player.check_output()); self.assertTrue(player._reopen.is_set())
            player._reopen.clear(); player.output_name = 'Headphones'; player._stream_state = ('', False, False, 4)
            with patch('phosphor.audio.output_devices.present', return_value=True): self.assertFalse(player.check_output())
            with patch('phosphor.audio.output_devices.present', return_value=False): self.assertTrue(player.check_output())
        player._reopen.clear()
        player.output_device = 'Speakers'  # Stopped: only the cached device rate is forgotten.
        self.assertFalse(player._reopen.is_set())


class FlakyDevice(SimulatedDevice):
    """A simulated device whose first stream fails after a few writes, as an unplugged one does."""
    def __init__(self, *args, fail_after=None, **kwargs):
        super().__init__(*args, **kwargs); self.fail_after = fail_after

    def OutputStream(self, **options):
        stream = super().OutputStream(**options)
        if self.fail_after is not None and len(self.streams) == 1:
            write, limit = stream.write, self.fail_after
            def failing(data):
                if stream.writes >= limit: raise self.PortAudioError('device unplugged')
                return write(data)
            stream.write = failing
        return stream


class LiveReopenTests(unittest.TestCase):
    def play(self, device):
        player = AudioPreview(); player.device = device
        player.start(Settings(shapes=(Shape(),)), 'mixed')
        return player

    def wait(self, condition, seconds=3):
        end = time.perf_counter() + seconds
        while not condition() and time.perf_counter() < end: time.sleep(.01)
        return condition()

    def test_reopen_moves_playback_to_a_new_stream_and_keeps_its_position(self):
        device = SimulatedDevice(48000)
        player = self.play(device)
        try:
            self.assertTrue(self.wait(lambda: player.frames > 4800))
            frames = player.frames
            player._reopen.set()
            self.assertTrue(self.wait(lambda: len(device.streams) == 2 and player.frames > frames + 4800))
            self.assertTrue(device.streams[0].closed)
            self.assertEqual(device.streams[1].samplerate, 48000)
            self.assertEqual((player.error, player.device_notice), ('', 'Output moved to Simulated output.'))
            self.assertTrue(player.running)
        finally:
            player.stop()

    def test_a_lost_device_continues_on_another_stream(self):
        device = FlakyDevice(48000, fail_after=20)
        player = self.play(device)
        try:
            self.assertTrue(self.wait(lambda: len(device.streams) == 2 and device.streams[1].writes > 20))
            self.assertEqual(player.error, ''); self.assertTrue(player.running)
        finally:
            player.stop()

    def test_no_device_that_opens_ends_playback_with_the_reason(self):
        device = SimulatedDevice(48000)
        player = self.play(device)
        self.assertTrue(self.wait(lambda: player.frames > 0))
        device.rates = (44100,)  # The new device refuses the stream's rate.
        player._reopen.set()
        self.assertTrue(self.wait(lambda: not player.running))
        self.assertIn('no device opened at 48 kHz', player.error)
        player.stop()


class EditorTests(unittest.TestCase):
    def test_signal_output_lists_devices_and_marks_a_missing_choice(self):
        from PySide6.QtWidgets import QApplication
        from phosphor.editor.preference_panels import SignalOutputEditor
        from phosphor.editor.qt_app import SliderInput
        app = QApplication.instance() or QApplication([])
        devices = ['Speakers', 'Headphones']
        editor = SignalOutputEditor(SliderInput, dict(DEFAULT_SIGNAL_PREFERENCES, output_device='USB DAC'), lambda: devices)
        combo = editor.device
        self.assertEqual([combo.itemText(i) for i in range(combo.count())],
                         ['System default', 'Speakers', 'Headphones', 'USB DAC (not connected)'])
        self.assertEqual(editor.values()['output_device'], 'USB DAC')
        seen = []; editor.changed.connect(seen.append)
        combo.setCurrentIndex(combo.findData('Headphones'))
        self.assertEqual(seen[-1]['output_device'], 'Headphones')
        devices.append('USB DAC'); combo.fill()  # Plugged in: listed plainly, the choice kept.
        self.assertEqual(combo.itemText(combo.count() - 1), 'USB DAC')
        self.assertEqual(editor.values()['output_device'], 'Headphones')
        combo.setCurrentIndex(0)
        self.assertEqual(validated_signal_preferences(editor.values())['output_device'], SYSTEM_DEFAULT_DEVICE)
        editor.deleteLater(); app.processEvents()


if __name__ == '__main__':
    unittest.main()
