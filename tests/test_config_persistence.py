import importlib
import asyncio
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


class _Logger:
    def warning(self, *args, **kwargs):
        pass


class _Star:
    def __init__(self, context):
        self.context = context


class _StarTools:
    data_dir = ""

    @staticmethod
    def get_data_dir(_name):
        return _StarTools.data_dir


class _Context:
    def __init__(self):
        self.routes = []

    def register_web_api(self, *args):
        self.routes.append(args)


def _command_decorator(*_args, **_kwargs):
    def decorate(func):
        return func

    return decorate


def _register_decorator(*_args, **_kwargs):
    def decorate(cls):
        return cls

    return decorate


def _install_astrbot_stubs():
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = _Logger()

    event = types.ModuleType("astrbot.api.event")
    event.AstrMessageEvent = object
    event.filter = types.SimpleNamespace(
        command=_command_decorator,
        llm_tool=_command_decorator,
        on_decorating_result=_command_decorator,
    )

    message_components = types.ModuleType("astrbot.api.message_components")
    message_components.File = object
    message_components.Plain = type("Plain", (), {})
    message_components.Record = object

    star = types.ModuleType("astrbot.api.star")
    star.Context = _Context
    star.Star = _Star
    star.StarTools = _StarTools
    star.register = _register_decorator

    sys.modules.setdefault("astrbot", astrbot)
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = event
    sys.modules["astrbot.api.message_components"] = message_components
    sys.modules["astrbot.api.star"] = star

    quart = types.ModuleType("quart")
    quart.jsonify = lambda payload: payload
    quart.request = types.SimpleNamespace()
    sys.modules["quart"] = quart


class _FailingNativeConfig(dict):
    def save_config(self):
        raise RuntimeError("native write failed")


class _GetOnlyConfig:
    def __init__(self, values):
        self.values = values

    def get(self, key):
        return self.values.get(key)


class ConfigPersistenceTests(unittest.TestCase):
    def setUp(self):
        _install_astrbot_stubs()
        self.module = importlib.import_module("astrbot_plugin_mimo_tts_clone.main")

    def test_pages_config_persists_locally_when_native_save_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            plugin = self.module.MimoTTSClonePlugin(_Context(), _FailingNativeConfig())

            persisted = plugin._update_runtime_config({"api_key": "mimo-secret", "max_text_chars": 321})

            self.assertTrue(persisted["local"])
            self.assertFalse(persisted["native"])
            saved = json.loads((Path(tmp) / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["api_key"], "mimo-secret")
            self.assertEqual(saved["max_text_chars"], 321)

            reloaded = self.module.MimoTTSClonePlugin(_Context(), {})
            self.assertEqual(reloaded.config["api_key"], "mimo-secret")
            self.assertEqual(reloaded.config["max_text_chars"], 321)

    def test_plugin_reads_get_only_native_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            config = _GetOnlyConfig({"api_key": "from-native-get", "max_text_chars": 222})

            plugin = self.module.MimoTTSClonePlugin(_Context(), config)

            self.assertEqual(plugin.config["api_key"], "from-native-get")
            self.assertEqual(plugin.config["max_text_chars"], 222)

    def test_tts_tail_strips_prefixed_and_bare_command_names(self):
        plugin_cls = self.module.MimoTTSClonePlugin

        self.assertEqual(plugin_cls._tail_any("/tts 晚上好", ("tts", "朗读", "语音")), "晚上好")
        self.assertEqual(plugin_cls._tail_any("tts 晚上好", ("tts", "朗读", "语音")), "晚上好")
        self.assertEqual(plugin_cls._tail_any("朗读 晚上好", ("tts", "朗读", "语音")), "晚上好")

    def test_runtime_config_normalizes_delivery_options(self):
        from astrbot_plugin_mimo_tts_clone.core.config import normalize_config

        cfg = normalize_config(
            {
                "reply_mode": "bad",
                "auto_tts_enabled": "true",
                "auto_tts_probability": 2,
                "file_fallback_enabled": "false",
                "output_retention_days": -1,
                "output_max_files": -5,
                "emotion_routing_enabled": "off",
            }
        )

        self.assertEqual(cfg["reply_mode"], "audio_only")
        self.assertTrue(cfg["auto_tts_enabled"])
        self.assertEqual(cfg["auto_tts_probability"], 1.0)
        self.assertFalse(cfg["file_fallback_enabled"])
        self.assertFalse(cfg["emotion_routing_enabled"])
        self.assertEqual(cfg["output_retention_days"], 0)
        self.assertEqual(cfg["output_max_files"], 0)

    def test_runtime_config_migrates_legacy_file_fallback(self):
        from astrbot_plugin_mimo_tts_clone.core.config import normalize_config

        cfg = normalize_config({"send_as_file_fallback": False})

        self.assertFalse(cfg["file_fallback_enabled"])
        self.assertNotIn("send_as_file_fallback", cfg)

    def test_cleanup_outputs_keeps_newest_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            plugin = self.module.MimoTTSClonePlugin(
                _Context(),
                {"output_retention_days": 0, "output_max_files": 2},
            )
            output_dir = Path(tmp) / "outputs"
            output_dir.mkdir()
            old = output_dir / "mimo_tts_old.wav"
            mid = output_dir / "mimo_tts_mid.wav"
            new = output_dir / "mimo_tts_new.wav"
            for index, path in enumerate((old, mid, new), start=1):
                path.write_bytes(b"RIFF....WAVE")
                path.touch()
                path.stat()
                os.utime(path, (index, index))

            plugin._cleanup_outputs()

            self.assertFalse(old.exists())
            self.assertTrue(mid.exists())
            self.assertTrue(new.exists())

    def test_text_to_speech_returns_first_output_path_for_generic_callers(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            plugin = self.module.MimoTTSClonePlugin(_Context(), {})

            async def fake_synthesize_text(text, **kwargs):
                self.assertEqual(text, "hello")
                self.assertEqual(kwargs["emotion"], "happy")
                self.assertEqual(kwargs["group_id"], "aiocqhttp:FriendMessage:123")
                return [Path(tmp) / "voice.wav"]

            plugin.synthesize_text = fake_synthesize_text
            result = asyncio.run(
                plugin.text_to_speech(
                    "hello",
                    emotion="happy",
                    target_umo="aiocqhttp:FriendMessage:123",
                )
            )

            self.assertEqual(result, str(Path(tmp) / "voice.wav"))

    def test_mimo_tts_speak_llm_tool_is_available_when_supported(self):
        self.assertTrue(hasattr(self.module.MimoTTSClonePlugin, "mimo_tts_speak"))

    def test_mimo_tts_speak_avoids_reserved_context_argument(self):
        params = inspect.signature(self.module.MimoTTSClonePlugin.mimo_tts_speak).parameters

        self.assertNotIn("context", params)
        self.assertIn("style", params)


if __name__ == "__main__":
    unittest.main()
