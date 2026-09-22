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
import wave


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
            if getattr(self.owner, "fail_llm_empty", False):
                raise TimeoutError()
            raise RuntimeError("llm failed")
        return types.SimpleNamespace(
            completion_text='{"style_context":"用默认服务商生成的温柔语气。","speech_text":"晚上好，欢迎回来。"}'
        )


class _Context:
    def __init__(self):
        self.routes = []
        self.llm_calls = []
        self.fail_llm = False
        self.fail_llm_empty = False
        self.providers = [_Provider(owner=self)]
        self.sent_messages = []
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

    async def send_message(self, session, chain):
        self.sent_messages.append((session, chain))
        return True


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
    event.MessageChain = type(
        "MessageChain",
        (),
        {"__init__": lambda self, chain=None, **_kwargs: setattr(self, "chain", chain or [])},
    )
    event.filter = types.SimpleNamespace(
        command=_command_decorator,
        llm_tool=_command_decorator,
        on_decorating_result=_command_decorator,
        after_message_sent=_command_decorator,
        on_platform_loaded=_command_decorator,
    )

    message_components = types.ModuleType("astrbot.api.message_components")
    class _Component:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    message_components.File = type("File", (_Component,), {})
    message_components.Plain = type("Plain", (), {})
    message_components.Record = type("Record", (_Component,), {})

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

    def test_blank_api_key_update_preserves_existing_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            plugin = self.module.MimoTTSClonePlugin(_Context(), {"api_key": "mimo-secret"})

            plugin._update_runtime_config({"api_key": "   ", "delivery_mode": "blocking"})

            self.assertEqual(plugin.config["api_key"], "mimo-secret")
            self.assertEqual(plugin.config["delivery_mode"], "blocking")

    def test_voice_audio_delete_is_confined_to_reference_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            plugin = self.module.MimoTTSClonePlugin(_Context(), {})
            outside = Path(tmp) / "outside.wav"
            outside.write_bytes(b"keep")
            inside = Path(tmp) / "voice_refs" / "inside.wav"
            inside.parent.mkdir()
            inside.write_bytes(b"delete")

            self.assertFalse(plugin._delete_voice_audio_file(outside))
            self.assertTrue(outside.exists())
            self.assertTrue(plugin._delete_voice_audio_file(inside))
            self.assertFalse(inside.exists())

    def test_auto_tts_keeps_result_in_pipeline_and_starts_after_text_sent(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                _StarTools.data_dir = tmp
                context = _Context()
                timeline = []

                async def active_send(session, chain):
                    timeline.append(("audio", asyncio.get_running_loop().time(), session, chain))
                    return True

                context.send_message = active_send
                plugin = self.module.MimoTTSClonePlugin(
                    context,
                    {
                        "auto_tts_enabled": True,
                        "auto_tts_probability": 1.0,
                        "reply_mode": "text_and_audio",
                        "delivery_mode": "background",
                        "audio_transport": "path",
                    },
                )
                output = Path(tmp) / "slow.wav"

                async def slow_synthesis(text, **_kwargs):
                    self.assertEqual(text, "这是原始文字。")
                    timeline.append(("tts-start", asyncio.get_running_loop().time()))
                    await asyncio.sleep(0.01)
                    output.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
                    timeline.append(("tts-end", asyncio.get_running_loop().time()))
                    return output

                plugin.synthesize_text = slow_synthesis
                plain = self.module.Plain()
                plain.text = "这是原始文字。"
                result = types.SimpleNamespace(
                    chain=[plain],
                    get_plain_text=lambda: " ".join(comp.text for comp in result.chain),
                    is_llm_result=lambda: True,
                )

                class Event:
                    unified_msg_origin = "aiocqhttp:FriendMessage:user-1"

                    def __init__(self):
                        self.cleared = False
                        self.extras = {}

                    def get_sender_id(self):
                        return "user-1"

                    def get_extra(self, key, default=None):
                        if key == "provider_request":
                            return types.SimpleNamespace(
                                conversation=types.SimpleNamespace(cid=self.unified_msg_origin)
                            )
                        return self.extras.get(key, default)

                    def set_extra(self, key, value):
                        self.extras[key] = value

                    def get_result(self):
                        return None if self.cleared else result

                    def clear_result(self):
                        self.cleared = True

                event = Event()
                started = asyncio.get_running_loop().time()
                await plugin.auto_tts_reply(event)
                returned = asyncio.get_running_loop().time()

                self.assertLess(returned - started, 0.5)
                self.assertFalse(event.cleared)
                self.assertIs(event.get_result(), result)
                self.assertEqual(timeline, [])

                # Simulate another decorating-result plugin and AstrBot's segmented send.
                result.chain[0].text = "这是后续插件改写后的第一段。"
                second = self.module.Plain()
                second.text = "第二段。"
                result.chain.append(second)
                timeline.append(("text-1", asyncio.get_running_loop().time()))
                timeline.append(("text-2", asyncio.get_running_loop().time()))
                await plugin.submit_tts_after_text_sent(event)

                await asyncio.wait_for(plugin._job_manager._idle.wait(), timeout=1.0)
                self.assertEqual(
                    [item[0] for item in timeline],
                    ["text-1", "text-2", "tts-start", "tts-end", "audio"],
                )
                self.assertEqual(timeline[-1][2], event.unified_msg_origin)
                self.assertEqual(plugin._queue_snapshot()["completed_jobs"], 1)

                await plugin.submit_tts_after_text_sent(event)
                self.assertEqual(plugin._queue_snapshot()["completed_jobs"], 1)

                manager = plugin._job_manager
                await plugin.terminate()
                self.assertIsNone(plugin._job_manager)
                self.assertEqual(manager._workers, [])

        asyncio.run(scenario())

    def test_background_tts_command_uses_normal_result_pipeline(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                _StarTools.data_dir = tmp
                plugin = self.module.MimoTTSClonePlugin(
                    _Context(),
                    {
                        "reply_mode": "text_and_audio",
                        "delivery_mode": "background",
                    },
                )
                submitted = []

                async def submit(**kwargs):
                    submitted.append(kwargs)
                    return True

                plugin._submit_background_job = submit

                class Event:
                    message_str = "/tts hello world"
                    unified_msg_origin = "aiocqhttp:FriendMessage:user-1"

                    def __init__(self):
                        self.extras = {}
                        self.result = None

                    def get_sender_id(self):
                        return "user-1"

                    def get_extra(self, key, default=None):
                        return self.extras.get(key, default)

                    def set_extra(self, key, value):
                        self.extras[key] = value

                    def get_result(self):
                        return self.result

                    def plain_result(self, text):
                        plain = self_module.Plain()
                        plain.text = text
                        return types.SimpleNamespace(
                            chain=[plain],
                            get_plain_text=lambda: plain.text,
                        )

                self_module = self.module
                event = Event()
                command = plugin.tts_command(event)
                event.result = await anext(command)

                self.assertEqual(event.result.get_plain_text(), "hello world")
                self.assertEqual(submitted, [])

                event.result.chain[0].text = "HELLO WORLD"
                await plugin.submit_tts_after_text_sent(event)
                self.assertEqual(len(submitted), 1)
                self.assertEqual(submitted[0]["text"], "hello world")
                self.assertEqual(submitted[0]["source"], "command")

                with self.assertRaises(StopAsyncIteration):
                    await anext(command)
                await plugin.terminate()

        asyncio.run(scenario())

    def test_plugin_reuses_client_and_closes_it_on_terminate(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                _StarTools.data_dir = tmp
                plugin = self.module.MimoTTSClonePlugin(_Context(), {"api_key": "token"})
                client = plugin._client()
                self.assertIs(plugin._client(), client)
                closed = []

                async def close():
                    closed.append(True)

                client.close = close
                await plugin.terminate()
                self.assertEqual(closed, [True])
                self.assertIsNone(plugin._mimo_client)

        asyncio.run(scenario())

    def test_pages_lists_astrbot_ai_providers(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            plugin = self.module.MimoTTSClonePlugin(_Context(), {})

            providers = plugin._list_ai_providers()

            self.assertEqual(providers[0]["id"], "provider-a")
            self.assertIn("model-a", providers[0]["label"])

    def test_pages_payload_reports_operator_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            plugin = self.module.MimoTTSClonePlugin(
                _Context(),
                {"api_key": "mimo-secret", "ai_style_director_enabled": True},
            )
            plugin.voice_store.add_voice("旁白", Path(tmp) / "voice.wav", "", "", True)

            payload = plugin._pages_payload()

            self.assertTrue(payload["readiness"]["api_key"])
            self.assertTrue(payload["readiness"]["voices"])
            self.assertTrue(payload["readiness"]["ai_director"])
            self.assertEqual(payload["readiness"]["providers"], 1)

    def test_v060_pages_task_routes_are_registered(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            context = _Context()
            self.module.MimoTTSClonePlugin(context, {})

            route_names = {Path(route[0]).name for route in context.routes}
            self.assertTrue(
                {"get_tts_tasks", "cancel_tts_task", "clear_tts_tasks"}.issubset(
                    route_names
                )
            )

    def test_live_pages_connection_test_is_opt_in(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                _StarTools.data_dir = tmp
                plugin = self.module.MimoTTSClonePlugin(_Context(), {})
                payload, status = await plugin._pages_test_connection()
                self.assertEqual(status, 403)
                self.assertFalse(payload["success"])
                self.assertIn("真实 MiMo 联调未启用", payload["error"])
                await plugin.terminate()

        asyncio.run(scenario())

    def test_pages_payload_never_returns_api_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            plugin = self.module.MimoTTSClonePlugin(_Context(), {"api_key": "mimo-secret"})

            payload = plugin._pages_payload()

            self.assertNotIn("api_key", payload["config"])
            self.assertTrue(payload["readiness"]["api_key"])
            self.assertEqual(payload["config"]["api_key_masked"], "********cret")

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
                "audio_transport": "bad",
                "delivery_segment_chars": 9999,
                "base64_max_mb": 999,
                "auto_tts_enabled": "true",
                "auto_tts_probability": 2,
                "file_fallback_enabled": "false",
                "output_retention_days": -1,
                "output_max_files": -5,
                "emotion_routing_enabled": "off",
            }
        )

        self.assertEqual(cfg["reply_mode"], "text_and_audio")
        self.assertEqual(cfg["delivery_mode"], "background")
        self.assertEqual(cfg["audio_transport"], "base64")
        self.assertEqual(cfg["delivery_segment_chars"], cfg["max_text_chars"])
        self.assertEqual(cfg["base64_max_mb"], 40)
        self.assertTrue(cfg["auto_tts_enabled"])
        self.assertEqual(cfg["auto_tts_probability"], 1.0)
        self.assertFalse(cfg["file_fallback_enabled"])
        self.assertFalse(cfg["emotion_routing_enabled"])
        self.assertEqual(cfg["output_retention_days"], 0)
        self.assertEqual(cfg["output_max_files"], 0)

    def test_runtime_config_normalizes_auto_tts_scope_lists(self):
        from astrbot_plugin_mimo_tts_clone.core.config import normalize_config

        cfg = normalize_config(
            {
                "auto_tts_group_whitelist": "123, 456\n123",
                "auto_tts_group_blacklist": ["789", " 789 ", ""],
                "auto_tts_private_whitelist": "alice，bob",
                "auto_tts_private_blacklist": None,
            }
        )

        self.assertEqual(cfg["auto_tts_group_whitelist"], ["123", "456"])
        self.assertEqual(cfg["auto_tts_group_blacklist"], ["789"])
        self.assertEqual(cfg["auto_tts_private_whitelist"], ["alice", "bob"])
        self.assertEqual(cfg["auto_tts_private_blacklist"], [])
        self.assertEqual(cfg["admin_users"], [])

    def test_runtime_config_normalizes_admin_users(self):
        from astrbot_plugin_mimo_tts_clone.core.config import normalize_config

        cfg = normalize_config({"admin_users": "admin-1, admin-2\nadmin-1"})

        self.assertEqual(cfg["admin_users"], ["admin-1", "admin-2"])

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

    def test_cleanup_outputs_never_touches_in_progress_parts(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            plugin = self.module.MimoTTSClonePlugin(
                _Context(),
                {"output_retention_days": 0, "output_max_files": 1},
            )
            output_dir = Path(tmp) / "outputs"
            output_dir.mkdir()
            (output_dir / "mimo_tts_1.wav").write_bytes(b"final")
            part_wav = output_dir / "mimo_tts_2.part000.wav"
            sdk_part = output_dir / "mimo_tts_2.part000.wav.part"
            part_wav.write_bytes(b"in progress")
            sdk_part.write_bytes(b"in progress")

            plugin._cleanup_outputs()

            self.assertTrue(part_wav.exists())
            self.assertTrue(sdk_part.exists())

    def test_cleanup_outputs_preserves_wav_waiting_for_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            plugin = self.module.MimoTTSClonePlugin(
                _Context(), {"output_retention_days": 0, "output_max_files": 1}
            )
            output_dir = Path(tmp) / "outputs"
            output_dir.mkdir()
            protected = output_dir / "mimo_tts_1.wav"
            newer = output_dir / "mimo_tts_2.wav"
            protected.write_bytes(b"protected")
            newer.write_bytes(b"newer")
            plugin._job_manager = types.SimpleNamespace(
                protected_output_paths=lambda: {protected.resolve()}
            )

            plugin._cleanup_outputs()

            self.assertTrue(protected.exists())
            self.assertTrue(newer.exists())

    def test_text_to_speech_returns_complete_output_path_for_generic_callers(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            plugin = self.module.MimoTTSClonePlugin(_Context(), {})

            async def fake_synthesize_text(text, **kwargs):
                self.assertEqual(text, "hello")
                self.assertEqual(kwargs["emotion"], "happy")
                self.assertEqual(kwargs["group_id"], "aiocqhttp:FriendMessage:123")
                return Path(tmp) / "voice.wav"

            plugin.synthesize_text = fake_synthesize_text
            result = asyncio.run(
                plugin.text_to_speech(
                    "hello",
                    emotion="happy",
                    target_umo="aiocqhttp:FriendMessage:123",
                )
            )

            self.assertEqual(result, str(Path(tmp) / "voice.wav"))

    def test_segmented_synthesis_returns_one_merged_wav_and_cleans_parts(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            plugin = self.module.MimoTTSClonePlugin(
                _Context(),
                {"max_text_chars": 5, "segment_threshold_chars": 5, "segment_max_segments": 4},
            )
            voice_path = Path(tmp) / "voice.wav"
            with wave.open(str(voice_path), "wb") as target:
                target.setnchannels(1)
                target.setsampwidth(2)
                target.setframerate(8000)
                target.writeframes(b"\0\0" * 2)
            plugin.voice_store.add_voice("test", voice_path, "", "test", True)
            plugin._voice_data_url = lambda _voice: asyncio.sleep(0, result="data:audio/wav;base64,AAAA")

            async def fake_part(_text, _voice, **kwargs):
                output = kwargs["output_path"]
                output.parent.mkdir(parents=True, exist_ok=True)
                with wave.open(str(output), "wb") as target:
                    target.setnchannels(1)
                    target.setsampwidth(2)
                    target.setframerate(8000)
                    target.writeframes(b"\1\0" * 3)
                return output

            plugin._synthesize_text_to_file = fake_part
            output = asyncio.run(plugin.synthesize_text("1234567890"))

            self.assertIsInstance(output, Path)
            with wave.open(str(output), "rb") as merged:
                self.assertEqual(merged.getnframes(), 6)
            self.assertEqual(list((Path(tmp) / "outputs").glob("*.part*")), [])

    def test_segment_failure_removes_partial_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            plugin = self.module.MimoTTSClonePlugin(
                _Context(),
                {"max_text_chars": 5, "segment_threshold_chars": 5, "segment_max_segments": 4},
            )
            voice_path = Path(tmp) / "voice.wav"
            voice_path.write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
            plugin.voice_store.add_voice("test", voice_path, "", "test", True)
            plugin._voice_data_url = lambda _voice: asyncio.sleep(0, result="data:audio/wav;base64,AAAA")
            calls = 0

            async def fake_part(_text, _voice, **kwargs):
                nonlocal calls
                calls += 1
                output = kwargs["output_path"]
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"partial")
                if calls == 2:
                    raise RuntimeError("second segment failed")
                return output

            plugin._synthesize_text_to_file = fake_part
            with self.assertRaisesRegex(RuntimeError, "second segment failed"):
                asyncio.run(plugin.synthesize_text("1234567890"))
            self.assertEqual(list((Path(tmp) / "outputs").glob("*")), [])

    def test_auto_tts_scope_whitelist_and_blacklist(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            plugin = self.module.MimoTTSClonePlugin(
                _Context(),
                {
                    "auto_tts_group_whitelist": ["group-a", "aiocqhttp:GroupMessage:group-b"],
                    "auto_tts_group_blacklist": ["group-x"],
                    "auto_tts_private_whitelist": ["user-a"],
                    "auto_tts_private_blacklist": ["user-x"],
                },
            )

            def make_event(origin: str, sender: str = "sender"):
                return types.SimpleNamespace(
                    unified_msg_origin=origin,
                    get_extra=lambda key: types.SimpleNamespace(
                        conversation=types.SimpleNamespace(cid=origin)
                    )
                    if key == "provider_request"
                    else None,
                    get_sender_id=lambda: sender,
                )

            self.assertTrue(plugin._scope_allowed(make_event("aiocqhttp:GroupMessage:group-b")))
            self.assertFalse(plugin._scope_allowed(make_event("aiocqhttp:GroupMessage:group-x")))
            self.assertFalse(plugin._scope_allowed(make_event("aiocqhttp:GroupMessage:group-z")))
            self.assertTrue(plugin._scope_allowed(make_event("aiocqhttp:FriendMessage:user-a")))
            self.assertFalse(plugin._scope_allowed(make_event("aiocqhttp:FriendMessage:user-x")))
            self.assertFalse(plugin._scope_allowed(make_event("aiocqhttp:FriendMessage:user-z")))

    def test_auto_tts_scope_allows_admins_even_when_blacklisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            plugin = self.module.MimoTTSClonePlugin(
                _Context(),
                {
                    "admin_users": ["admin-1"],
                    "auto_tts_group_blacklist": ["group-x"],
                    "auto_tts_private_blacklist": ["user-x"],
                },
            )

            def make_event(origin: str, sender: str):
                return types.SimpleNamespace(
                    unified_msg_origin=origin,
                    get_extra=lambda key: types.SimpleNamespace(
                        conversation=types.SimpleNamespace(cid=origin)
                    )
                    if key == "provider_request"
                    else None,
                    get_sender_id=lambda: sender,
                )

            self.assertTrue(
                plugin._scope_allowed(
                    make_event("aiocqhttp:GroupMessage:group-x", "admin-1")
                )
            )
            self.assertTrue(
                plugin._scope_allowed(
                    make_event("aiocqhttp:FriendMessage:user-x", "admin-1")
                )
            )
            self.assertFalse(
                plugin._scope_allowed(
                    make_event("aiocqhttp:GroupMessage:group-x", "normal-user")
                )
            )

    def test_auto_tts_access_decision_explains_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            plugin = self.module.MimoTTSClonePlugin(
                _Context(),
                {
                    "admin_users": ["admin-1"],
                    "auto_tts_group_whitelist": ["group-ok"],
                    "auto_tts_group_blacklist": ["group-block"],
                },
            )

            def make_event(origin: str, sender: str = "normal-user"):
                return types.SimpleNamespace(
                    unified_msg_origin=origin,
                    get_extra=lambda key: types.SimpleNamespace(
                        conversation=types.SimpleNamespace(cid=origin)
                    )
                    if key == "provider_request"
                    else None,
                    get_sender_id=lambda: sender,
                )

            admin = plugin._auto_tts_access_decision(
                make_event("aiocqhttp:GroupMessage:group-block", "admin-1")
            )
            blacklisted = plugin._auto_tts_access_decision(
                make_event("aiocqhttp:GroupMessage:group-block")
            )
            whitelist_miss = plugin._auto_tts_access_decision(
                make_event("aiocqhttp:GroupMessage:group-miss")
            )
            unrestricted_private = plugin._auto_tts_access_decision(
                make_event("aiocqhttp:FriendMessage:user-free")
            )

            self.assertTrue(admin["allowed"])
            self.assertIn("admin bypass", admin["reason"])
            self.assertFalse(blacklisted["allowed"])
            self.assertIn("blacklist matched", blacklisted["reason"])
            self.assertFalse(whitelist_miss["allowed"])
            self.assertIn("whitelist missed", whitelist_miss["reason"])
            self.assertTrue(unrestricted_private["allowed"])
            self.assertIn("unrestricted", unrestricted_private["reason"])

    def test_pages_payload_includes_access_control_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            plugin = self.module.MimoTTSClonePlugin(
                _Context(),
                {
                    "admin_users": ["admin-1"],
                    "auto_tts_group_whitelist": ["group-ok"],
                    "auto_tts_group_blacklist": ["group-block"],
                },
            )

            payload = plugin._pages_payload()

            self.assertIn("access_control", payload)
            preview = payload["access_control"]
            self.assertEqual(preview["admins"]["count"], 1)
            self.assertEqual(preview["group"]["whitelist_count"], 1)
            self.assertEqual(preview["group"]["blacklist_count"], 1)
            self.assertIn("黑名单", preview["summary"])

    def test_mimo_tts_speak_llm_tool_is_available_when_supported(self):
        self.assertTrue(hasattr(self.module.MimoTTSClonePlugin, "mimo_tts_speak"))

    def test_mimo_tts_speak_avoids_reserved_context_argument(self):
        params = inspect.signature(self.module.MimoTTSClonePlugin.mimo_tts_speak).parameters

        self.assertNotIn("context", params)
        self.assertIn("style", params)

    @staticmethod
    def _make_tool_event():
        return types.SimpleNamespace(
            unified_msg_origin="FAKE-PLATFORM:GroupMessage:FAKE-GROUP-001",
            get_sender_id=lambda: "FAKE-SENDER-001",
            get_extra=lambda key: types.SimpleNamespace(
                conversation=types.SimpleNamespace(cid="FAKE-CID-001")
            )
            if key == "provider_request"
            else None,
        )

    @staticmethod
    def _run_speak(plugin, text):
        event = ConfigPersistenceTests._make_tool_event()

        async def collect():
            try:
                return [
                    chunk
                    async for chunk in plugin.mimo_tts_speak(event, text=text)
                ]
            finally:
                await plugin.terminate()

        return asyncio.run(collect())

    def test_mimo_tts_speak_success_result_hides_audio_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            plugin = self.module.MimoTTSClonePlugin(_Context(), {})
            fake_output = Path(tmp) / "mimo_tts_fake.wav"

            async def fake_synthesize(*_args, **_kwargs):
                return fake_output

            async def fake_send(_event, _output):
                return None

            plugin.synthesize_text = fake_synthesize
            plugin._send_audio_result = fake_send

            chunks = self._run_speak(plugin, "FAKE-TTS-TEXT-001")

            self.assertEqual(len(chunks), 1)
            self.assertNotIn(".wav", chunks[0])
            self.assertNotIn(str(fake_output), chunks[0])
            self.assertNotIn(str(tmp), chunks[0])

    def test_mimo_tts_speak_success_confirms_delivery_and_sends_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            plugin = self.module.MimoTTSClonePlugin(_Context(), {})
            sent = []

            async def fake_synthesize(*_args, **_kwargs):
                return Path(tmp) / "mimo_tts_fake.wav"

            async def fake_send(_event, output):
                sent.append(output)

            plugin.synthesize_text = fake_synthesize
            plugin._send_audio_result = fake_send

            chunks = self._run_speak(plugin, "FAKE-TTS-TEXT-002")

            self.assertEqual(len(sent), 1)
            self.assertEqual(chunks, ["已用你的声音把这句话说给用户，语音已送达"])

    def test_mimo_tts_speak_empty_text_returns_fixed_notice(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            plugin = self.module.MimoTTSClonePlugin(_Context(), {})

            async def fake_synthesize(*_args, **_kwargs):
                raise AssertionError("synthesize must not run for empty text")

            plugin.synthesize_text = fake_synthesize

            chunks = self._run_speak(plugin, "   ")

            self.assertEqual(chunks, ["文本为空，没有可说的话"])

    def test_mimo_tts_speak_send_failure_leaks_no_internal_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            plugin = self.module.MimoTTSClonePlugin(_Context(), {})

            async def fake_synthesize(*_args, **_kwargs):
                return Path(tmp) / "mimo_tts_fake.wav"

            async def fake_send(_event, _output):
                raise RuntimeError("NapCat 未接受 Base64 语音，已禁止回退为本地路径。")

            plugin.synthesize_text = fake_synthesize
            plugin._send_audio_result = fake_send

            chunks = self._run_speak(plugin, "FAKE-TTS-TEXT-003")

            self.assertEqual(len(chunks), 1)
            self.assertIn("送达", chunks[0])
            lowered = chunks[0].lower()
            self.assertNotIn("napcat", lowered)
            self.assertNotIn("未接受", chunks[0])
            self.assertNotIn("tts failed", lowered)

    def test_mimo_tts_speak_synthesize_failure_leaks_no_internal_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            plugin = self.module.MimoTTSClonePlugin(_Context(), {})

            async def fake_synthesize(*_args, **_kwargs):
                raise RuntimeError(
                    "aiocqhttp OneBot send failed: /fake/outputs/mimo_tts_x.wav"
                )

            plugin.synthesize_text = fake_synthesize

            chunks = self._run_speak(plugin, "FAKE-TTS-TEXT-004")

            self.assertEqual(len(chunks), 1)
            self.assertIn("送达", chunks[0])
            lowered = chunks[0].lower()
            self.assertNotIn("aiocqhttp", lowered)
            self.assertNotIn("onebot", lowered)
            self.assertNotIn(".wav", lowered)
            self.assertNotIn("tts failed", lowered)

    def test_mimo_tts_speak_docstring_frames_voice_as_own_speech(self):
        doc = inspect.getdoc(self.module.MimoTTSClonePlugin.mimo_tts_speak) or ""
        returns_section = doc.partition("Returns:")[2]

        self.assertIn("your own speech", doc)
        self.assertNotIn("Generated audio path", doc)
        self.assertNotIn("path", returns_section.lower())
        self.assertNotIn("napcat", doc.lower())

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

            result = asyncio.run(
                plugin._build_tts_context(
                    voice,
                    "neutral",
                    "",
                    text="晚上好，欢迎回来。",
                    style_director_enabled=True,
                )
            )

            self.assertIn("自然清晰", result.context)
            self.assertIn("平稳", result.context)
            self.assertIn("靠近一点", result.context)
            self.assertIn("默认服务商生成", result.context)
            self.assertEqual(result.speech_text, "晚上好，欢迎回来。")
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
            ctx.fail_llm_empty = True
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

            with self.assertRaises(RuntimeError) as caught:
                asyncio.run(
                    plugin._build_tts_context(
                        voice,
                        "neutral",
                        "",
                        text="晚上好",
                        style_director_enabled=True,
                    )
                )
            self.assertIn("TimeoutError", str(caught.exception))

    def test_ai_style_director_failure_log_includes_context_when_fallback_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            _StarTools.data_dir = tmp
            ctx = _Context()
            ctx.fail_llm = True
            ctx.fail_llm_empty = True
            plugin = self.module.MimoTTSClonePlugin(
                ctx,
                {
                    "ai_style_director_enabled": True,
                    "ai_style_director_provider_id": "openai/deepseek-v4-flash",
                    "ai_style_director_fallback_to_emotion": True,
                    "default_context": "base",
                },
            )
            voice = plugin.voice_store.add_voice(
                "温柔女生",
                Path(tmp) / "voice.wav",
                "soft",
                "test",
                True,
            )

            result = asyncio.run(
                plugin._build_tts_context(
                    voice,
                    "neutral",
                    "",
                    text="晚上好",
                    style_director_enabled=True,
                )
            )

            self.assertIn("base", result.context)
            self.assertEqual(result.speech_text, "晚上好")
            self.assertEqual(len(plugin.logger.warnings), 1)
            warning_args = plugin.logger.warnings[0]
            warning_text = warning_args[0] % warning_args[1:]
            self.assertIn("style director failed", warning_text)
            self.assertIn("provider=openai/deepseek-v4-flash", warning_text)
            self.assertIn("voice=温柔女生", warning_text)
            self.assertIn("emotion=neutral", warning_text)
            self.assertIn("error_type=TimeoutError", warning_text)
            self.assertIn("fallback=true", warning_text)


if __name__ == "__main__":
    unittest.main()
