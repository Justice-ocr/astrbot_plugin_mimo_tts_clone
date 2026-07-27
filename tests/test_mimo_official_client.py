from pathlib import Path
import asyncio
import base64
import sys
import tempfile
import types
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_mimo_tts_clone.core.mimo_official_client import (
    MimoAuthenticationError,
    MimoInvalidResponseError,
    MimoOfficialClient,
    MimoRateLimitError,
    MimoTTSConfig,
    MimoTransientError,
)


class _Completions:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    async def create(self, **_payload):
        if self.error:
            raise self.error
        return self.result


class _OpenAIClient:
    def __init__(self, result=None, error=None):
        self.chat = types.SimpleNamespace(completions=_Completions(result, error))
        self.closed = False

    async def close(self):
        self.closed = True


def _completion(audio_data):
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(
            finish_reason="stop",
            message=types.SimpleNamespace(audio=types.SimpleNamespace(data=audio_data)),
        )]
    )


class MimoOfficialClientTests(unittest.TestCase):
    def test_build_payload_uses_assistant_text_and_voice_data_url(self):
        client = MimoOfficialClient(MimoTTSConfig(api_key="token"))

        payload = client.build_payload(
            text="今天也要好好吃饭。",
            voice_data_url="data:audio/wav;base64,AAAA",
            context="温柔、自然地朗读",
        )

        self.assertEqual(payload["model"], "mimo-v2.5-tts-voiceclone")
        self.assertEqual(
            payload["messages"],
            [
                {"role": "user", "content": "温柔、自然地朗读"},
                {"role": "assistant", "content": "今天也要好好吃饭。"},
            ],
        )
        self.assertEqual(
            payload["audio"],
            {
                "format": "wav",
                "voice": "data:audio/wav;base64,AAAA",
            },
        )


    def test_build_payload_omits_empty_context(self):
        client = MimoOfficialClient(MimoTTSConfig(api_key="token"))

        payload = client.build_payload(
            text="测试",
            voice_data_url="data:audio/mpeg;base64,AAAA",
            context="",
        )

        self.assertEqual(payload["messages"], [{"role": "assistant", "content": "测试"}])

    def test_classifies_http_failures(self):
        cases = [
            (401, MimoAuthenticationError),
            (429, MimoRateLimitError),
            (503, MimoTransientError),
        ]
        for status, expected in cases:
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temp_dir:
                error = RuntimeError("failed")
                error.status_code = status
                client = MimoOfficialClient(MimoTTSConfig(api_key="token"))
                client._openai_client = _OpenAIClient(error=error)
                with self.assertRaises(expected):
                    asyncio.run(client.synthesize_to_file(
                        text="test",
                        voice_data_url="data:audio/wav;base64,AAAA",
                        output_path=Path(temp_dir) / "out.wav",
                    ))

    def test_rejects_invalid_audio_without_partial_file(self):
        for audio_data in ("not-base64", base64.b64encode(b"not a wav").decode()):
            with self.subTest(audio_data=audio_data), tempfile.TemporaryDirectory() as temp_dir:
                output = Path(temp_dir) / "out.wav"
                client = MimoOfficialClient(MimoTTSConfig(api_key="token"))
                client._openai_client = _OpenAIClient(result=_completion(audio_data))
                with self.assertRaises(MimoInvalidResponseError):
                    asyncio.run(client.synthesize_to_file(
                        text="test",
                        voice_data_url="data:audio/wav;base64,AAAA",
                        output_path=output,
                    ))
                self.assertFalse(output.exists())
                self.assertFalse((Path(temp_dir) / "out.wav.part").exists())

    def test_reuses_and_closes_underlying_client(self):
        client = MimoOfficialClient(MimoTTSConfig(api_key="token"))
        fake = _OpenAIClient()
        client._openai_client = fake

        first = asyncio.run(client._get_client())
        second = asyncio.run(client._get_client())
        asyncio.run(client.close())

        self.assertIs(first, second)
        self.assertTrue(fake.closed)
        self.assertIsNone(client._openai_client)
