from __future__ import annotations

import asyncio
from collections.abc import Mapping
import pathlib
import re
import time
from typing import Any

from .audio_codec import (
    AudioValidationError,
    SUPPORTED_AUDIO_MIME,
    estimate_base64_chars,
    validate_voice_file,
)
from .emotion import normalize_emotion


def _metadata_get(metadata: Any, key: str, default: str = "") -> str:
    if isinstance(metadata, Mapping):
        value = metadata.get(key, default)
    else:
        value = getattr(metadata, "get", lambda _key, _default=None: _default)(key, default)
    return str(value or default)


async def store_voice_sample(
    *,
    voice_store: Any,
    data_dir: str | pathlib.Path,
    max_voice_file_bytes: int,
    data: bytes,
    filename: str,
    metadata: Any,
):
    filename = pathlib.Path(filename or "voice.wav").name
    ext = pathlib.Path(filename).suffix.lower()
    if ext not in SUPPORTED_AUDIO_MIME:
        raise AudioValidationError("仅支持 mp3 / wav 音频")
    if len(data) > max_voice_file_bytes:
        raise AudioValidationError("音频文件超过大小限制")
    if estimate_base64_chars(len(data)) > max_voice_file_bytes:
        raise AudioValidationError("音频 Base64 编码后超过大小限制")

    voice_dir = pathlib.Path(data_dir) / "voice_refs"
    voice_dir.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w.-]+", "_", pathlib.Path(filename).stem).strip("._") or "voice"
    save_path = voice_dir / f"{time.time_ns()}_{stem[:60]}{ext}"
    await asyncio.to_thread(save_path.write_bytes, data)
    try:
        validate_voice_file(
            save_path,
            max_bytes=max_voice_file_bytes,
            max_base64_chars=max_voice_file_bytes,
        )
    except AudioValidationError:
        save_path.unlink(missing_ok=True)
        raise

    name = _metadata_get(metadata, "name", pathlib.Path(filename).stem or "新音色").strip()
    description = _metadata_get(metadata, "description").strip()
    created_by = _metadata_get(metadata, "created_by", "pages").strip()
    style_context = _metadata_get(metadata, "style_context").strip()
    style_tags = _metadata_get(metadata, "style_tags").strip()
    emotion = normalize_emotion(_metadata_get(metadata, "emotion")) or ""
    consent = _metadata_get(metadata, "consent_confirmed", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not consent:
        save_path.unlink(missing_ok=True)
        raise AudioValidationError("上传音色前必须明确确认已获得声音授权")
    return voice_store.add_voice(
        name,
        save_path,
        description,
        created_by,
        consent,
        style_context=style_context,
        style_tags=style_tags,
        emotion=emotion,
    )
