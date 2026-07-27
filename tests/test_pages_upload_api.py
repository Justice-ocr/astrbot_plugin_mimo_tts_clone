from pathlib import Path
import asyncio
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_mimo_tts_clone.core.audio_codec import AudioValidationError
from astrbot_plugin_mimo_tts_clone.core.pages_upload import store_voice_sample
from astrbot_plugin_mimo_tts_clone.core.voice_store import VoiceStore


class PagesUploadAPITests(unittest.TestCase):
    def test_store_voice_sample_accepts_bridge_file_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            voice_store = VoiceStore(temp_dir)

            voice = asyncio.run(
                store_voice_sample(
                    voice_store=voice_store,
                    data_dir=temp_dir,
                    max_voice_file_bytes=10 * 1024 * 1024,
                    data=b"ID3\x04\x00\x00\x00\x00\x00\x00mp3-data",
                    filename="voice.mp3",
                    metadata={"name": "知更鸟", "consent_confirmed": "true"},
                )
            )

            saved = Path(voice.audio_path)
            self.assertTrue(saved.is_file())
            self.assertEqual(voice.name, "知更鸟")
            self.assertEqual(saved.suffix, ".mp3")

    def test_store_voice_sample_requires_explicit_consent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            voice_store = VoiceStore(temp_dir)

            with self.assertRaises(AudioValidationError):
                asyncio.run(
                    store_voice_sample(
                        voice_store=voice_store,
                        data_dir=temp_dir,
                        max_voice_file_bytes=10 * 1024 * 1024,
                        data=b"ID3" + b"\x00" * 32,
                        filename="voice.mp3",
                        metadata={"name": "未授权"},
                    )
                )
