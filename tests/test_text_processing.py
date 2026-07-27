from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_mimo_tts_clone.core.text_processing import (
    TextSegmentationError,
    clean_tts_text,
    split_tts_text,
)


class TextProcessingTests(unittest.TestCase):
    def test_clean_tts_text_removes_urls_and_code_blocks(self):
        text = "请看 https://example.com\n```python\nprint('x')\n```\n（开心）今天真棒！"

        self.assertEqual(clean_tts_text(text), "请看 （开心）今天真棒！")

    def test_clean_tts_text_keeps_mimo_style_tags(self):
        text = "[whisper] 这里低声说。 (笑) 然后正常说。"

        self.assertEqual(clean_tts_text(text), "[whisper] 这里低声说。 (笑) 然后正常说。")

    def test_split_tts_text_keeps_punctuation_and_limits_segments(self):
        parts = split_tts_text(
            "第一句很短。第二句也很短！第三句继续？第四句结束。",
            max_chars=8,
            max_segments=4,
        )

        self.assertEqual(parts, ["第一句很短。", "第二句也很短！", "第三句继续？", "第四句结束。"])

    def test_split_tts_text_never_creates_an_oversized_tail(self):
        text = "这是一个句子。" * 400

        with self.assertRaises(TextSegmentationError):
            split_tts_text(text, max_chars=180, max_segments=6)

    def test_split_tts_text_hard_splits_a_single_long_sentence(self):
        parts = split_tts_text("甲" * 25, max_chars=10, max_segments=3)

        self.assertEqual([len(part) for part in parts], [10, 10, 5])
