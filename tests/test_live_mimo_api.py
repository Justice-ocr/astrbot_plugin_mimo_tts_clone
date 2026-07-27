import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_mimo_tts_clone.core.audio_codec import (
    encode_voice_file_data_url,
)
from astrbot_plugin_mimo_tts_clone.core.mimo_official_client import (
    MimoOfficialClient,
    MimoTTSConfig,
)


@unittest.skipUnless(
    os.getenv("MIMO_TTS_LIVE_TEST") == "1",
    "set MIMO_TTS_LIVE_TEST=1 to run the real MiMo API test",
)
class LiveMimoAPITests(unittest.TestCase):
    def test_voiceclone_returns_valid_wav(self):
        api_key = os.getenv("MIMO_API_KEY", "").strip()
        voice_file = Path(os.getenv("MIMO_VOICE_FILE", "")).expanduser()
        if not api_key or not voice_file.is_file():
            self.skipTest("MIMO_API_KEY and an existing MIMO_VOICE_FILE are required")

        async def scenario():
            client = MimoOfficialClient(
                MimoTTSConfig(
                    api_key=api_key,
                    base_url=os.getenv("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1"),
                    model=os.getenv("MIMO_TTS_MODEL", "mimo-v2.5-tts-voiceclone"),
                    max_retries=0,
                )
            )
            try:
                with tempfile.TemporaryDirectory() as temp_dir:
                    output = Path(temp_dir) / "live.wav"
                    result = await client.synthesize_to_file(
                        text=os.getenv("MIMO_TTS_LIVE_TEXT", "连接测试，声音工作正常。"),
                        voice_data_url=encode_voice_file_data_url(
                            voice_file, max_bytes=10 * 1024 * 1024
                        ),
                        output_path=output,
                    )
                    self.assertEqual(result, output)
                    self.assertTrue(output.read_bytes().startswith(b"RIFF"))
            finally:
                await client.close()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
