"""Encoder discovery and sample-clock video timing (no Qt dependency)."""
import math
import shutil
import subprocess
import sys

# A Mac app opened from Finder or a DMG gets launchd's PATH (/usr/bin:/bin:/usr/sbin:/sbin),
# without Homebrew or MacPorts, so look where they install too.
KNOWN_LOCATIONS = (('/opt/homebrew/bin/ffmpeg', '/usr/local/bin/ffmpeg', '/opt/local/bin/ffmpeg')
                   if sys.platform == 'darwin' else ())


def find_ffmpeg(preference_path='', runner=subprocess.run, which=shutil.which, known=KNOWN_LOCATIONS):
    for path in dict.fromkeys(filter(None, (preference_path, which('ffmpeg'), *known))):
        try:
            result = runner([str(path), '-version'], capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and 'ffmpeg version' in result.stdout.lower(): return str(path)
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def frame_count(seconds, fps):
    if not math.isfinite(seconds) or seconds < 0 or fps <= 0: raise ValueError('Invalid video timing.')
    return math.ceil(seconds * fps)


def frame_ranges(total_samples, rate, fps):
    if total_samples < 0 or rate <= 0 or fps <= 0: raise ValueError('Invalid sample timing.')
    for k in range(frame_count(total_samples / rate, fps)):
        yield min(total_samples, round(k * rate / fps)), min(total_samples, round((k + 1) * rate / fps))


ENCODER_CHOICES = ('Auto', 'Hardware', 'Software')
SOFTWARE_CODEC = 'libx264'
# Per-codec quality settings, roughly libx264 -crf 18. Probed before use, so a codec the
# build or the machine lacks (or rejects these options for) is skipped, never fatal.
CODEC_ARGUMENTS = {
    'libx264': ('-crf', '18'),
    'h264_nvenc': ('-preset', 'p4', '-tune', 'hq', '-rc', 'vbr', '-cq', '19', '-b:v', '0'),
    'h264_amf': ('-quality', 'quality', '-rc', 'cqp', '-qp_i', '18', '-qp_p', '20'),
    'h264_qsv': ('-preset', 'slow', '-global_quality', '20'),
    'h264_videotoolbox': ('-q:v', '65'),
}


def hardware_codecs(platform=sys.platform):
    return ('h264_videotoolbox',) if platform == 'darwin' else ('h264_nvenc', 'h264_amf', 'h264_qsv')


def encoder_candidates(choice, platform=sys.platform):
    """Codecs to try in order; libx264 is always last, so export never needs hardware.

    Auto uses hardware only where it measured faster: NVENC 4K ran 73 fps against
    libx264's 50 on the Windows workstation, while the Apple M5's VideoToolbox (66 fps)
    was slower than libx264 (88 fps), so Auto stays on libx264 on macOS.
    """
    if choice not in ENCODER_CHOICES: raise ValueError('Select Auto, Hardware or Software.')
    if choice == 'Software' or (choice == 'Auto' and platform == 'darwin'): return (SOFTWARE_CODEC,)
    return (*hardware_codecs(platform), SOFTWARE_CODEC)


def probe_command(ffmpeg, codec):
    return [str(ffmpeg), '-hide_banner', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=256x256:r=30',
            '-frames:v', '3', '-pix_fmt', 'nv12', '-c:v', codec, *CODEC_ARGUMENTS[codec], '-f', 'null', '-']


_probed = {}


def probe_encoder(ffmpeg, codec, runner=subprocess.run):
    """True when this ffmpeg can encode a few frames with codec (cached per session)."""
    key = (str(ffmpeg), codec)
    if key not in _probed:
        try: _probed[key] = runner(probe_command(ffmpeg, codec), capture_output=True, timeout=20).returncode == 0
        except (OSError, subprocess.SubprocessError): _probed[key] = False
    return _probed[key]


def choose_encoder(ffmpeg, choice, platform=sys.platform, probe=probe_encoder):
    for codec in encoder_candidates(choice, platform):
        if codec == SOFTWARE_CODEC or probe(ffmpeg, codec): return codec
    return SOFTWARE_CODEC


def ffmpeg_command(ffmpeg, w, h, fps, audio_path, out_path, container='MP4', codec=SOFTWARE_CODEC, pixel_format='rgba'):
    """pixel_format is the piped frames' format: rgba (converted by ffmpeg to yuv420p) or
    nv12 (converted on the GPU, export_nv12; passed through). audio_path None writes a
    video-only file (mux_command adds the audio)."""
    if w <= 0 or h <= 0 or w % 2 or h % 2 or fps <= 0: raise ValueError('Video needs positive even dimensions and frame rate.')
    if container.upper() not in ('MP4', 'MKV'): raise ValueError('Select MP4 or MKV.')
    if codec not in CODEC_ARGUMENTS: raise ValueError(f'Unknown video encoder {codec}.')
    if pixel_format not in ('rgba', 'nv12'): raise ValueError('Frames must be rgba or nv12.')
    audio = (['-i', str(audio_path)] if audio_path is not None else [])
    audio_codec = ((['-c:a', *(['aac', '-b:a', '320k'] if container.upper() == 'MP4' else ['flac'])], '-shortest')
                   if audio_path is not None else ([], None))
    return [str(ffmpeg), '-y', '-f', 'rawvideo', '-pix_fmt', pixel_format, '-s', f'{w}x{h}',
            '-r', str(fps), '-i', '-', *audio, '-c:v', codec, *CODEC_ARGUMENTS[codec],
            '-pix_fmt', 'yuv420p' if pixel_format == 'rgba' else 'nv12', *audio_codec[0],
            *([audio_codec[1]] if audio_codec[1] else []), '-f', 'mp4' if container.upper() == 'MP4' else 'matroska', str(out_path)]


def mux_command(ffmpeg, video_path, audio_path, out_path, container='MP4'):
    """Copy the encoded video and encode the audio exactly as ffmpeg_command would."""
    if container.upper() not in ('MP4', 'MKV'): raise ValueError('Select MP4 or MKV.')
    return [str(ffmpeg), '-y', '-i', str(video_path), '-i', str(audio_path), '-map', '0:v:0', '-map', '1:a:0',
            '-c:v', 'copy', '-c:a', *(['aac', '-b:a', '320k'] if container.upper() == 'MP4' else ['flac']),
            '-shortest', '-f', 'mp4' if container.upper() == 'MP4' else 'matroska', str(out_path)]
