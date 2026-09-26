from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "config_version": 3,
    "api_key": "",
    "base_url": "https://api.xiaomimimo.com/v1",
    "model": "mimo-v2.5-tts-voiceclone",
    "output_format": "wav",
    "default_context": "",
    "max_text_chars": 2500,
    "max_voice_file_mb": 10,
    "max_concurrency": 2,
    "audio_transport": "base64",
    "shared_path_linux": "/mnt/c/Users/Public/mimo_tts_audio",
    "shared_path_windows": "C:\\Users\\Public\\mimo_tts_audio",
    "delivery_segment_chars": 500,
    "base64_max_mb": 8,
    "reply_mode": "text_and_audio",
    "delivery_mode": "background",
    "background_queue_size": 20,
    "tts_timeout_seconds": 120,
    "tts_max_retries": 2,
    "tts_rate_limit_rpm": 90,
    "tts_retry_backoff_base_seconds": 1.0,
    "tts_retry_backoff_max_seconds": 15.0,
    "circuit_failure_threshold": 5,
    "circuit_recovery_seconds": 60,
    "async_failure_notice": "command_only",
    "job_persistence_enabled": True,
    "job_recovery_max_age_hours": 24,
    "job_history_size": 100,
    "background_audio_cleanup": "after_delivery",
    "platform_preflight_enabled": True,
    "live_api_test_enabled": False,
    "auto_tts_enabled": False,
    "auto_tts_probability": 0.0,
    "auto_tts_group_whitelist": [],
    "auto_tts_group_blacklist": [],
    "auto_tts_private_whitelist": [],
    "auto_tts_private_blacklist": [],
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
    "director_text_mode": "optimize",
    "ai_style_director_fallback_to_emotion": True,
    "ai_style_director_debug_log": True,
    "segment_enabled": True,
    "segment_threshold_chars": 2500,
    "segment_max_segments": 6,
    "admin_users": [],
}


@dataclass(slots=True)
class PluginConfig:
    config_version: int
    api_key: str
    base_url: str
    model: str
    output_format: str
    default_context: str
    max_text_chars: int
    max_voice_file_mb: int
    max_concurrency: int
    audio_transport: str
    shared_path_linux: str
    shared_path_windows: str
    delivery_segment_chars: int
    base64_max_mb: int
    reply_mode: str
    delivery_mode: str
    background_queue_size: int
    tts_timeout_seconds: int
    tts_max_retries: int
    tts_rate_limit_rpm: int
    tts_retry_backoff_base_seconds: float
    tts_retry_backoff_max_seconds: float
    circuit_failure_threshold: int
    circuit_recovery_seconds: int
    async_failure_notice: str
    job_persistence_enabled: bool
    job_recovery_max_age_hours: int
    job_history_size: int
    background_audio_cleanup: str
    platform_preflight_enabled: bool
    live_api_test_enabled: bool
    auto_tts_enabled: bool
    auto_tts_probability: float
    auto_tts_group_whitelist: list[str]
    auto_tts_group_blacklist: list[str]
    auto_tts_private_whitelist: list[str]
    auto_tts_private_blacklist: list[str]
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
    director_text_mode: str
    ai_style_director_fallback_to_emotion: bool
    ai_style_director_debug_log: bool
    segment_enabled: bool
    segment_threshold_chars: int
    segment_max_segments: int
    admin_users: list[str]

    @property
    def max_voice_file_bytes(self) -> int:
        return max(1, self.max_voice_file_mb) * 1024 * 1024

    @property
    def base64_max_bytes(self) -> int:
        return max(1, self.base64_max_mb) * 1024 * 1024


