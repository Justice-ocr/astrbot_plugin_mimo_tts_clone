from pathlib import Path
import unittest


PAGES_DIR = Path(__file__).resolve().parents[1] / "pages" / "Settings"
MOJIBAKE_MARKERS = ("闂", "闁", "閻", "濮", "閸", "濞", "缂", "閺")


class PagesUITests(unittest.TestCase):
    def test_settings_page_uses_warm_studio_shell(self):
        html = (PAGES_DIR / "index.html").read_text(encoding="utf-8")
        css = (PAGES_DIR / "style.css").read_text(encoding="utf-8")

        self.assertIn("MiMo Sound Studio", html)
        self.assertIn("studio-shell", html)
        self.assertIn("studio-hero", html)
        self.assertIn("--studio-gold", css)
        self.assertIn("radial-gradient", css)
        self.assertIn("upload-fields", html)
        self.assertIn("voice-upload-actions", html)
        self.assertIn("repeat(auto-fit", css)
        self.assertIn("save-state", html)
        self.assertIn("upload-hint", html)

    def test_settings_page_loads_astrbot_bridge_before_app(self):
        html = (PAGES_DIR / "index.html").read_text(encoding="utf-8")
        bridge_script = '<script src="/api/plugin/page/bridge-sdk.js"></script>'
        app_script = '<script src="./app.js"></script>'

        self.assertIn(bridge_script, html)
        self.assertIn(app_script, html)
        self.assertLess(html.index(bridge_script), html.index(app_script))

    def test_settings_frontend_copy_is_not_mojibake(self):
        combined = "\n".join(
            (PAGES_DIR / name).read_text(encoding="utf-8")
            for name in ("index.html", "app.js")
        )

        for marker in MOJIBAKE_MARKERS:
            self.assertNotIn(marker, combined)
        self.assertIn("自动", combined)
        self.assertIn("未设置", combined)
        self.assertIn("请在 AstrBot 插件管理页中打开", combined)
        self.assertNotIn("AstrBot Pages bridge unavailable", combined)

    def test_settings_app_waits_for_late_bridge_injection(self):
        js = (PAGES_DIR / "app.js").read_text(encoding="utf-8")

        self.assertIn("async function resolveBridge", js)
        self.assertIn("waitForAstrBotBridge", js)
        self.assertNotIn("const bridge = window.AstrBotPluginPage ||", js)

    def test_settings_app_guides_user_actions(self):
        js = (PAGES_DIR / "app.js").read_text(encoding="utf-8")
        css = (PAGES_DIR / "style.css").read_text(encoding="utf-8")

        self.assertIn("function markDirty", js)
        self.assertIn("function setBusy", js)
        self.assertIn("function validateVoiceUpload", js)
        self.assertIn("function updateActionAvailability", js)
        self.assertLess(js.index("updateActionAvailability();"), js.index("await refresh();"))
        self.assertIn("lastUploadedVoiceId", js)
        self.assertIn("请填写音色名称", js)
        self.assertIn("只支持 mp3 / wav 音频", js)
        self.assertIn("aria-busy", js)
        self.assertIn("is-busy", css)
        self.assertIn("is-dirty", css)
        self.assertIn("field-hint", css)
