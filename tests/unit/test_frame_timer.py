"""P7c: the '60 Hz' deadline timer (H4), display pacing for audio (H1) and the Windows timer resolution."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from phosphor.audio import timer_resolution
from phosphor.editor import qt_app


class FakeTimer:
    def __init__(self, active=True): self.active, self.starts = active, []
    def isActive(self): return self.active
    def start(self, ms): self.starts.append(ms)


def window(now, rate='60 Hz'):
    """The attributes paced_frame, schedule_frame and defer_for_audio read, without a Qt window."""
    w = SimpleNamespace(preferences=dict(display_rate=rate), timer=FakeTimer(), frame_timer=FakeTimer(False),
                        views_released=False, next_frame_time=None, frames=[], deferred_frames=0, frame_fresh=False,
                        last_display_time=now, audio=SimpleNamespace(buffered_seconds=lambda: None, output_latency=.1))
    w.display_frame = lambda: w.frames.append(clock.now)
    w.schedule_frame = lambda: qt_app.Window.schedule_frame(w)
    w.defer_for_audio = lambda at: qt_app.Window.defer_for_audio(w, at)
    return w


class Clock:
    now = 0.
    def __call__(self): return self.now


clock = Clock()


class FrameTimerTests(unittest.TestCase):
    def setUp(self):
        clock.now = 10.
        patcher = patch('phosphor.editor.qt_app.time.perf_counter', clock)
        patcher.start(); self.addCleanup(patcher.stop)

    def test_frames_follow_absolute_deadlines(self):
        w = window(clock.now)
        qt_app.Window.paced_frame(w)  # First frame now; the next deadline is 1/60 s later.
        self.assertEqual(w.frames, [10.])
        self.assertAlmostEqual(w.next_frame_time, 10 + 1/60)
        self.assertEqual(w.frame_timer.starts, [16])  # Whole milliseconds, rounded down.
        # The work took 12 ms and the timer fired at 16 ms: the next wait is short, so frames
        # don't drift (the housekeeping timer re-armed from now and gave 40 a second).
        for k in range(1, 60):
            clock.now = 10 + k/60 - .0004
            qt_app.Window.paced_frame(w)
        self.assertEqual(len(w.frames), 60)
        self.assertAlmostEqual(w.next_frame_time, 11.)

    def test_early_wake_reschedules_without_drawing(self):
        w = window(clock.now); w.next_frame_time = 10.005
        qt_app.Window.paced_frame(w)
        self.assertEqual(w.frames, [])
        self.assertEqual(w.frame_timer.starts, [5])

    def test_a_stall_resyncs_instead_of_bursting(self):
        w = window(clock.now); w.next_frame_time = 10.
        w.display_frame = lambda: (w.frames.append(clock.now), setattr(clock, 'now', clock.now + .2))
        qt_app.Window.paced_frame(w)
        self.assertAlmostEqual(w.next_frame_time, 10.2)  # Not 10 + 1/60: no catch-up burst.

    def test_waits_for_the_last_frame_to_reach_the_screen(self):
        w = window(clock.now - .012); w.next_frame_time = clock.now; w.frame_fresh = True  # No swap since.
        qt_app.Window.paced_frame(w)
        self.assertEqual((w.frames, w.frame_timer.starts), ([], [1]))
        clock.now += .014  # 26 ms since the last frame: draw anyway.
        qt_app.Window.paced_frame(w)
        self.assertEqual(len(w.frames), 1)

    def test_other_rates_and_stopped_clock_do_nothing(self):
        for w in (window(clock.now, 'Match monitor'), window(clock.now)):
            if w.preferences['display_rate'] == '60 Hz': w.timer.active = False
            w.next_frame_time = 9.
            qt_app.Window.paced_frame(w)
            self.assertEqual((w.frames, w.next_frame_time, w.frame_timer.starts), ([], None, []))


class DisplayPacingTests(unittest.TestCase):
    def setUp(self):
        clock.now = 10.
        patcher = patch('phosphor.editor.qt_app.time.perf_counter', clock)
        patcher.start(); self.addCleanup(patcher.stop)

    def test_defers_while_the_device_buffer_is_low(self):
        w = window(clock.now - .007)
        for queued, expected in ((None, False), (.08, False), (.05, False), (.049, True), (0., True)):
            w.audio.buffered_seconds = lambda queued=queued: queued
            self.assertEqual(qt_app.Window.defer_for_audio(w, clock.now), expected, queued)
        self.assertEqual(w.deferred_frames, 2)

    def test_never_below_30_frames_a_second(self):
        w = window(clock.now - .034)  # Just over 1/30 s since the last frame.
        w.audio.buffered_seconds = lambda: 0.
        self.assertFalse(qt_app.Window.defer_for_audio(w, clock.now))

    def test_switch_turns_it_off(self):
        w = window(clock.now - .007)
        w.audio.buffered_seconds = lambda: 0.
        with patch.object(qt_app, 'PACE_FOR_AUDIO', False):
            self.assertFalse(qt_app.Window.defer_for_audio(w, clock.now))

    def test_a_deferred_60_hz_frame_waits_without_moving_its_deadline(self):
        w = window(clock.now - .007); w.next_frame_time = clock.now
        w.audio.buffered_seconds = lambda: 0.
        qt_app.Window.paced_frame(w)
        self.assertEqual((w.frames, w.next_frame_time, w.frame_timer.starts), ([], 10., [qt_app.AUDIO_DEFER_MS]))


class TimerResolutionTests(unittest.TestCase):
    def test_begin_and_end_are_counted(self):
        calls = []
        api = SimpleNamespace(timeBeginPeriod=lambda ms: calls.append(('begin', ms)) or 0,
                              timeEndPeriod=lambda ms: calls.append(('end', ms)) or 0)
        with patch.object(timer_resolution.sys, 'platform', 'win32'), patch.object(timer_resolution, '_api', return_value=api), \
                patch.object(timer_resolution, '_count', 0):
            self.assertTrue(timer_resolution.begin())
            self.assertFalse(timer_resolution.begin())  # Nested: already raised.
            timer_resolution.end()
            self.assertTrue(timer_resolution.active())
            timer_resolution.end()
            self.assertFalse(timer_resolution.active())
            timer_resolution.end()  # Unbalanced end is ignored.
        self.assertEqual(calls, [('begin', 1), ('end', 1)])

    def test_other_platforms_do_nothing(self):
        with patch.object(timer_resolution.sys, 'platform', 'darwin'), patch.object(timer_resolution, '_api') as api:
            self.assertFalse(timer_resolution.begin()); timer_resolution.end()
        api.assert_not_called()

    def test_playback_holds_the_resolution_only_while_the_stream_is_open(self):
        import time
        from phosphor.audio.audio_preview import AudioPreview
        from phosphor.audio.simulated_output import SimulatedDevice
        from phosphor.engine.signal_engine import Settings
        events = []
        with patch.object(timer_resolution, 'begin', side_effect=lambda: events.append('begin')), \
                patch.object(timer_resolution, 'end', side_effect=lambda: events.append('end')):
            player = AudioPreview(); player.device = SimulatedDevice(48000)
            player.start(Settings(), 'mixed')
            deadline = time.perf_counter() + 5
            while not events and time.perf_counter() < deadline: time.sleep(.005)
            self.assertEqual(events, ['begin'])
            player.stop()
        self.assertEqual(events, ['begin', 'end'])


if __name__ == '__main__':
    unittest.main()
