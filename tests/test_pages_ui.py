from pathlib import Path
import unittest


PAGES_DIR = Path(__file__).resolve().parents[1] / "pages" / "settings"
MOJIBAKE_MARKERS = ("闂", "闁", "閻", "濮", "閸", "濞", "缂", "閺")


class PagesUITests(unittest.TestCase):
    def test_settings_page_uses_firefly_inspired_studio_shell(self):
        html = (PAGES_DIR / "index.html").read_text(encoding="utf-8")
        css = (PAGES_DIR / "style.css").read_text(encoding="utf-8")

        self.assertIn("MiMo Sound Studio", html)
        self.assertIn("Firefly Inspired Voice Console", html)
        self.assertIn("studio-shell", html)
        self.assertIn("studio-hero", html)
        self.assertIn("workflow-strip", html)
        self.assertIn("top-gradient-highlight", html)
        self.assertIn("发送策略", html)
        self.assertIn("一键诊断", html)
        self.assertIn("--studio-gold", css)
        self.assertIn("--hue", css)
        self.assertIn("--primary", css)
        self.assertIn("card-rise", css)
        self.assertIn("wave-breathe", css)
        self.assertIn("backdrop-filter", css)
        self.assertIn("prefers-reduced-motion", css)
        self.assertIn("radial-gradient", css)
        self.assertIn("--amber", css)
        self.assertIn("upload-fields", html)
        self.assertIn("voice-upload-actions", html)
        self.assertIn("repeat(auto-fit", css)
        self.assertIn("save-state", html)
        self.assertIn("upload-hint", html)
        self.assertIn("readiness-list", html)
        self.assertIn("preview-hint", html)
        self.assertIn("ai-style-director-provider-select", html)
        self.assertIn("ai-style-director-debug-log", html)

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
        html = (PAGES_DIR / "index.html").read_text(encoding="utf-8")

        self.assertIn("function markDirty", js)
        self.assertIn("function setBusy", js)
        self.assertIn("function validateVoiceUpload", js)
        self.assertIn("function uploadVoiceSample", js)
        self.assertIn("function readFileAsBase64", js)
        self.assertIn("function voiceMetadataPayload", js)
        self.assertIn("function syncVoiceMetadata", js)
        self.assertIn("元数据同步失败", js)
        self.assertIn("音色已上传，但刷新列表失败", js)
        self.assertIn("upload_voice_sample_json", js)
        self.assertIn("兼容上传", js)
        self.assertIn("function updateActionAvailability", js)
        self.assertIn("function renderReadiness", js)
        self.assertIn("function previewDisabledReason", js)
        self.assertIn("function setPreviewHint", js)
        self.assertIn("readiness", js)
        self.assertIn("function testConnection", js)
        self.assertIn("test_connection", js)
        self.assertIn("reply_mode", js)
        self.assertIn("auto_tts_probability", js)
        self.assertIn("auto_tts_group_whitelist", js)
        self.assertIn("auto_tts_private_blacklist", js)
        self.assertIn("list_ai_providers", js)
        self.assertIn("function renderProviderSelect", js)
        self.assertIn("function bindProviderSelect", js)
        self.assertIn("ai_style_director_provider_id", js)
        self.assertIn("ai_style_director_debug_log", js)
        self.assertLess(js.index("updateActionAvailability();"), js.index("await refresh();"))
        self.assertIn("lastUploadedVoiceId", js)
        self.assertIn("请填写音色名称", js)
        self.assertIn("只支持 mp3 / wav 音频", js)
        self.assertIn("aria-busy", js)
        self.assertIn("is-busy", css)
        self.assertIn("is-dirty", css)
        self.assertIn("field-hint", css)
        self.assertIn("readiness-item", css)
        self.assertIn("policy-grid", html)
        self.assertIn("access-card", html)
        self.assertIn("admin-users", html)
        self.assertIn("auto-tts-group-whitelist", html)
        self.assertIn("auto-tts-private-blacklist", html)

    def test_settings_css_keeps_large_cards_stable_on_hover(self):
        css = (PAGES_DIR / "style.css").read_text(encoding="utf-8")

        self.assertNotIn(".workflow-strip article:hover,\n.studio-card:hover", css)
        self.assertIn('input:not([type="checkbox"]):not([type="radio"]):not([type="file"])', css)
        self.assertRegex(css, r"\.studio-card\s*\{[^}]*isolation:\s*isolate;")
        self.assertRegex(css, r"\.studio-card::before\s*\{[^}]*z-index:\s*0;")
        self.assertRegex(css, r"\.studio-card > \*\s*\{[^}]*z-index:\s*1;")
        self.assertRegex(css, r"\.studio-card:hover\s*\{[^}]*box-shadow:")
        self.assertRegex(css, r"\.studio-switch input\s*\{[^}]*width:\s*16px;")
        self.assertRegex(css, r"\.studio-switch input\s*\{[^}]*transition:\s*none;")
        studio_hover = css.split(".studio-card:hover", 1)[1].split("}", 1)[0]
        self.assertNotIn("transform", studio_hover)
