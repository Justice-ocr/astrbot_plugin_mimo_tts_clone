import asyncio
import base64
import importlib
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_mimo_tts_clone.tests.test_config_persistence import (
    _Context,
    _StarTools,
    _install_astrbot_stubs,
)


class V070DeliveryTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        _install_astrbot_stubs()
        cls.module = importlib.import_module("astrbot_plugin_mimo_tts_clone.main")

    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        _StarTools.data_dir = self.temp_dir.name
        self.module.StarTools.data_dir = self.temp_dir.name

    async def asyncTearDown(self):
        self.temp_dir.cleanup()

    def plugin(self, config=None, context=None):
        return self.module.MimoTTSClonePlugin(context or _Context(), config or {})

    async def test_audio_source_uses_base64_by_default(self):
        plugin = self.plugin()
        audio = Path(self.temp_dir.name) / "audio.wav"
        audio.write_bytes(b"test-audio")

        source = plugin._audio_source(audio)

        self.assertTrue(source.startswith("base64://"))
        self.assertEqual(base64.b64decode(source.removeprefix("base64://")), b"test-audio")
        await plugin.terminate()

    async def test_shared_path_stages_audio_and_returns_windows_path(self):
        linux_root = Path(self.temp_dir.name) / "shared"
        plugin = self.plugin({
            "audio_transport": "shared_path",
            "shared_path_linux": str(linux_root),
            "shared_path_windows": r"C:\Users\Public\mimo_tts_audio",
        })
        audio = Path(self.temp_dir.name) / "audio.wav"
        audio.write_bytes(b"shared-audio")

        source, staged = plugin._transport_source(audio)

        self.assertEqual(source, r"C:\Users\Public\mimo_tts_audio\mimo_tts_transport_" + staged.name.split("mimo_tts_transport_", 1)[1])
        self.assertIsNotNone(staged)
        self.assertTrue(staged.exists())
        self.assertEqual(staged.read_bytes(), b"shared-audio")
        staged.unlink()
        await plugin.terminate()

    async def test_shared_path_foreground_sends_raw_onebot_record(self):
        class FakeBot:
            def __init__(self):
                self.calls = []

            async def send_group_msg(self, **kwargs):
                self.calls.append(kwargs)

        linux_root = Path(self.temp_dir.name) / "shared"
        plugin = self.plugin({
            "audio_transport": "shared_path",
            "shared_path_linux": str(linux_root),
            "shared_path_windows": r"C:\Users\Public\mimo_tts_audio",
        })
        audio = Path(self.temp_dir.name) / "audio.wav"
        audio.write_bytes(b"shared-audio")
        bot = FakeBot()
        event = types.SimpleNamespace(
            bot=bot,
            message_obj=types.SimpleNamespace(raw_message={"self_id": "10001"}),
            get_group_id=lambda: "20002",
            get_sender_id=lambda: "30003",
        )

        await plugin._send_audio_result(event, audio)

        self.assertEqual(len(bot.calls), 1)
        self.assertEqual(bot.calls[0]["group_id"], 20002)
        self.assertEqual(bot.calls[0]["self_id"], "10001")
        self.assertEqual(bot.calls[0]["message"][0]["type"], "record")
        self.assertTrue(
            bot.calls[0]["message"][0]["data"]["file"].startswith(
                "C:\\Users\\Public\\mimo_tts_audio\\"
            )
        )
        await plugin.terminate()

    async def test_shared_path_background_sends_raw_onebot_private_record(self):
        class FakeBot:
            def __init__(self):
                self.calls = []

            async def send_private_msg(self, **kwargs):
                self.calls.append(kwargs)

        class FakePlatform:
            def __init__(self, bot):
                self.bot = bot

            def meta(self):
                return types.SimpleNamespace(id="知更鸟", name="aiocqhttp")

            def get_client(self):
                return self.bot

        context = _Context()
        bot = FakeBot()
        context.platform_manager = types.SimpleNamespace(
            get_insts=lambda: [FakePlatform(bot)]
        )
        linux_root = Path(self.temp_dir.name) / "shared"
        plugin = self.plugin({
            "audio_transport": "shared_path",
            "shared_path_linux": str(linux_root),
            "shared_path_windows": r"C:\Users\Public\mimo_tts_audio",
        }, context)
        audio = Path(self.temp_dir.name) / "audio.wav"
        audio.write_bytes(b"shared-audio")

        await plugin._send_audio_path_to_session(
            "知更鸟:FriendMessage:40004", audio
        )

        self.assertEqual(bot.calls[0]["user_id"], 40004)
        self.assertEqual(bot.calls[0]["message"][0]["type"], "record")
        self.assertTrue(
            bot.calls[0]["message"][0]["data"]["file"].startswith(
                "C:\\Users\\Public\\mimo_tts_audio\\"
            )
        )
        self.assertEqual(context.sent_messages, [])
        await plugin.terminate()

    async def test_delivery_bundle_generates_segments_concurrently_and_writes_manifest_last(self):
        plugin = self.plugin({"max_concurrency": 2})
        segments = ["first", "second"]
        voice = types.SimpleNamespace()
        context = types.SimpleNamespace(context="style")
        plugin._prepare_synthesis = lambda *_args, **_kwargs: asyncio.sleep(
            0, result=(voice, context, segments, "data:audio/wav;base64,AAAA")
        )
        active = 0
        maximum = 0
        both_started = asyncio.Event()
        release = asyncio.Event()

        async def fake_synthesize(text, _voice, **kwargs):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                both_started.set()
            await release.wait()
            kwargs["output_path"].write_bytes(text.encode("ascii"))
            active -= 1
            return kwargs["output_path"]

        plugin._synthesize_text_to_file = fake_synthesize
        job = self.module.TTSJob(session="aiocqhttp:FriendMessage:1", text="ignored")
        task = asyncio.create_task(plugin._synthesize_delivery_bundle(job))

        await asyncio.wait_for(both_started.wait(), timeout=1)
        self.assertFalse(task.done())
        self.assertEqual(list((Path(self.temp_dir.name) / "outputs").glob("*/manifest.json")), [])
        release.set()
        manifest = await asyncio.wait_for(task, timeout=1)

        self.assertEqual(maximum, 2)
        data = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(data["segments"], ["part000.wav", "part001.wav"])
        await plugin.terminate()

    async def test_background_bundle_sends_independent_base64_records_in_order(self):
        context = _Context()
        plugin = self.plugin({"background_audio_cleanup": "after_delivery"}, context)
        bundle = Path(self.temp_dir.name) / "outputs" / "mimo_tts_job_test"
        bundle.mkdir(parents=True)
        (bundle / "part000.wav").write_bytes(b"first")
        (bundle / "part001.wav").write_bytes(b"second")
        manifest = bundle / "manifest.json"
        manifest.write_text(
            json.dumps({
                "kind": "mimo_tts_delivery_v1",
                "segments": ["part000.wav", "part001.wav"],
            }),
            encoding="utf-8",
        )
        job = self.module.TTSJob(session="aiocqhttp:FriendMessage:1", text="hello")

        await plugin._deliver_tts_job(job, manifest)

        payloads = [chain.chain[0].file for _session, chain in context.sent_messages]
        self.assertEqual(len(payloads), 2)
        self.assertEqual(
            [base64.b64decode(item.removeprefix("base64://")) for item in payloads],
            [b"first", b"second"],
        )
        self.assertFalse(bundle.exists())
        await plugin.terminate()

    async def test_delivery_bundle_failure_cleans_all_segment_outputs(self):
        plugin = self.plugin({"max_concurrency": 2})
        voice = types.SimpleNamespace()
        context = types.SimpleNamespace(context="style")
        plugin._prepare_synthesis = lambda *_args, **_kwargs: asyncio.sleep(
            0,
            result=(voice, context, ["first", "second"], "data:audio/wav;base64,AAAA"),
        )

        async def fake_synthesize(text, _voice, **kwargs):
            kwargs["output_path"].write_bytes(text.encode("ascii"))
            if text == "second":
                raise RuntimeError("segment failed")
            return kwargs["output_path"]

        plugin._synthesize_text_to_file = fake_synthesize
        job = self.module.TTSJob(session="aiocqhttp:FriendMessage:1", text="ignored")

        with self.assertRaisesRegex(RuntimeError, "segment failed"):
            await plugin._synthesize_delivery_bundle(job)

        outputs = Path(self.temp_dir.name) / "outputs"
        self.assertEqual(list(outputs.iterdir()), [])
        await plugin.terminate()

    async def test_oversized_segment_fails_without_path_fallback(self):
        context = _Context()
        plugin = self.plugin({"base64_max_mb": 1}, context)
        audio = Path(self.temp_dir.name) / "large.wav"
        audio.write_bytes(b"x" * (1024 * 1024 + 1))
        job = self.module.TTSJob(session="aiocqhttp:FriendMessage:1", text="hello")

        with self.assertRaisesRegex(RuntimeError, "超过 Base64 上限"):
            await plugin._deliver_tts_job(job, audio)

        self.assertEqual(context.sent_messages, [])
        self.assertTrue(audio.exists())
        await plugin.terminate()


if __name__ == "__main__":
    unittest.main()
