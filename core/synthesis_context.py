from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


StyleDirectorCacheKey = str


@dataclass(slots=True)
class TTSContextResult:
    context: str
    speech_text: str
    style_context: str = ""
    cached: bool = False


def build_style_director_cache_key(
    *,
    voice_id: str,
    emotion: str,
    text: str,
    optimize_text: bool,
    command_context: str = "",
    voice_context: str = "",
    voice_description: str = "",
    provider_id: str = "",
    prompt: str = "",
    mode: str = "",
) -> StyleDirectorCacheKey:
    payload = {
        "voice_id": str(voice_id or ""),
        "emotion": str(emotion or ""),
        "text": str(text or ""),
        "optimize_text": bool(optimize_text),
        "command_context": str(command_context or ""),
        "voice_context": str(voice_context or ""),
        "voice_description": str(voice_description or ""),
        "provider_id": str(provider_id or ""),
        "prompt": str(prompt or ""),
        "mode": str(mode or ""),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def merge_directed_context(base_context: str, directive: str, mode: str) -> str:
    if str(mode or "").strip().lower() == "direct":
        return str(directive or "").strip() or base_context
    return "\n".join(
        part for part in (str(base_context or "").strip(), str(directive or "").strip()) if part
    )


def clip_log_text(value: Any, limit: int = 160) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return text if len(text) <= limit else f"{text[:limit].rstrip()}..."
