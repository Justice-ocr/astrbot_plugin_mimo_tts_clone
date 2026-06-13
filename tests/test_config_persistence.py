import importlib
import json
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
    event.filter = types.SimpleNamespace(command=_command_decorator)

    message_components = types.ModuleType("astrbot.api.message_components")
    message_components.File = object
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


if __name__ == "__main__":
    unittest.main()
