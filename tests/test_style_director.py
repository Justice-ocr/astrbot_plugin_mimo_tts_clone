import asyncio
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_mimo_tts_clone.core.style_director import (
    StyleDirectorInput,
    build_style_director_prompt,
    generate_style_directive,
    generate_style_plan,
    parse_style_director_plan,
    sanitize_style_director_output,
)


class _Response:
    completion_text = '{"style_context":"用贴近耳边的轻声、慢一点、带一点安慰感。","speech_text":"晚上好，欢迎回来。"}'


class _Provider:
    def __init__(self):
        self.calls = []

    async def text_chat(self, **kwargs):
        self.calls.append(kwargs)
        return _Response()


class _Context:
    def __init__(self):
        self.calls = []
        self.provider = _Provider()

    async def llm_generate(self, **kwargs):
        self.calls.append(kwargs)
        return _Response()

    def get_provider_by_id(self, provider_id=None):
        return self.provider if provider_id == "director-provider" else None


class StyleDirectorTests(unittest.TestCase):
    def test_sanitizes_director_output(self):
        result = sanitize_style_director_output(
            '风格指令："用轻柔、克制、带一点笑意的语气朗读。"',
            max_chars=12,
        )

        self.assertEqual(result, "用轻柔、克制、带一点笑意")

    def test_build_prompt_keeps_text_as_context_not_output(self):
        system_prompt, user_prompt = build_style_director_prompt(
            StyleDirectorInput(
                text="今晚月亮很好。",
                emotion="happy",
                voice_name="旁白",
                max_chars=80,
            )
        )

        self.assertIn("不会展示给用户", system_prompt)
        self.assertIn("待朗读文本：今晚月亮很好。", user_prompt)
        self.assertIn("请输出最终 JSON", user_prompt)

    def test_parses_json_director_plan(self):
        result = parse_style_director_plan(
            '{"style_context":"自然一点，像真人聊天。","speech_text":"嗯，晚上好。"}',
            max_chars=80,
            max_speech_chars=80,
        )

        self.assertEqual(result.style_context, "自然一点，像真人聊天。")
        self.assertEqual(result.speech_text, "嗯，晚上好。")

    def test_generate_style_plan_uses_specific_provider(self):
        context = _Context()

        result = asyncio.run(
            generate_style_plan(
                context,
                StyleDirectorInput(text="晚上好", emotion="neutral", max_chars=80),
                provider_id="director-provider",
            )
        )

        self.assertEqual(result.style_context, "用贴近耳边的轻声、慢一点、带一点安慰感。")
        self.assertEqual(result.speech_text, "晚上好，欢迎回来。")
        self.assertEqual(len(context.calls), 0)
        self.assertEqual(len(context.provider.calls), 1)

    def test_generate_style_directive_uses_llm_generate(self):
        context = _Context()

        result = asyncio.run(
            generate_style_directive(
                context,
                StyleDirectorInput(text="晚上好", emotion="neutral", max_chars=80),
            )
        )

        self.assertEqual(result, "用贴近耳边的轻声、慢一点、带一点安慰感。")
        self.assertEqual(context.calls[0]["prompt"].count("晚上好"), 1)


if __name__ == "__main__":
    unittest.main()
