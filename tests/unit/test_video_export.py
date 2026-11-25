"""Pure video timing and encoder contracts; no encoder or Qt needed."""
import subprocess
import unittest
from types import SimpleNamespace
from phosphor.engine import video_export
from phosphor.engine.video_export import find_ffmpeg, ffmpeg_command, frame_ranges, frame_count
from phosphor.storage.app_files import DEFAULT_PREFERENCES, validated_preferences


class VideoExportTests(unittest.TestCase):
    def test_encoder_candidates(self):
        for platform in ('win32', 'darwin'):
            self.assertEqual(video_export.encoder_candidates('Software', platform), ('libx264',))
        self.assertEqual(video_export.encoder_candidates('Auto', 'darwin'), ('libx264',))
        self.assertEqual(video_export.encoder_candidates('Auto', 'win32'),
                         ('h264_nvenc', 'h264_amf', 'h264_qsv', 'libx264'))
        self.assertEqual(video_export.encoder_candidates('Hardware', 'darwin'),
                         ('h264_videotoolbox', 'libx264'))
        with self.assertRaises(ValueError):
            video_export.encoder_candidates('invalid', 'win32')

    def test_choose_encoder(self):
        calls = []
        def probe(ffmpeg, codec):
            calls.append((ffmpeg, codec))
            return codec == 'h264_amf'
        self.assertEqual(video_export.choose_encoder('f', 'Auto', 'win32', probe), 'h264_amf')
        self.assertEqual(calls, [('f', 'h264_nvenc'), ('f', 'h264_amf')])
        calls.clear()
        def failing_probe(ffmpeg, codec):
            calls.append((ffmpeg, codec))
            return False
        self.assertEqual(video_export.choose_encoder('f', 'Hardware', 'win32', failing_probe), 'libx264')
        self.assertEqual(calls, [('f', 'h264_nvenc'), ('f', 'h264_amf'), ('f', 'h264_qsv')])
        for platform in ('win32', 'darwin'):
            calls.clear()
            self.assertEqual(video_export.choose_encoder('f', 'Software', platform, probe), 'libx264')
            self.assertEqual(calls, [])
        self.assertEqual(video_export.choose_encoder('f', 'Auto', 'darwin', probe), 'libx264')
        self.assertEqual(calls, [])

    def test_probe_encoder_results_are_cached(self):
        for returncode in (0, 1):
            with self.subTest(returncode=returncode):
                executable = f'probe-result-{returncode}'
                key = (executable, 'h264_nvenc')
                video_export._probed.pop(key, None)
                self.addCleanup(video_export._probed.pop, key, None)
                calls = []
                def run(args, **kwargs):
                    calls.append(args)
                    return SimpleNamespace(returncode=returncode)
                for _ in range(2):
                    self.assertIs(video_export.probe_encoder(executable, 'h264_nvenc', run), returncode == 0)
                self.assertEqual(calls, [video_export.probe_command(executable, 'h264_nvenc')])

    def test_probe_encoder_timeout_is_cached(self):
        key = ('probe-timeout', 'h264_nvenc')
        video_export._probed.pop(key, None)
        self.addCleanup(video_export._probed.pop, key, None)
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            raise subprocess.TimeoutExpired(args, 20)
        for _ in range(2):
            self.assertIs(video_export.probe_encoder(*key, runner=run), False)
        self.assertEqual(len(calls), 1)

    def test_default_command_is_unchanged(self):
        self.assertEqual(ffmpeg_command('f', 1920, 1080, 60, 'a.wav', 'o.mp4'),
                         ['f', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgba', '-s', '1920x1080',
                          '-r', '60', '-i', '-', '-i', 'a.wav', '-c:v', 'libx264', '-crf', '18',
                          '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '320k', '-shortest',
                          '-f', 'mp4', 'o.mp4'])

    def test_video_only_nv12_command(self):
        command = ffmpeg_command('f', 1920, 1080, 60, None, 'v.mkv', 'MKV', 'h264_nvenc', 'nv12')
        self.assertEqual([command[i+1] for i, value in enumerate(command) if value == '-pix_fmt'],
                         ['nv12', 'nv12'])
        start = command.index('-c:v')
        arguments = ['-c:v', 'h264_nvenc', *video_export.CODEC_ARGUMENTS['h264_nvenc']]
        self.assertEqual(command[start:start+len(arguments)], arguments)
        self.assertNotIn('-c:a', command)
        self.assertNotIn('-shortest', command)
        self.assertEqual(command.count('-i'), 1)
        self.assertEqual(command[-3:], ['-f', 'matroska', 'v.mkv'])
        for kwargs in ({'codec': 'unknown'}, {'pixel_format': 'unknown'}):
            with self.subTest(**kwargs), self.assertRaises(ValueError):
                ffmpeg_command('f', 1920, 1080, 60, None, 'v.mkv', 'MKV', **kwargs)

    def test_mux_commands(self):
        for container, audio, format_name in (('MP4', 'aac', 'mp4'), ('MKV', 'flac', 'matroska')):
            with self.subTest(container=container):
                command = video_export.mux_command('f', 'v.mkv', 'a.wav', 'out', container)
                self.assertEqual([command[i+1] for i, value in enumerate(command) if value == '-map'],
                                 ['0:v:0', '1:a:0'])
                self.assertEqual(command[command.index('-c:v')+1], 'copy')
                self.assertEqual(command[command.index('-c:a')+1], audio)
                self.assertIn('-shortest', command)
                self.assertEqual(command[-3:], ['-f', format_name, 'out'])
                if container == 'MP4':
                    self.assertEqual(command[command.index('-b:a')+1], '320k')
                else:
                    self.assertNotIn('-b:a', command)

    def test_discovery_preference_then_path(self):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout='ffmpeg version fake')
        self.assertEqual(find_ffmpeg('custom/ffmpeg', run, lambda _: 'path/ffmpeg'), 'custom/ffmpeg')
        self.assertEqual(calls, [['custom/ffmpeg', '-version']])
        def fallback(args, **kwargs):
            if args[0] == 'bad': raise OSError('missing')
            return run(args, **kwargs)
        self.assertEqual(find_ffmpeg('bad', fallback, lambda _: 'path/ffmpeg'), 'path/ffmpeg')
        self.assertIsNone(find_ffmpeg('', run, lambda _: None, ()))
        # Not on PATH (a Mac app opened from Finder): the known install locations, in order.
        def only_second(args, **kwargs):
            if args[0] != 'known/second': raise OSError('missing')
            return run(args, **kwargs)
        self.assertEqual(find_ffmpeg('', only_second, lambda _: None, ('known/first', 'known/second')), 'known/second')
        self.assertEqual(find_ffmpeg('', run, lambda _: 'path/ffmpeg', ('known/first',)), 'path/ffmpeg')

    def test_discovery_rejects_invalid_and_timeout(self):
        for result in (SimpleNamespace(returncode=1, stdout='ffmpeg version broken'),
                       SimpleNamespace(returncode=0, stdout='another program')):
            self.assertIsNone(find_ffmpeg('fake', lambda *a, **k: result, lambda _: None))
        def timeout(*args, **kwargs): raise subprocess.TimeoutExpired('fake', 5)
        self.assertIsNone(find_ffmpeg('fake', timeout, lambda _: None))

    def test_commands(self):
        mp4 = ffmpeg_command('fake ffmpeg', 1920, 1080, 60, 'a b.wav', 'out.mp4', 'MP4')
        self.assertEqual(mp4[:2], ['fake ffmpeg', '-y'])
        for flag, value in (('-s', '1920x1080'), ('-r', '60'), ('-c:v', 'libx264'), ('-c:a', 'aac'), ('-b:a', '320k')):
            self.assertEqual(mp4[mp4.index(flag)+1], value)
        self.assertIn('rgba', mp4); self.assertIn('yuv420p', mp4); self.assertIn('a b.wav', mp4)
        mkv = ffmpeg_command('fake', 320, 320, 30, 'audio.flac', 'out.mkv', 'MKV')
        self.assertEqual(mkv[mkv.index('-c:a')+1], 'flac'); self.assertNotIn('-b:a', mkv)
        self.assertEqual(mkv[-2:], ['matroska', 'out.mkv'])

    def test_frame_count(self):
        for seconds, fps, expected in ((0, 30, 0), (2, 30, 60), (2.001, 60, 121), (.01, 30, 1)):
            self.assertEqual(frame_count(seconds, fps), expected)
        with self.assertRaises(ValueError): frame_count(-1, 30)

    def test_exact_sample_cover(self):
        for total, rate, fps in ((96000, 48000, 30), (96001, 48000, 60), (12345, 44100, 60), (1, 48000, 30), (0, 48000, 30)):
            ranges = list(frame_ranges(total, rate, fps))
            self.assertEqual(len(ranges), frame_count(total/rate, fps))
            self.assertEqual([n for start, end in ranges for n in range(start, end)], list(range(total)))
            if ranges:
                self.assertEqual(ranges[-1][1], total)
                self.assertTrue(all(a[1] == b[0] for a, b in zip(ranges, ranges[1:])))
        self.assertEqual(list(frame_ranges(1601, 48000, 30)), [(0, 1600), (1600, 1601)])

    def test_older_preferences(self):
        old = dict(DEFAULT_PREFERENCES); old.pop('ffmpeg_path')
        self.assertEqual(validated_preferences(old)['ffmpeg_path'], '')