def normalize_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    raw = migrate_config(raw)
    raw_has_file_fallback = False
    legacy_file_fallback = DEFAULT_CONFIG["file_fallback_enabled"]
    if isinstance(raw, dict):
        raw_has_file_fallback = "file_fallback_enabled" in raw
        legacy_file_fallback = _bool_value(raw.get("send_as_file_fallback", True))
        for key in cfg:
            if key in raw:
                cfg[key] = raw[key]

    cfg["config_version"] = 3
    cfg["api_key"] = str(cfg.get("api_key") or "").strip()
    cfg["base_url"] = str(cfg.get("base_url") or DEFAULT_CONFIG["base_url"]).rstrip("/")
    cfg["model"] = DEFAULT_CONFIG["model"]
    cfg["output_format"] = "wav"
    cfg["default_context"] = str(cfg.get("default_context") or "")
    cfg["max_text_chars"] = _int_between(cfg.get("max_text_chars"), 2500, 1, 8000)
    cfg["max_voice_file_mb"] = _int_between(cfg.get("max_voice_file_mb"), 10, 1, 10)
    cfg["max_concurrency"] = _int_between(cfg.get("max_concurrency"), 2, 1, 4)
    transport = str(cfg.get("audio_transport") or "base64").strip().lower()
    cfg["audio_transport"] = (
        transport if transport in {"base64", "path", "shared_path"} else "base64"
    )
    cfg["shared_path_linux"] = str(
        cfg.get("shared_path_linux") or DEFAULT_CONFIG["shared_path_linux"]
    ).strip().rstrip("/\\")
    cfg["shared_path_windows"] = str(
        cfg.get("shared_path_windows") or DEFAULT_CONFIG["shared_path_windows"]
    ).strip().rstrip("/\\")
    cfg["delivery_segment_chars"] = _int_between(
        cfg.get("delivery_segment_chars"), 500, 1, cfg["max_text_chars"]
    )
    cfg["base64_max_mb"] = _int_between(cfg.get("base64_max_mb"), 8, 1, 40)
    reply_mode = str(cfg.get("reply_mode") or "text_and_audio").strip().lower()
    if reply_mode not in {"audio_only", "text_and_audio", "text_only"}:
        reply_mode = "text_and_audio"
    cfg["reply_mode"] = reply_mode
    delivery_mode = str(cfg.get("delivery_mode") or "background").strip().lower()
    cfg["delivery_mode"] = delivery_mode if delivery_mode in {"blocking", "background"} else "background"
    cfg["background_queue_size"] = _int_between(cfg.get("background_queue_size"), 20, 1, 100)
    cfg["tts_timeout_seconds"] = _int_between(cfg.get("tts_timeout_seconds"), 120, 10, 300)
    cfg["tts_max_retries"] = _int_between(cfg.get("tts_max_retries"), 2, 0, 5)
    cfg["tts_rate_limit_rpm"] = _int_between(cfg.get("tts_rate_limit_rpm"), 90, 1, 100)
    cfg["tts_retry_backoff_base_seconds"] = _float_between(
        cfg.get("tts_retry_backoff_base_seconds"), 1.0, 0.1, 30.0
    )
    cfg["tts_retry_backoff_max_seconds"] = _float_between(
        cfg.get("tts_retry_backoff_max_seconds"), 15.0,
        cfg["tts_retry_backoff_base_seconds"], 120.0
    )
    cfg["circuit_failure_threshold"] = _int_between(
        cfg.get("circuit_failure_threshold"), 5, 1, 20
    )
    cfg["circuit_recovery_seconds"] = _int_between(
        cfg.get("circuit_recovery_seconds"), 60, 5, 600
    )
    failure_notice = str(cfg.get("async_failure_notice") or "command_only").strip().lower()
    cfg["async_failure_notice"] = (
        failure_notice if failure_notice in {"command_only", "always", "never"} else "command_only"
    )
    cfg["job_persistence_enabled"] = _bool_value(cfg.get("job_persistence_enabled", True))
    cfg["job_recovery_max_age_hours"] = _int_between(
        cfg.get("job_recovery_max_age_hours"), 24, 1, 168
    )
    cfg["job_history_size"] = _int_between(cfg.get("job_history_size"), 100, 10, 500)
    cleanup = str(cfg.get("background_audio_cleanup") or "after_delivery").strip().lower()
    cfg["background_audio_cleanup"] = (
        cleanup if cleanup in {"after_delivery", "retention"} else "after_delivery"
    )
    cfg["platform_preflight_enabled"] = _bool_value(
        cfg.get("platform_preflight_enabled", True)
    )
    cfg["live_api_test_enabled"] = _bool_value(cfg.get("live_api_test_enabled", False))
    cfg["auto_tts_enabled"] = _bool_value(cfg.get("auto_tts_enabled", False))
    try:
        probability = float(cfg.get("auto_tts_probability") or 0.0)
    except (TypeError, ValueError):
        probability = 0.0
    cfg["auto_tts_probability"] = min(1.0, max(0.0, probability))
    cfg["auto_tts_group_whitelist"] = _string_list(cfg.get("auto_tts_group_whitelist"))
    cfg["auto_tts_group_blacklist"] = _string_list(cfg.get("auto_tts_group_blacklist"))
    cfg["auto_tts_private_whitelist"] = _string_list(cfg.get("auto_tts_private_whitelist"))
    cfg["auto_tts_private_blacklist"] = _string_list(cfg.get("auto_tts_private_blacklist"))
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
    if "director_text_mode" not in raw:
        cfg["director_text_mode"] = "optimize" if cfg["ai_style_director_optimize_text"] else "instructions"
    if cfg["director_text_mode"] not in {"off", "instructions", "tags", "optimize"}:
        cfg["director_text_mode"] = "instructions"
    cfg["ai_style_director_fallback_to_emotion"] = _bool_value(
        cfg.get("ai_style_director_fallback_to_emotion", True)
    )
    cfg["ai_style_director_debug_log"] = _bool_value(
        cfg.get("ai_style_director_debug_log", True)
    )
    cfg["segment_enabled"] = _bool_value(cfg.get("segment_enabled", True))
    cfg["segment_threshold_chars"] = _int_between(
        cfg.get("segment_threshold_chars"), 2500, 1, cfg["max_text_chars"]
    )
    cfg["segment_max_segments"] = _int_between(cfg.get("segment_max_segments"), 6, 1, 20)
    admins = cfg.get("admin_users") or []
    cfg["admin_users"] = _string_list(admins)
    return cfg


