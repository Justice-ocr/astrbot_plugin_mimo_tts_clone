import importlib
from pathlib import Path
import sys
import tempfile
import types
import unittest
import asyncio
import base64
import io
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_mimo_tts_clone.core.studio_store import StudioStore
from astrbot_plugin_mimo_tts_clone.core.voice_store import VoiceStore, VoiceProfile
from astrbot_plugin_mimo_tts_clone.core.model_catalog import MODELS, singing_text
from astrbot_plugin_mimo_tts_clone.core.style_director import protect_speech_text
from astrbot_plugin_mimo_tts_clone.core.studio_transfer import import_package, export_package
from astrbot_plugin_mimo_tts_clone.core.mimo_official_client import MimoOfficialClient, MimoTTSConfig
from astrbot_plugin_mimo_tts_clone.tests.test_config_persistence import _Context, _StarTools, _install_astrbot_stubs


class StudioStoreTests(unittest.TestCase):
    def test_transfer_roundtrip_and_reject_invalid_package_atomically(self):
        with tempfile.TemporaryDirectory() as root:
            voices, studio = VoiceStore(root), StudioStore(root)
            voice = voices.add_voice("base", "", "", "", False, type="builtin", builtin_voice="mimo_default")
            studio.save_setting("styles", "soft", {"name": "轻声", "context": "温柔"})
            package = export_package(voices, studio, [voice.id], include_audio=False)
            self.assertNotIn("audio_path", package["voices"][0])
            imported = import_package(voices, studio, package, consent=False)
            self.assertEqual(len(imported), 1)
            self.assertNotEqual(imported[0], voice.id)
            count = len(voices.list_voices())
            package["voices"].append({"name": "bad", "type": "design"})
            with self.assertRaises(ValueError):
                import_package(voices, studio, package, consent=False)
            self.assertEqual(len(voices.list_voices()), count)

    def test_clone_export_requires_consent_and_safe_reference(self):
        with tempfile.TemporaryDirectory() as root:
            voices, studio = VoiceStore(root), StudioStore(root)
            voice = voices.add_voice("clone", "/unmanaged.wav", "", "", True)
            with self.assertRaises(ValueError):
                export_package(voices, studio, [voice.id], include_audio=False)
            with self.assertRaises(ValueError):
                export_package(voices, studio, [voice.id], include_audio=True)

    def test_tags_mode_rejects_body_changes_and_unknown_tags(self):
        original = "晚上好。"
        self.assertEqual(protect_speech_text(original, "(轻声)晚上好。", "tags"), "(轻声)晚上好。")
        for candidate in ("晚上好！", "(唱歌)晚上好。", "亲爱的晚上好。", "晚上 好。"):
            self.assertEqual(protect_speech_text(original, candidate, "tags"), original)
        self.assertEqual(protect_speech_text(original, "你好", "instructions"), original)
        self.assertEqual(protect_speech_text(original, "你好", "optimize"), "你好")

    def test_history_pagination_deletion_and_clamping(self):
        with tempfile.TemporaryDirectory() as root:
            store = StudioStore(root)
            keys = [store.add_history(text=str(i), file=f"{i}.wav") for i in range(21)]
            first = store.history()
            self.assertEqual((len(first["items"]), first["total"], first["pages"]), (10, 21, 3))
            self.assertEqual(first["items"][0]["text"], "20")
            self.assertEqual(len(store.history(3)["items"]), 1)
            self.assertEqual(store.history(100)["page"], 3)
            store.delete_history(keys[0])
            self.assertEqual(store.history(3)["page"], 2)
            self.assertNotIn("file", first["items"][0])
            store.delete_history()
            self.assertEqual(store.history()["items"], [])

    def test_paths_cannot_escape_outputs(self):
        with tempfile.TemporaryDirectory() as root:
            store = StudioStore(root)
            (Path(root) / "outside.wav").write_bytes(b"x")
            self.assertIsNone(store.audio_path({"file": "../outside.wav"}))
            self.assertIsNone(store.audio_path({"file": str(Path(root) / "outside.wav")}))

    def test_nonclone_voices_and_legacy_defaults(self):
        with tempfile.TemporaryDirectory() as root:
            store = VoiceStore(root)
            voice = store.add_voice("builtin", "", "", "", False, type="builtin", builtin_voice="mimo_default")
            self.assertEqual(store.find_voice(voice.id).type, "builtin")
            design = store.add_voice("design", "", "", "", False, type="design", design_prompt="温柔女声")
            self.assertEqual(design.model, MODELS["design"])
            self.assertEqual(VoiceProfile.from_dict({"id": "old"}).type, "clone")
            with self.assertRaises(ValueError):
                store.add_voice("bad", "", "", "", False, type="design")

    def test_all_model_payloads(self):
        client = MimoOfficialClient(MimoTTSConfig(api_key="unused"))
        base = client.build_payload(text="hi", voice_data_url="mimo_default", model=MODELS["builtin"])
        self.assertEqual(base["audio"]["voice"], "mimo_default")
        design = client.build_payload(text="hi", voice_data_url="", context="温柔女声", model=MODELS["design"])
        self.assertNotIn("voice", design["audio"])
        with self.assertRaises(ValueError):
            client.build_payload(text="hi", voice_data_url="", model=MODELS["design"])

    def test_singing_tag_only_once(self):
        self.assertEqual(singing_text("(唱歌) (唱歌) 晚安"), "(唱歌)晚安")
        with self.assertRaises(ValueError):
            singing_text(" ")


class StudioIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _install_astrbot_stubs()
        self.module = importlib.import_module("astrbot_plugin_mimo_tts_clone.main")
        self.temp = tempfile.TemporaryDirectory()
        _StarTools.data_dir = self.temp.name
        self.module.StarTools.data_dir = self.temp.name
        self.plugin = self.module.MimoTTSClonePlugin(_Context(), {})

    async def asyncTearDown(self):
        await self.plugin.terminate()
        self.temp.cleanup()

    async def test_builtin_never_reads_reference_file(self):
        voice = self.plugin.voice_store.add_voice("base", "", "", "", False, type="builtin", builtin_voice="mimo_default")
        result = await self.plugin._prepare_synthesis("晚上好", voice_id=voice.id, mode="sing")
        self.assertEqual(result[2], ["(唱歌)晚上好"])
        self.assertEqual(result[3], "mimo_default")

    async def test_design_rejects_singing(self):
        voice = self.plugin.voice_store.add_voice("design", "", "", "", False, type="design", design_prompt="温柔女声")
        with self.assertRaisesRegex(ValueError, "唱歌仅支持"):
            await self.plugin._prepare_synthesis("hi", voice_id=voice.id, mode="sing")

    async def test_synthesis_creates_history(self):
        voice = self.plugin.voice_store.add_voice("base", "", "", "", False, type="builtin", builtin_voice="mimo_default")
        async def fake(text, voice, **kwargs):
            path = kwargs["output_path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fake")
            return path
        self.plugin._synthesize_text_to_file = fake
        await self.plugin.synthesize_text("晚上好", voice_id=voice.id)
        result = self.plugin.studio_store.history()
        self.assertEqual(result["total"], 1)
        self.assertTrue(result["items"][0]["available"])

    async def test_stream_preview_wav_history_and_client_release(self):
        voice = self.plugin.voice_store.add_voice("base", "", "", "", False, type="builtin", builtin_voice="mimo_default")
        class Client:
            async def stream_pcm(self, **kwargs):
                yield b"\x01\x02" * 24
        client = Client()
        released = []
        self.plugin._acquire_client = lambda: client
        self.plugin._release_client = released.append
        job = {"chunks": [], "status": "queued"}
        await self.plugin._run_studio_preview(job, "hello", {
            "voice_id": voice.id, "mode": "speech", "split": False,
            "style_director_enabled": False,
        }, True)
        self.assertEqual(job["status"], "completed")
        self.assertEqual(released, [client])
        raw = base64.b64decode(job["audio_data"].split(",")[1])
        with wave.open(io.BytesIO(raw)) as audio:
            self.assertEqual((audio.getframerate(), audio.getnchannels(), audio.getnframes()), (24000, 1, 24))
        self.assertEqual(self.plugin.studio_store.history()["total"], 1)

    async def test_cancel_stream_releases_client_without_history(self):
        voice = self.plugin.voice_store.add_voice("base", "", "", "", False, type="builtin", builtin_voice="mimo_default")
        ready = asyncio.Event()
        class Client:
            async def stream_pcm(self, **kwargs):
                yield b"\x01\x02"
                ready.set()
                await asyncio.Event().wait()
        client = Client()
        released = []
        self.plugin._acquire_client = lambda: client
        self.plugin._release_client = released.append
        job = {"chunks": [], "status": "queued"}
        task = asyncio.create_task(self.plugin._run_studio_preview(job, "hello", {
            "voice_id": voice.id, "mode": "speech", "split": False,
            "style_director_enabled": False,
        }, True))
        await asyncio.wait_for(ready.wait(), 2)
        task.cancel()
        await task
        self.assertEqual(job["status"], "cancelled")
        self.assertEqual(job["chunks"], [])
        self.assertEqual(released, [client])
        self.assertEqual(self.plugin.studio_store.history()["total"], 0)
