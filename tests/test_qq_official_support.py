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

FAKE_GROUP_OPENID = "FAKE-GROUP-OPENID-001"
FAKE_USER_OPENID = "FAKE-USER-OPENID-001"
FAKE_ONEBOT_USER_ID = "12345"


class QQOfficialSupportTests(unittest.IsolatedAsyncioTestCase):
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

    @staticmethod
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

    @staticmethod
    def qq_platform(*, proactive: bool = True):
        return types.SimpleNamespace(
            meta=lambda: types.SimpleNamespace(
                id="qq_official", name="qq_official", support_proactive_message=proactive
            )
        )

    def test_chat_scope_recognizes_qq_official_umos(self):
        scope, ident = self.module.MimoTTSClonePlugin._chat_scope(
            self.make_event(f"qq_official:GroupMessage:{FAKE_GROUP_OPENID}")
        )
        self.assertEqual(scope, "group")
        self.assertEqual(ident, f"qq_official:GroupMessage:{FAKE_GROUP_OPENID}")

        scope, ident = self.module.MimoTTSClonePlugin._chat_scope(
            self.make_event(f"qq_official:FriendMessage:{FAKE_USER_OPENID}")
        )
        self.assertEqual(scope, "private")
        self.assertEqual(ident, f"qq_official:FriendMessage:{FAKE_USER_OPENID}")

    def test_scope_allowed_matches_qq_official_umos(self):
        plugin = self.plugin({
            "auto_tts_group_whitelist": [
                f"qq_official:GroupMessage:{FAKE_GROUP_OPENID}",
                "qq_official:GroupMessage:FAKE-GROUP-OPENID-002",
            ],
            "auto_tts_group_blacklist": [
                f"qq_official:GroupMessage:FAKE-GROUP-OPENID-003",
            ],
            "auto_tts_private_whitelist": [f"qq_official:FriendMessage:{FAKE_USER_OPENID}"],
            "auto_tts_private_blacklist": [
                "qq_official:FriendMessage:FAKE-USER-OPENID-003",
            ],
        })

        self.assertTrue(
            plugin._scope_allowed(self.make_event(f"qq_official:GroupMessage:{FAKE_GROUP_OPENID}"))
        )
        self.assertTrue(
            plugin._scope_allowed(self.make_event("qq_official:GroupMessage:FAKE-GROUP-OPENID-002"))
        )
        self.assertFalse(
            plugin._scope_allowed(self.make_event("qq_official:GroupMessage:FAKE-GROUP-OPENID-003"))
        )
        self.assertFalse(
            plugin._scope_allowed(self.make_event("qq_official:GroupMessage:FAKE-GROUP-OPENID-004"))
        )
        self.assertTrue(
            plugin._scope_allowed(self.make_event(f"qq_official:FriendMessage:{FAKE_USER_OPENID}"))
        )
        self.assertFalse(
            plugin._scope_allowed(self.make_event("qq_official:FriendMessage:FAKE-USER-OPENID-003"))
        )

    def test_preflight_session_allows_qq_official_with_proactive(self):
        context = _Context()
        context.platform_manager = types.SimpleNamespace(
            platform_insts=[self.qq_platform()]
        )
        plugin = self.plugin(context=context)

        plugin._preflight_session(f"qq_official:GroupMessage:{FAKE_GROUP_OPENID}")

        context.platform_manager.platform_insts = [self.qq_platform(proactive=False)]
        with self.assertRaisesRegex(RuntimeError, "不支持主动消息"):
            plugin._preflight_session(f"qq_official:GroupMessage:{FAKE_GROUP_OPENID}")

    async def test_shared_path_rejects_qq_official_session(self):
        context = _Context()
        context.platform_manager = types.SimpleNamespace(
            platform_insts=[self.qq_platform()]
        )
        plugin = self.plugin({"audio_transport": "shared_path"}, context)

        with self.assertRaisesRegex(RuntimeError, "shared_path 模式仅支持 aiocqhttp"):
            await plugin._send_shared_path_audio(
                source=r"C:\Users\Public\mimo_tts_audio\fake.wav",
                session=f"qq_official:GroupMessage:{FAKE_GROUP_OPENID}",
            )
        await plugin.terminate()

    async def test_shared_path_rejects_qq_official_event(self):
        context = _Context()
        context.platform_manager = types.SimpleNamespace(
            platform_insts=[self.qq_platform()]
        )
        plugin = self.plugin({"audio_transport": "shared_path"}, context)
        event = self.make_event(f"qq_official:FriendMessage:{FAKE_USER_OPENID}")

        with self.assertRaisesRegex(RuntimeError, "shared_path 模式仅支持 aiocqhttp"):
            await plugin._send_shared_path_audio(
                source=r"C:\Users\Public\mimo_tts_audio\fake.wav",
                event=event,
            )
        await plugin.terminate()

    async def test_audio_component_rejects_qq_official_shared_path(self):
        context = _Context()
        context.platform_manager = types.SimpleNamespace(
            platform_insts=[self.qq_platform()]
        )
        plugin = self.plugin({"audio_transport": "shared_path"}, context)

        with self.assertRaisesRegex(RuntimeError, "shared_path 模式仅支持 aiocqhttp"):
            plugin._audio_component(
                Path(self.temp_dir.name) / "audio.wav",
                f"qq_official:GroupMessage:{FAKE_GROUP_OPENID}",
            )
        await plugin.terminate()

    async def test_shared_path_still_allows_aiocqhttp_custom_id(self):
        class FakeBot:
            def __init__(self):
                self.calls = []

            async def send_private_msg(self, **kwargs):
                self.calls.append(kwargs)

        class FakePlatform:
            def __init__(self, bot):
                self.bot = bot

            def meta(self):
                return types.SimpleNamespace(
                    id="知更鸟", name="aiocqhttp", support_proactive_message=True
                )

            def get_client(self):
                return self.bot

        context = _Context()
        bot = FakeBot()
        platform = FakePlatform(bot)
        context.platform_manager = types.SimpleNamespace(
            platform_insts=[platform],
            get_insts=lambda: [platform],
        )
        plugin = self.plugin({"audio_transport": "shared_path"}, context)

        await plugin._send_shared_path_audio(
            source=r"C:\Users\Public\mimo_tts_audio\fake.wav",
            session=f"知更鸟:FriendMessage:{FAKE_ONEBOT_USER_ID}",
        )

        self.assertEqual(len(bot.calls), 1)
        self.assertEqual(bot.calls[0]["user_id"], int(FAKE_ONEBOT_USER_ID))
        self.assertEqual(bot.calls[0]["message"][0]["type"], "record")
        await plugin.terminate()

    async def test_base64_record_failure_message_is_platform_agnostic(self):
        plugin = self.plugin({"audio_transport": "base64"})
        audio = Path(self.temp_dir.name) / "audio.wav"
        audio.write_bytes(b"fake-audio")

        async def failing_send(_chain):
            raise RuntimeError("boom")

        event = types.SimpleNamespace(
            chain_result=lambda chain: chain,
            send=failing_send,
        )

        with self.assertRaisesRegex(RuntimeError, "Base64 语音未能发送"):
            await plugin._send_audio_result(event, audio)
        await plugin.terminate()


if __name__ == "__main__":
    unittest.main()
