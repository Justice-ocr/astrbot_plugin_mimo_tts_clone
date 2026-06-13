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
