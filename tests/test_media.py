from pathlib import Path
import tempfile
import threading
import unittest
import wave

import av
import numpy as np

from whisper_dictation.media import decode_media, MediaCancelled, PCM16WaveWriter


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def _source(self):
        source = self.root / "original.wav"
        with wave.open(str(source), "wb") as output:
            output.setnchannels(2)
            output.setsampwidth(2)
            output.setframerate(48000)
            signal = (np.sin(np.arange(48000) * 2 * np.pi * 440 / 48000) * 16000).astype("<i2")
            output.writeframes(np.column_stack((signal, signal)).tobytes())
        return source

    def test_stereo_media_normalizes_to_mono_16k_without_changing_original(self):
        source = self._source()
        original = source.read_bytes()
        progress = []
        output = self.root / "normalized.wav"
        self.assertAlmostEqual(decode_media(source, output, progress=progress.append), 1.0, places=3)
        with wave.open(str(output)) as audio:
            self.assertEqual((audio.getnchannels(), audio.getframerate(), audio.getsampwidth()), (1, 16000, 2))
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(progress[-1], 1.0)

    def test_cancel_preserves_existing_destination_and_removes_partial(self):
        source = self._source()
        output = self.root / "normalized.wav"
        output.write_bytes(b"existing")
        cancel = threading.Event()
        with self.assertRaises(MediaCancelled):
            decode_media(source, output, cancel, lambda _: cancel.set())
        self.assertEqual(output.read_bytes(), b"existing")
        self.assertFalse(list(self.root.glob("*.part")))

    def test_compressed_and_video_containers_decode_with_bundled_codecs(self):
        source = self._source()
        for extension, codec in [("flac", "flac"), ("ogg", "libopus"), ("mp3", "libmp3lame"), ("m4a", "aac"), ("mp4", "aac"), ("mkv", "flac")]:
            with self.subTest(extension=extension):
                encoded = self.root / ("input." + extension)
                with av.open(str(source)) as incoming, av.open(str(encoded), "w") as outgoing:
                    stream = outgoing.add_stream(codec, rate=48000)
                    stream.layout = "stereo"
                    for frame in incoming.decode(audio=0):
                        for packet in stream.encode(frame):
                            outgoing.mux(packet)
                    for packet in stream.encode(None):
                        outgoing.mux(packet)
                self.assertAlmostEqual(decode_media(encoded, self.root / (extension + ".wav")), 1, delta=0.08)

    def test_rf64_audio_uses_the_same_import_pipeline(self):
        source = self.root / "large-format.wav"
        with PCM16WaveWriter(source, rf64="always") as writer:
            writer.writeframesraw(np.zeros(16000, dtype="<i2").tobytes())
        self.assertEqual(source.read_bytes()[:4], b"RF64")
        self.assertEqual(decode_media(source, self.root / "normalized.wav"), 1)

    def test_invalid_and_video_without_audio_leave_no_partial_output(self):
        invalid = self.root / "broken.mp3"
        invalid.write_bytes(b"not audio")
        with self.assertRaises(Exception):
            decode_media(invalid, self.root / "output.wav")
        video = self.root / "silent.mp4"
        with av.open(str(video), "w") as container:
            stream = container.add_stream("mpeg4", rate=1)
            stream.width, stream.height = 16, 16
            frame = av.VideoFrame.from_ndarray(np.zeros((16, 16, 3), dtype=np.uint8), format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
            for packet in stream.encode(None):
                container.mux(packet)
        with self.assertRaisesRegex(ValueError, "faixa de áudio"):
            decode_media(video, self.root / "output.wav")
        self.assertFalse((self.root / "output.wav").exists())
        self.assertFalse(list(self.root.glob("*.part")))
