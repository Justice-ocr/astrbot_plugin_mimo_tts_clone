from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any


DEFAULT_STYLE_DIRECTOR_PROMPT = """你是发送前语音导演，只为 TTS 生成不会展示给用户的音频控制方案。
请根据待朗读文本、情绪和音色信息，输出严格 JSON：
{"style_context":"给 MiMo 的自然语言风格控制指令","speech_text":"只用于音频朗读的优化文本"}

要求：
1. style_context 只描述语气、节奏、情绪、停顿、亲近感、场景感，不要复述正文。
2. speech_text 可以剔除“嗯、啊、呃、那个、就是说、然后呢”等无意义口头填充，整理重复语气词。
3. speech_text 可以用逗号、顿号、省略号、句号增加自然停顿，让朗读更像真人。
4. speech_text 必须保持原意，不要新增事实、承诺、称呼、表情、动作或解释。
5. 如果不允许优化朗读文本，speech_text 输出空字符串。
6. 不要输出 Markdown、代码块或 JSON 以外的解释。
7. style_context 长度控制在 {max_chars} 字以内。"""


@dataclass(slots=True)
class StyleDirectorInput:
    text: str
    emotion: str = "neutral"
    voice_name: str = ""
    voice_description: str = ""
    voice_style_context: str = ""
    existing_context: str = ""
    max_chars: int = 120
    optimize_speech_text: bool = True
    max_speech_chars: int = 500
    text_mode: str = "optimize"


@dataclass(slots=True)
class StyleDirectorPlan:
    style_context: str = ""
    speech_text: str = ""


def build_style_director_prompt(data: StyleDirectorInput, template: str = "") -> tuple[str, str]:
    max_chars = max(20, int(data.max_chars or 120))
    system_prompt = _render_template(template or DEFAULT_STYLE_DIRECTOR_PROMPT, max_chars=max_chars)
    if data.text_mode == "tags":
        system_prompt += (
            "\n覆盖上述正文优化要求：不得修改正文任何字符或标点，只允许插入"
            "(轻声)、(停顿)、(微笑)、(叹气)、(呼吸)、(强调)这六种标签。"
        )
    elif data.text_mode in {"instructions", "off"}:
        system_prompt += "\n不得改写正文，speech_text 必须为空字符串。"
    user_prompt = "\n".join(
        part
        for part in (
            f"待朗读文本：{_clip(data.text, max(200, data.max_speech_chars))}",
            f"情绪：{data.emotion or 'neutral'}",
            f"音色名称：{data.voice_name}" if data.voice_name else "",
            f"音色说明：{data.voice_description}" if data.voice_description else "",
            f"音色已有风格：{data.voice_style_context}" if data.voice_style_context else "",
            f"已有风格指令：{data.existing_context}" if data.existing_context else "",
            f"是否允许优化朗读文本：{'是' if data.optimize_speech_text else '否'}",
            f"朗读文本最大长度：{max(20, int(data.max_speech_chars or 500))} 字",
            "请输出最终 JSON：",
        )
        if part
    )
    return system_prompt, user_prompt


def sanitize_style_director_output(value: Any, max_chars: int = 120) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"^[\s\-\d.、:：]+", "", text)
    text = text.strip().strip("\"'“”‘’`")
    text = re.sub(r"^(风格指令|风格控制|style_context|输出|建议|语气|风格)\s*[:：]\s*", "", text)
    text = text.strip().strip("\"'“”‘’`")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip("，。；、,. ")
    return text


def sanitize_speech_text(value: Any, max_chars: int = 500) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = text.strip().strip("\"'“”‘’`")
    text = re.sub(r"^(speech_text|朗读文本|音频文本)\s*[:：]\s*", "", text)
    text = re.sub(r"[ \t]+", " ", text).strip()
    limit = max(20, int(max_chars or 500))
    if len(text) > limit:
        text = text[:limit].rstrip("，。；、,. ")
    return text


def parse_style_director_plan(value: Any, *, max_chars: int = 120, max_speech_chars: int = 500) -> StyleDirectorPlan:
    text = str(value or "").strip()
    if not text:
        return StyleDirectorPlan()
    text = _strip_code_fences(text)
    payload = _extract_json_object(text)
    if isinstance(payload, dict):
        return StyleDirectorPlan(
            style_context=sanitize_style_director_output(
                payload.get("style_context") or payload.get("style") or payload.get("context"),
                max_chars,
            ),
            speech_text=sanitize_speech_text(
                payload.get("speech_text") or payload.get("text") or payload.get("assistant_text"),
                max_speech_chars,
            ),
        )
    return StyleDirectorPlan(style_context=sanitize_style_director_output(text, max_chars))


