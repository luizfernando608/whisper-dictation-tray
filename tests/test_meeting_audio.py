import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

import av
import numpy as np

from whisper_dictation.meeting_audio import MeetingRecorder, recover_recording, _choose


class Clock:
    now = 100.0


class Stream:
    def __init__(self, callback, clock):
        self.callback, self.clock, self.running = callback, clock, False
    def get_time(self):
        return self.clock.now
    def start_stream(self):
        self.running = True
    def is_active(self):
        return self.running
    def stop_stream(self):
        self.running = False
    def close(self):
        pass


class Audio:
    def __init__(self, clock):
        self.clock = clock
        self.streams = []
        self.devices = [dict(index=0, hostApi=0, name="Mic", maxInputChannels=1, defaultSampleRate=16000),
                        dict(index=1, hostApi=0, name="Speakers", maxInputChannels=0, defaultSampleRate=16000),
                        dict(index=2, hostApi=0, name="Speakers [Loopback]", maxInputChannels=1, defaultSampleRate=16000, isLoopbackDevice=True)]
    def get_host_api_info_by_type(self, _):
        return dict(index=0, defaultInputDevice=0, defaultOutputDevice=1)
    def get_device_count(self):
        return len(self.devices)
    def get_device_info_by_index(self, index):
        return self.devices[index]
    def get_loopback_device_info_generator(self):
        return iter([self.devices[2]])
    def open(self, **kwargs):
        stream = Stream(kwargs["stream_callback"], self.clock)
        self.streams.append(stream)
        return stream
    def terminate(self):
        pass


class Module:
    paWASAPI = 13
    paFloat32 = 1
    paAbort = 2
    paContinue = 0
    paInputOverflow = 2
    def __init__(self, clock):
        self.audio = Audio(clock)
    def PyAudio(self):
        return self.audio


class MeetingAudioTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.clock = Clock()
        self.module = Module(self.clock)

    def _feed(self, source, start, value, length=1600):
        data = np.full(length, value, dtype="<f4").tobytes()
        self.module.audio.streams[source].callback(data, length, {"input_buffer_adc_time": 100 + start, "current_time": 100 + start + length / 16000}, 0)

    def _read(self, path):
        with wave.open(str(path)) as audio:
            return np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(float) / 32767

    def test_identical_device_names_require_and_accept_explicit_wasapi_ids(self):
        devices = [dict(index=7, name="USB microphone"), dict(index=12, name="USB microphone")]
        with self.assertRaisesRegex(RuntimeError, "ambíguo"):
            _choose(devices, "USB microphone", 7)
        self.assertEqual(_choose(devices, "wasapi:12:USB microphone", 7)["index"], 12)
        with self.assertRaises(RuntimeError):
            _choose(devices, "wasapi:12:Different name", 7)

    def test_pause_removes_both_sources_and_keeps_common_clock(self):
        with patch("whisper_dictation.meeting_audio._audio_module", return_value=self.module), patch("whisper_dictation.meeting_audio.time.monotonic", side_effect=lambda: self.clock.now):
            recorder = MeetingRecorder(self.root)
            recorder.start()
            for i in range(10):
                self._feed(0, i / 10, 0.6)
                self._feed(1, i / 10, 0.2)
            self.clock.now = 101
            recorder.pause()
            self._feed(0, 1.2, 1)
            self._feed(1, 1.2, 1)
            self.clock.now = 103
            self.assertEqual(recorder.duration, 1)
            recorder.resume()
            for i in range(10):
                self._feed(0, 3 + i / 10, -0.6)
                self._feed(1, 3 + i / 10, -0.2)
            self.clock.now = 104
            audio = recorder.stop()
            self.assertFalse(recorder.active)
            self.assertEqual(recorder.duration, 2)
        samples = self._read(audio)
        self.assertEqual(len(samples), 32000)
        np.testing.assert_allclose(samples[:16000], 0.4, atol=1e-4)
        np.testing.assert_allclose(samples[16000:], -0.4, atol=1e-4)

    def test_silent_output_and_delayed_microphone_are_preserved_without_clipping(self):
        with patch("whisper_dictation.meeting_audio._audio_module", return_value=self.module), patch("whisper_dictation.meeting_audio.time.monotonic", side_effect=lambda: self.clock.now):
            recorder = MeetingRecorder(self.root)
            recorder.start()
            self._feed(0, .25, .8, 4000)
            self._feed(1, .75, 2, 4000)
            self._feed(0, .75, 2, 4000)
            self.clock.now = 101
            audio = recorder.stop()
        samples = self._read(audio)
        self.assertEqual(len(samples), 16000)
        self.assertEqual(np.max(np.abs(samples[:4000])), 0)
        np.testing.assert_allclose(samples[4000:8000], .4, atol=1e-4)
        np.testing.assert_allclose(samples[8000:12000], 0, atol=1e-4)
        np.testing.assert_allclose(samples[12000:], 1, atol=1e-4)

    def test_recovery_ignores_torn_journal_and_downsampling_suppresses_aliasing(self):
        samples = (np.sin(np.arange(4800) * 2 * np.pi * 12000 / 48000) * .8).astype("<f4")
        (self.root / "capture.json").write_text(json.dumps(dict(duration=.1, sources={"microphone": {"rate": 48000}})))
        (self.root / "microphone.pcm").write_bytes(samples.tobytes() + b"partial")
        (self.root / "microphone.jsonl").write_text(json.dumps(dict(offset=0, frames=4800, start=0)) + '\n{"off')
        audio = recover_recording(self.root)
        result = self._read(audio)
        self.assertEqual(len(result), 1600)
        self.assertLess(float(np.sqrt(np.mean(result[50:-50] ** 2))), .01)

    def test_device_failure_retains_partial_audio_and_reports_error(self):
        with patch("whisper_dictation.meeting_audio._audio_module", return_value=self.module), patch("whisper_dictation.meeting_audio.time.monotonic", side_effect=lambda: self.clock.now):
            recorder = MeetingRecorder(self.root)
            recorder.start()
            self._feed(0, 0, .5)
            self.clock.now = 100.1
            recorder._set_error("Device disconnected")
            audio = recorder.stop()
        self.assertEqual(recorder.error, "Device disconnected")
        self.assertTrue(audio.exists())
        self.assertEqual(json.loads((self.root / "capture.json").read_text())["status"], "interrupted")

    def test_continuous_resampling_has_no_zero_gaps_between_1024_frame_blocks(self):
        samples = (.8 * np.sin(np.arange(48000) * 2 * np.pi * 440 / 48000)).astype("<f4")
        (self.root / "capture.json").write_text(json.dumps(dict(duration=1, sources={"microphone": {"rate": 48000}})))
        (self.root / "microphone.pcm").write_bytes(samples.tobytes())
        with (self.root / "microphone.jsonl").open("w") as journal:
            for first in range(0, len(samples), 1024):
                journal.write(json.dumps(dict(offset=first*4, frames=min(1024, len(samples)-first), start=first/48000)) + "\n")
        resampler = av.AudioResampler(format="flt", layout="mono", rate=16000)
        frame = av.AudioFrame.from_ndarray(samples.reshape(1, -1), format="flt", layout="mono")
        frame.sample_rate = 48000
        expected = np.concatenate([f.to_ndarray().reshape(-1) for f in resampler.resample(frame) + resampler.resample(None)])
        actual = self._read(recover_recording(self.root))
        self.assertEqual(len(actual), 16000)
        self.assertLess(float(np.sqrt(np.mean((actual-expected) ** 2))), 5e-5)
        self.assertLess(float(np.max(np.abs(actual-expected))), 1e-4)

    def test_watchdog_suspend_cutoff_excludes_sleep_and_racing_wakeup_callbacks(self):
        watch = MeetingRecorder._watch_loop
        with patch("whisper_dictation.meeting_audio._audio_module", return_value=self.module), patch("whisper_dictation.meeting_audio.time.monotonic", side_effect=lambda: self.clock.now), patch.object(MeetingRecorder, "_watch_loop"):
            recorder = MeetingRecorder(self.root)
            recorder.start()
            self._feed(0, 0, .5)
            self.clock.now = 100.1
            def wakeup(_):
                self.clock.now = 100.3
                self._feed(0, .2, .7)
                self.clock.now = 3700
                self._feed(0, 3600, .5)
                return False
            with patch.object(recorder._shutdown, "wait", side_effect=wakeup):
                watch(recorder)
            audio = recorder.stop()
        self.assertIn("suspensão", recorder.error)
        self.assertAlmostEqual(recorder.duration, .3)
        self.assertEqual(len(self._read(audio)), 4800)
        np.testing.assert_allclose(self._read(audio)[3200:], .35, atol=1e-4)
