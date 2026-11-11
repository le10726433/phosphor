"""Portable, user-facing help for audio effects and X–Y visualization."""
AUDIO_TROUBLESHOOTING = '''Audio effects and X–Y traces

Your playback device can change the signal before Windows loopback capture sees
it. Equalizers, bass boost, speaker compensation, loudness processing and spatial
effects can change relative frequencies, channel relationships or phase. This
can change the X–Y picture even when the music sounds normal.

Volume alone scales the drawing. Frequency-dependent gain or phase changes can
alter its shape. A cleaner-looking trace does not necessarily mean a higher
frame rate, sample rate or better graphics performance.

What our controlled test found — November 7, 2026

On the development PC, seven WASAPI outputs were tested with identical stereo
test audio and visualization settings. All used 48 kHz stereo and rendered at
about 62.5 UI frames/second, with no reported capture/output errors.

AudioBox USB 96 preserved the test waveform's shape with uniform attenuation.
Realtek speakers and digital output initially changed relative tone levels.
After the user disabled Windows audio effects, two repeat Realtek-speaker tests
matched the commanded waveform to numerical precision:

Test tone     Effects enabled     Effects disabled
173 Hz        1.754x gain          1.000x gain
347 Hz        1.200x gain          1.000x gain
613 Hz        1.096x gain          1.000x gain

Rendering remained approximately 62.5 frames/second. Disabling effects restored
waveform fidelity; it did not demonstrate a significant rendering-speed increase.
This isolates the previous change to the effects chain on that PC, not a fault
in every Realtek device. The individual effect was not identified because all
effects were disabled together. These measurements are not a THD or DAC-quality
rating, and the physical analog outputs were not measured.

If the picture differs between playback devices

1. Use the same audio, visualization gain, interpolation and glow settings.
2. Open Windows Settings > System > Sound and select the playback device that
   the app is capturing. Set Audio enhancements to Off, where available.
3. Check Spatial sound and turn it Off for an unprocessed comparison. Older
   Windows/driver panels may instead provide an Enhancements tab with Disable
   all enhancements or Disable all sound effects. Manufacturer audio utilities
   may also contain EQ, surround or speaker-compensation controls.
4. Stop visualization, refresh playback devices if needed, select that endpoint
   and restart visualization. Repeat the same audio and compare. Change one
   effect at a time if you want to identify the cause.

Windows labels and available controls vary by driver/version. This app does not
change Windows device settings, effects or volume automatically.

If the trace is small, flat or appears to freeze

Check that the other application is playing through the selected playback
device. Look at Raw input peak and captured-frame count. Visualization input
gain changes only the displayed size, not playback. Silence clears old capture
data; positive glow decay intentionally leaves a fading afterimage.

If motion stutters, compare with interpolation Off and glow/decay reduced while
keeping the audio unchanged. Those preferences add rendering work. They do not
increase the true capture sample rate. System-audio capture uses the endpoint's
native mix rate; interpolation adds synthetic display points, not audio detail.

Microsoft references

Audio enhancements and troubleshooting:
https://support.microsoft.com/en-gb/windows/hardware/audio/fix-distorted-or-crackling-audio-in-windows

How Windows audio effects work (Audio Processing Objects):
https://learn.microsoft.com/en-us/windows-hardware/drivers/audio/audio-processing-object-architecture
'''

MACOS_AUDIO_TROUBLESHOOTING = '''macOS system-audio visualization

Choose Monitor input > System audio — visualization only, then press START
VISUALIZATION. Play audio in another application. The Mac source mixes system
output apps into stereo; it is not a microphone or a selectable Windows endpoint.

Permission
Allow System Audio Recording when macOS asks. If permission was denied, open
System Settings > Privacy & Security > Screen & System Audio Recording and
allow phosphor Audio Capture, or the app named by macOS as its launcher.
Stop and restart visualization after granting access. Microphone permission is
not required. macOS 14.2 or later is required.

Silence or no trace
Check that the other app is playing and not muted. Protected content may not be
available for capture. Look at the raw input peak and capture status beneath the
input controls. Silence clears the old trace. If capture reports an error, stop
it, check permission, and restart. A command run in a restricted sandbox may not
have access to Core Audio even when the normal desktop app does.

Trace size and smoothness
Visualization input gain changes only the displayed trace. Start at 1x and raise
it for quiet sources. Interpolation adds display points only; it does not recover
missing audio detail. A mono source commonly produces a diagonal line. Stereo
phase, effects and the source material can change the X-Y image.

Capture does not play audio, record it, export it, capture screen images, or open
the microphone. Normal audio from other apps continues through their selected
outputs. Switch back to Generated scene / MIDI to edit and play generated audio.
'''
