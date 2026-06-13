from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_mimo_tts_clone.core.audio_codec import (
    AudioValidationError,
    encode_voice_file_data_url,
)


class AudioCodecTests(unittest.TestCase):
    def test_encode_voice_file_data_url_accepts_mp3(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            sample = Path(temp_dir) / "voice.mp3"
            sample.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00mp3-data")

            data_url = encode_voice_file_data_url(sample, max_bytes=100)

        self.assertTrue(data_url.startswith("data:audio/mpeg;base64,SUQz"))

    def test_encode_voice_file_data_url_rejects_unsupported_format(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            sample = Path(temp_dir) / "voice.ogg"
            sample.write_bytes(b"ogg-data")

            with self.assertRaisesRegex(AudioValidationError, "Unsupported audio format"):
                encode_voice_file_data_url(sample, max_bytes=100)

    def test_encode_voice_file_data_url_rejects_large_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            sample = Path(temp_dir) / "voice.wav"
            sample.write_bytes(b"RIFF" + b"0" * 4 + b"WAVE" + b"0" * 89)

            with self.assertRaisesRegex(AudioValidationError, "Voice file too large"):
                encode_voice_file_data_url(sample, max_bytes=100)

    def test_encode_voice_file_data_url_rejects_invalid_audio_header(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            sample = Path(temp_dir) / "voice.mp3"
            sample.write_bytes(b"not really an mp3")

            with self.assertRaisesRegex(AudioValidationError, "Invalid mp3 audio header"):
                encode_voice_file_data_url(sample, max_bytes=100)

    def test_encode_voice_file_data_url_rejects_large_base64_payload(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            sample = Path(temp_dir) / "voice.wav"
            sample.write_bytes(b"RIFF" + b"0" * 4 + b"WAVE" + b"0" * 20)

            with self.assertRaisesRegex(AudioValidationError, "Base64 audio payload too large"):
                encode_voice_file_data_url(sample, max_bytes=100, max_base64_chars=40)
