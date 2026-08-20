from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_mimo_tts_clone.core.config import migrate_config, normalize_config


class ConfigV060Tests(unittest.TestCase):
    def test_v05_config_migrates_without_changing_existing_behavior(self):
        migrated = normalize_config({
            "api_key": "secret",
            "reply_mode": "audio_only",
            "delivery_mode": "blocking",
            "auto_tts_enabled": True,
        })

        self.assertEqual(migrated["config_version"], 3)
        self.assertEqual(migrated["api_key"], "secret")
        self.assertEqual(migrated["reply_mode"], "audio_only")
        self.assertEqual(migrated["delivery_mode"], "blocking")
        self.assertTrue(migrated["auto_tts_enabled"])
        self.assertTrue(migrated["job_persistence_enabled"])
        self.assertEqual(migrated["background_audio_cleanup"], "after_delivery")

    def test_migration_is_idempotent(self):
        once = migrate_config({"config_version": 1, "tts_rate_limit_rpm": 30})
        self.assertEqual(migrate_config(once), once)


if __name__ == "__main__":
    unittest.main()