async def generate_style_plan(
    context: Any,
    data: StyleDirectorInput,
    *,
    template: str = "",
    provider_id: str = "",
) -> StyleDirectorPlan:
    system_prompt, user_prompt = build_style_director_prompt(data, template)
    response = await asyncio.wait_for(
        _call_llm(context, user_prompt, system_prompt, provider_id=provider_id),
        timeout=15,
    )
    output = getattr(response, "completion_text", response)
    plan = parse_style_director_plan(
        output,
        max_chars=data.max_chars,
        max_speech_chars=data.max_speech_chars,
    )
    plan.speech_text = protect_speech_text(data.text, plan.speech_text, data.text_mode)
    return plan


PERFORMANCE_TAGS = ("轻声", "停顿", "微笑", "叹气", "呼吸", "强调")
_PERFORMANCE_TAG_RE = re.compile(r"\((?:" + "|".join(PERFORMANCE_TAGS) + r")\)")


def protect_speech_text(original: str, candidate: str, mode: str) -> str:
    if mode in {"instructions", "off"}:
        return original
    if not candidate:
        return original
    if mode == "tags":
        # Compare exact characters; whitespace and punctuation are part of the body.
        body = _PERFORMANCE_TAG_RE.sub("", candidate)
        source = _PERFORMANCE_TAG_RE.sub("", original)
        if body != source:
            return original
    return candidate


async def generate_style_directive(
    context: Any,
    data: StyleDirectorInput,
    *,
    template: str = "",
    provider_id: str = "",
) -> str:
    plan = await generate_style_plan(context, data, template=template, provider_id=provider_id)
    return plan.style_context


async def _call_llm(context: Any, prompt: str, system_prompt: str, *, provider_id: str = "") -> Any:
    provider_id = str(provider_id or "").strip()
    if provider_id:
        provider_getter = getattr(context, "get_provider_by_id", None)
        if callable(provider_getter):
            provider = provider_getter(provider_id=provider_id)
            if provider is None:
                provider = provider_getter(provider_id)
            if provider is not None and callable(getattr(provider, "text_chat", None)):
                return await provider.text_chat(prompt=prompt, context=[], system_prompt=system_prompt)
    current_provider = _current_chat_provider(context)
    if current_provider is not None and callable(getattr(current_provider, "text_chat", None)):
        return await current_provider.text_chat(prompt=prompt, context=[], system_prompt=system_prompt)
    llm_generate = getattr(context, "llm_generate", None)
    if callable(llm_generate):
        kwargs = {
            "prompt": prompt,
            "system_prompt": system_prompt,
        }
        if provider_id:
            kwargs["chat_provider_id"] = provider_id
        return await llm_generate(**kwargs)
    raise RuntimeError("未找到可用的 AstrBot AI 服务商")


def _extract_json_object(text: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _clip(value: str, max_chars: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= max_chars else text[:max_chars].rstrip() + "..."


def _render_template(template: str, *, max_chars: int) -> str:
    try:
        return str(template or "").format(max_chars=max_chars)
    except Exception:
        return str(template or "")


def _current_chat_provider(context: Any) -> Any:
    getter = getattr(context, "get_using_provider", None)
    if callable(getter):
        try:
            provider = getter()
            if provider is not None:
                return provider
        except Exception:
            pass
        try:
            provider = getter(umo=None)
            if provider is not None:
                return provider
        except Exception:
            pass

    provider_manager = getattr(context, "provider_manager", None)
    if provider_manager is None:
        return None
    current = getattr(provider_manager, "curr_provider_inst", None)
    if current is not None:
        return current
    providers = getattr(provider_manager, "provider_insts", None)
    if providers:
        return providers[0]
    return None


def _current_chat_provider_id(context: Any) -> str:
    provider = _current_chat_provider(context)
    if provider is None:
        return ""
    meta_fn = getattr(provider, "meta", None)
    if callable(meta_fn):
        try:
            meta = meta_fn()
            return str(getattr(meta, "id", "") or "").strip()
        except Exception:
            pass
    provider_config = getattr(provider, "provider_config", None)
    if isinstance(provider_config, dict):
        return str(provider_config.get("id") or "").strip()
    try:
        return str(getattr(provider, "provider_id", "") or getattr(provider, "id", "") or "").strip()
    except Exception:
        return ""
