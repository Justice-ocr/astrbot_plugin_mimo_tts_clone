import asyncio
import importlib
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


class V060IntegrationTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_transient_synthesis_retries_once_and_updates_diagnostics(self):
        plugin = self.plugin({"tts_max_retries": 1})
        calls = []

        class Client:
            async def synthesize_to_file(self, **kwargs):
                calls.append(kwargs)
                if len(calls) == 1:
                    raise self_module.MimoTransientError("temporary")
                output = Path(kwargs["output_path"])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
                return output

        self_module = self.module
        fake_client = Client()
        plugin._acquire_client = lambda: fake_client
        plugin._release_client = lambda _client: None
        sleeps = []
        plugin._reliability = self.module.ReliabilityController(
            max_retries=1,
            backoff_base_seconds=1,
            backoff_max_seconds=2,
            sleep=lambda delay: asyncio.sleep(0, result=sleeps.append(delay)),
        )
        voice = types.SimpleNamespace(style_tags="")
        output = Path(self.temp_dir.name) / "result.wav"

        result = await plugin._synthesize_text_to_file(
            "hello", voice, voice_data_url="data:audio/wav;base64,AAAA", output_path=output
        )

        self.assertEqual(result, output)
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [1])
        diagnostics = plugin._queue_snapshot()
        self.assertEqual(diagnostics["attempts"], 2)
        self.assertEqual(diagnostics["retries"], 1)
        await plugin.terminate()

    async def test_persistent_manager_uses_versioned_job_store_and_start_is_idempotent(self):
        plugin = self.plugin()
        manager = plugin._background_manager()
        self.assertEqual(manager._persistence_path, Path(self.temp_dir.name) / "tts_jobs.json")

        await plugin.resume_persisted_tts_jobs()
        workers = tuple(manager._workers)
        await plugin.resume_persisted_tts_jobs()
        self.assertEqual(tuple(manager._workers), workers)
        await plugin.terminate()

    async def test_explicitly_unsupported_platform_is_rejected_but_unknown_is_allowed(self):
        context = _Context()
        context.platform_manager = types.SimpleNamespace(platform_insts=[
            types.SimpleNamespace(meta=lambda: types.SimpleNamespace(
                id="aiocqhttp", name="aiocqhttp", support_proactive_message=False
            ))
        ])
        plugin = self.plugin(context=context)
        with self.assertRaisesRegex(RuntimeError, "不支持主动消息"):
            plugin._preflight_session("aiocqhttp:FriendMessage:1")

        context.platform_manager.platform_insts = []
        plugin._preflight_session("unknown:FriendMessage:1")
        await plugin.terminate()

    async def test_record_failure_retries_file_and_after_delivery_removes_audio(self):
        context = _Context()
        components = []

        async def send(_session, chain):
            component = chain.chain[0]
            components.append(component.__class__.__name__)
            if component.__class__.__name__ == "Record":
                raise RuntimeError("record unsupported")
            return True

        context.send_message = send
        plugin = self.plugin({"audio_transport": "path"}, context)
        output = Path(self.temp_dir.name) / "audio.wav"
        output.write_bytes(b"audio")
        job = self.module.TTSJob(session="unknown:FriendMessage:1", text="hello")

        await plugin._deliver_tts_job(job, output)

        self.assertEqual(components, ["Record", "File"])
        self.assertFalse(output.exists())
        await plugin.terminate()

    async def test_file_fallback_can_be_disabled_and_retention_keeps_audio(self):
        context = _Context()

        async def fail_record(_session, _chain):
            raise RuntimeError("send failed")

        context.send_message = fail_record
        plugin = self.plugin({"file_fallback_enabled": False}, context)
        output = Path(self.temp_dir.name) / "failed.wav"
        output.write_bytes(b"audio")
        job = self.module.TTSJob(session="unknown:FriendMessage:1", text="hello")
        with self.assertRaisesRegex(RuntimeError, "send failed"):
            await plugin._deliver_tts_job(job, output)
        self.assertTrue(output.exists())
        await plugin.terminate()

        context = _Context()
        plugin = self.plugin({"background_audio_cleanup": "retention"}, context)
        retained = Path(self.temp_dir.name) / "retained.wav"
        retained.write_bytes(b"audio")
        await plugin._deliver_tts_job(job, retained)
        self.assertTrue(retained.exists())
        await plugin.terminate()


if __name__ == "__main__":
    unittest.main()
