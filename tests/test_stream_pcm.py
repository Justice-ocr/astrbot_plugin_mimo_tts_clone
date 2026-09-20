import asyncio
import base64
from types import SimpleNamespace
import unittest

from astrbot_plugin_mimo_tts_clone.core.mimo_official_client import (
    MimoOfficialClient, MimoTTSConfig, MimoInvalidResponseError,
)


class FakeStream:
    def __init__(self, parts):
        self.parts = iter(parts)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            value = next(self.parts)
        except StopIteration:
            raise StopAsyncIteration
        return SimpleNamespace(choices=[SimpleNamespace(
            delta=SimpleNamespace(audio={"data": value}), finish_reason=None,
        )])

    async def close(self):
        self.closed = True


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    def client(self, parts):
        stream = FakeStream(parts)
        client = MimoOfficialClient(MimoTTSConfig(api_key="test"))
        async def create(**payload):
            self.assertEqual(payload["audio"]["format"], "pcm16")
            self.assertTrue(payload["stream"])
            return stream
        client._openai_client = SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=create),
        ))
        return client, stream

    async def test_reassembles_sample_boundaries_and_closes(self):
        client, stream = self.client([base64.b64encode(x).decode() for x in (b"\x01", b"\x02\x03\x04")])
        chunks = [x async for x in client.stream_pcm(text="hi", voice="mimo_default")]
        self.assertEqual(b"".join(chunks), b"\x01\x02\x03\x04")
        self.assertTrue(stream.closed)

    async def test_rejects_incomplete_empty_and_invalid_data(self):
        for parts in ([], ["AQ=="], ["%%%"]):
            client, stream = self.client(parts)
            with self.assertRaises(MimoInvalidResponseError):
                _ = [x async for x in client.stream_pcm(text="hi", voice="mimo_default")]
            self.assertTrue(stream.closed)

    async def test_consumer_cancellation_closes_stream(self):
        client, stream = self.client(["AQI=", "AwQ="])
        generator = client.stream_pcm(text="hi", voice="mimo_default")
        await anext(generator)
        await generator.aclose()
        self.assertTrue(stream.closed)
