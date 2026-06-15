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
    def __init__(self):
        self.infos = []
        self.warnings = []

    def info(self, *args, **kwargs):
        self.infos.append(args)

    def warning(self, *args, **kwargs):
        self.warnings.append(args)


class _Star:
    def __init__(self, context):
        self.context = context


class _StarTools:
    data_dir = ""

    @staticmethod
    def get_data_dir(_name):
        return _StarTools.data_dir


class _Provider:
    def __init__(self, owner=None, provider_id="provider-a", model="model-a"):
        self.owner = owner
        self.provider_config = {"id": provider_id, "type": "openai", "model": model}
        self.calls = []

    def meta(self):
        return types.SimpleNamespace(id=self.provider_config["id"], model=self.provider_config["model"])

    async def text_chat(self, **kwargs):
        self.calls.append(kwargs)
        if self.owner is not None and self.owner.fail_llm:
            raise RuntimeError("llm failed")
        return types.SimpleNamespace(
            completion_text='{"style_context":"用默认服务商生成的温柔语气。","speech_text":"晚上好，欢迎回来。"}'
        )


class _Context:
    def __init__(self):
        self.routes = []
        self.llm_calls = []
        self.fail_llm = False
        self.providers = [_Provider(owner=self)]
        self.provider_manager = types.SimpleNamespace(
            curr_provider_inst=self.providers[0],
            provider_insts=self.providers,
            inst_map={self.providers[0].provider_config["id"]: self.providers[0]},
            providers_config=[self.providers[0].provider_config],
        )

    def register_web_api(self, *args):
        self.routes.append(args)

    async def llm_generate(self, **kwargs):
        self.llm_calls.append(kwargs)
        if self.fail_llm:
            raise RuntimeError("llm failed")
        return types.SimpleNamespace(
            completion_text='{"style_context":"用轻柔、贴近、带一点夜晚陪伴感的语气。","speech_text":"晚上好，欢迎回来。"}'
        )

    def get_all_providers(self):
        return list(self.providers)

    def get_using_provider(self, umo=None):
        return self.provider_manager.curr_provider_inst


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
        self.module.logger = _Logger()

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

    def test_pages_lists_astrbot_ai_providers(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            plugin = self.module.MimoTTSClonePlugin(_Context(), {})

            providers = plugin._list_ai_providers()

            self.assertEqual(providers[0]["id"], "provider-a")
            self.assertIn("model-a", providers[0]["label"])

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

    def test_runtime_config_normalizes_ai_style_director_options(self):
        from astrbot_plugin_mimo_tts_clone.core.config import normalize_config

        cfg = normalize_config(
            {
                "ai_style_director_enabled": "true",
                "ai_style_director_provider_id": "  director-a  ",
                "ai_style_director_prompt": "  让语音更像真人  ",
                "ai_style_director_mode": "hybrid",
                "ai_style_director_max_chars": 12,
                "ai_style_director_optimize_text": "on",
                "ai_style_director_fallback_to_emotion": "off",
                "ai_style_director_debug_log": "off",
            }
        )

        self.assertTrue(cfg["ai_style_director_enabled"])
        self.assertEqual(cfg["ai_style_director_provider_id"], "director-a")
        self.assertEqual(cfg["ai_style_director_prompt"], "让语音更像真人")
        self.assertEqual(cfg["ai_style_director_mode"], "hybrid")
        self.assertEqual(cfg["ai_style_director_max_chars"], 20)
        self.assertTrue(cfg["ai_style_director_optimize_text"])
        self.assertFalse(cfg["ai_style_director_fallback_to_emotion"])
        self.assertFalse(cfg["ai_style_director_debug_log"])

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

    def test_ai_style_director_adds_hidden_mimo_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            ctx = _Context()
            plugin = self.module.MimoTTSClonePlugin(
                ctx,
                {
                    "ai_style_director_enabled": True,
                    "ai_style_director_mode": "hybrid",
                    "default_context": "自然清晰",
                    "emotion_contexts": {"neutral": "平稳"},
                },
            )
            voice = plugin.voice_store.add_voice(
                "旁白",
                Path(tmp) / "voice.wav",
                "温柔音色",
                "test",
                True,
                style_context="靠近一点",
            )

            result, speech_text = asyncio.run(
                plugin._build_tts_context(
                    voice,
                    "neutral",
                    "",
                    text="晚上好，欢迎回来。",
                    style_director_enabled=True,
                )
            )

            self.assertIn("自然清晰", result)
            self.assertIn("平稳", result)
            self.assertIn("靠近一点", result)
            self.assertIn("默认服务商生成", result)
            self.assertEqual(speech_text, "晚上好，欢迎回来。")
            self.assertEqual(len(ctx.llm_calls), 0)
            self.assertEqual(len(ctx.providers[0].calls), 1)
            self.assertIn("待朗读文本：晚上好，欢迎回来。", ctx.providers[0].calls[0]["prompt"])
            self.assertIn("请输出最终 JSON", ctx.providers[0].calls[0]["prompt"])
            self.assertEqual(len(plugin.logger.infos), 1)
            log_args = plugin.logger.infos[0]
            log_text = log_args[0] % log_args[1:]
            self.assertIn("[mimo-tts] AI导演", log_text)
            self.assertIn("cached=false", log_text)
            self.assertIn("style_context=", log_text)
            self.assertIn("speech_text=", log_text)

    def test_ai_style_director_can_fail_hard_when_fallback_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            ctx = _Context()
            ctx.fail_llm = True
            plugin = self.module.MimoTTSClonePlugin(
                ctx,
                {
                    "ai_style_director_enabled": True,
                    "ai_style_director_fallback_to_emotion": False,
                },
            )
            voice = plugin.voice_store.add_voice(
                "旁白",
                Path(tmp) / "voice.wav",
                "温柔音色",
                "test",
                True,
            )

            with self.assertRaises(RuntimeError):
                asyncio.run(
                    plugin._build_tts_context(
                        voice,
                        "neutral",
                        "",
                        text="晚上好",
                        style_director_enabled=True,
                    )
                )


if __name__ == "__main__":
    unittest.main()
