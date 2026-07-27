import unittest

from astrbot_plugin_mimo_tts_clone.core.synthesis_context import (
    build_style_director_cache_key,
    clip_log_text,
    merge_directed_context,
)


class SynthesisContextTests(unittest.TestCase):
    def test_cache_key_uses_stable_short_text_prefix(self):
        key = build_style_director_cache_key(
            voice_id="voice-a",
            emotion="happy",
            text="x" * 200,
            optimize_text=True,
        )

        self.assertEqual(len(key), 64)
        self.assertNotEqual(
            key,
            build_style_director_cache_key(
                voice_id="voice-a",
                emotion="happy",
                text="x" * 128 + "different suffix",
                optimize_text=True,
            ),
        )

    def test_cache_key_includes_director_context(self):
        base = dict(
            voice_id="voice-a",
            emotion="neutral",
            text="same text",
            optimize_text=True,
        )

        first = build_style_director_cache_key(**base, command_context="soft")
        second = build_style_director_cache_key(**base, command_context="urgent")

        self.assertNotEqual(first, second)

    def test_merge_directed_context_supports_direct_and_hybrid_modes(self):
        self.assertEqual(merge_directed_context("base", "director", "direct"), "director")
        self.assertEqual(merge_directed_context("base", "", "direct"), "base")
        self.assertEqual(merge_directed_context("base", "director", "hybrid"), "base\ndirector")

    def test_clip_log_text_flattens_and_limits_output(self):
        self.assertEqual(clip_log_text("hello\nworld", 20), "hello world")
        self.assertEqual(clip_log_text("x" * 8, 5), "xxxxx...")


if __name__ == "__main__":
    unittest.main()
