from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "api_key": "",
    "base_url": "https://api.xiaomimimo.com/v1",
    "model": "mimo-v2.5-tts-voiceclone",
    "output_format": "wav",
    "default_context": "",
    "max_text_chars": 500,
    "max_voice_file_mb": 10,
    "max_concurrency": 1,
    "reply_mode": "audio_only",
    "auto_tts_enabled": False,
    "auto_tts_probability": 0.0,
    "file_fallback_enabled": True,
    "output_retention_days": 7,
    "output_max_files": 100,
    "emotion_routing_enabled": True,
    "emotion_contexts": {},
    "ai_style_director_enabled": False,
    "ai_style_director_provider_id": "",
    "ai_style_director_prompt": "",
    "ai_style_director_mode": "direct",
    "ai_style_director_max_chars": 120,
    "ai_style_director_optimize_text": True,
    "ai_style_director_fallback_to_emotion": True,
    "segment_enabled": True,
    "segment_threshold_chars": 180,
    "segment_max_segments": 6,
    "admin_users": [],
}


@dataclass(slots=True)
class PluginConfig:
    api_key: str
    base_url: str
    model: str
    output_format: str
    default_context: str
    max_text_chars: int
    max_voice_file_mb: int
    max_concurrency: int
    reply_mode: str
    auto_tts_enabled: bool
    auto_tts_probability: float
    file_fallback_enabled: bool
    output_retention_days: int
    output_max_files: int
    emotion_routing_enabled: bool
    emotion_contexts: dict[str, str]
    ai_style_director_enabled: bool
    ai_style_director_provider_id: str
    ai_style_director_prompt: str
    ai_style_director_mode: str
    ai_style_director_max_chars: int
    ai_style_director_optimize_text: bool
    ai_style_director_fallback_to_emotion: bool
    segment_enabled: bool
    segment_threshold_chars: int
    segment_max_segments: int
    admin_users: list[str]

    @property
    def max_voice_file_bytes(self) -> int:
        return max(1, self.max_voice_file_mb) * 1024 * 1024


def normalize_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    raw_has_file_fallback = False
    legacy_file_fallback = DEFAULT_CONFIG["file_fallback_enabled"]
    if isinstance(raw, dict):
        raw_has_file_fallback = "file_fallback_enabled" in raw
        legacy_file_fallback = _bool_value(raw.get("send_as_file_fallback", True))
        for key in cfg:
            if key in raw:
                cfg[key] = raw[key]

    cfg["api_key"] = str(cfg.get("api_key") or "").strip()
    cfg["base_url"] = str(cfg.get("base_url") or DEFAULT_CONFIG["base_url"]).rstrip("/")
    cfg["model"] = str(cfg.get("model") or DEFAULT_CONFIG["model"]).strip()
    cfg["output_format"] = str(cfg.get("output_format") or "wav").strip().lower()
    cfg["default_context"] = str(cfg.get("default_context") or "")
    cfg["max_text_chars"] = _int_at_least(cfg.get("max_text_chars"), 500, 1)
    cfg["max_voice_file_mb"] = _int_at_least(cfg.get("max_voice_file_mb"), 10, 1)
    cfg["max_concurrency"] = _int_at_least(cfg.get("max_concurrency"), 1, 1)
    reply_mode = str(cfg.get("reply_mode") or "audio_only").strip().lower()
    if reply_mode not in {"audio_only", "text_and_audio", "text_only"}:
        reply_mode = "audio_only"
    cfg["reply_mode"] = reply_mode
    cfg["auto_tts_enabled"] = _bool_value(cfg.get("auto_tts_enabled", False))
    try:
        probability = float(cfg.get("auto_tts_probability") or 0.0)
    except (TypeError, ValueError):
        probability = 0.0
    cfg["auto_tts_probability"] = min(1.0, max(0.0, probability))
    cfg["file_fallback_enabled"] = _bool_value(
        cfg.get("file_fallback_enabled") if raw_has_file_fallback else legacy_file_fallback
    )
    cfg["output_retention_days"] = _int_at_least(cfg.get("output_retention_days"), 7, 0)
    cfg["output_max_files"] = _int_at_least(cfg.get("output_max_files"), 100, 0)
    cfg["emotion_routing_enabled"] = _bool_value(cfg.get("emotion_routing_enabled", True))
    raw_contexts = cfg.get("emotion_contexts") or {}
    if not isinstance(raw_contexts, dict):
        raw_contexts = {}
    cfg["emotion_contexts"] = {
        str(key).strip().lower(): str(value or "").strip()
        for key, value in dict(raw_contexts).items()
        if str(key).strip() and str(value or "").strip()
    }
    cfg["ai_style_director_enabled"] = _bool_value(cfg.get("ai_style_director_enabled", False))
    cfg["ai_style_director_provider_id"] = str(cfg.get("ai_style_director_provider_id") or "").strip()
    cfg["ai_style_director_prompt"] = str(cfg.get("ai_style_director_prompt") or "").strip()
    style_mode = str(cfg.get("ai_style_director_mode") or "direct").strip().lower()
    if style_mode not in {"direct", "hybrid"}:
        style_mode = "direct"
    cfg["ai_style_director_mode"] = style_mode
    cfg["ai_style_director_max_chars"] = _int_at_least(cfg.get("ai_style_director_max_chars"), 120, 20)
    cfg["ai_style_director_optimize_text"] = _bool_value(
        cfg.get("ai_style_director_optimize_text", True)
    )
    cfg["ai_style_director_fallback_to_emotion"] = _bool_value(
        cfg.get("ai_style_director_fallback_to_emotion", True)
    )
    cfg["segment_enabled"] = _bool_value(cfg.get("segment_enabled", True))
    cfg["segment_threshold_chars"] = _int_at_least(cfg.get("segment_threshold_chars"), 180, 1)
    cfg["segment_max_segments"] = _int_at_least(cfg.get("segment_max_segments"), 6, 1)
    admins = cfg.get("admin_users") or []
    cfg["admin_users"] = [str(item).strip() for item in admins if str(item).strip()]
    return cfg


def build_plugin_config(raw: dict[str, Any] | None) -> PluginConfig:
    cfg = normalize_config(raw)
    return PluginConfig(**cfg)


def _int_at_least(value: Any, default: int, minimum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


def _bool_value(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", ""}
    return bool(value)