def build_plugin_config(raw: dict[str, Any] | None) -> PluginConfig:
    cfg = normalize_config(raw)
    return PluginConfig(**cfg)


def migrate_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Migrate persisted plugin configuration into the current schema."""
    if not isinstance(raw, dict):
        return {}
    migrated = copy.deepcopy(raw)
    try:
        version = int(migrated.get("config_version") or 1)
    except (TypeError, ValueError):
        version = 1
    if version < 2:
        migrated.setdefault("job_persistence_enabled", True)
        migrated.setdefault("job_recovery_max_age_hours", 24)
        migrated.setdefault("job_history_size", 100)
        migrated.setdefault("background_audio_cleanup", "after_delivery")
        migrated.setdefault("tts_rate_limit_rpm", 90)
        migrated.setdefault("platform_preflight_enabled", True)
        migrated.setdefault("live_api_test_enabled", False)
    if version < 3:
        migrated.setdefault("audio_transport", "base64")
        migrated.setdefault("shared_path_linux", DEFAULT_CONFIG["shared_path_linux"])
        migrated.setdefault("shared_path_windows", DEFAULT_CONFIG["shared_path_windows"])
    migrated["config_version"] = 3
    return migrated


def _int_at_least(value: Any, default: int, minimum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, parsed)


def _int_between(value: Any, default: int, minimum: int, maximum: int) -> int:
    return min(maximum, _int_at_least(value, default, minimum))


def _float_between(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return min(maximum, max(minimum, parsed))


def _bool_value(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off", ""}
    return bool(value)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = value.replace("，", ",").replace("\n", ",").split(",")
    if not isinstance(value, (list, tuple, set)):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
