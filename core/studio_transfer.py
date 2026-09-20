"""Portable JSON packages; paths and credentials are never part of the format."""
from __future__ import annotations

import base64
import binascii
import copy
import json
import uuid
from pathlib import Path

from .audio_codec import validate_voice_file
from .model_catalog import validate_voice

VOICE_FIELDS = (
    "name", "type", "description", "builtin_voice", "design_prompt",
    "style_context", "style_tags", "emotion",
)
MAX_PACKAGE_BYTES = 24 * 1024 * 1024


def export_package(voices, studio, ids: list[str], *, include_audio: bool) -> dict:
    if len(ids) > 50:
        raise ValueError("一次最多导出 50 个音色")
    result = {"format": "mimo-studio-v1", "voices": [],
              "styles": list(studio.settings("styles").values())}
    for key in dict.fromkeys(ids):
        voice = voices.get_voice(key)
        if voice is None:
            raise ValueError("导出音色不存在")
        item = {field: getattr(voice, field) for field in VOICE_FIELDS}
        if voice.type == "clone":
            if not include_audio:
                raise ValueError("导出克隆音色需要明确允许包含参考音频")
            path = Path(voice.audio_path).resolve()
            root = (voices.data_dir / "voice_refs").resolve()
            if not path.is_relative_to(root):
                raise ValueError("参考音频不在受管目录中")
            validate_voice_file(path)
            item["audio"] = {"extension": path.suffix.lower(),
                             "base64": base64.b64encode(path.read_bytes()).decode("ascii")}
        result["voices"].append(item)
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_PACKAGE_BYTES:
        raise ValueError("导出包超过 24 MB，请分批导出")
    return result


def import_package(voices, studio, package: dict, *, consent: bool) -> list[str]:
    if not isinstance(package, dict) or package.get("format") != "mimo-studio-v1":
        raise ValueError("不支持的导入包格式")
    if len(json.dumps(package, ensure_ascii=False).encode("utf-8")) > MAX_PACKAGE_BYTES:
        raise ValueError("导入包超过 24 MB")
    items, styles = package.get("voices", []), package.get("styles", [])
    if not isinstance(items, list) or not isinstance(styles, list) or len(items) > 50 or len(styles) > 100:
        raise ValueError("导入条目过多或格式无效")
    prepared, files = [], []
    root = voices.data_dir / "voice_refs"
    root.mkdir(parents=True, exist_ok=True)
    old_state = copy.deepcopy(voices._state)
    style_ids = []
    try:
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("音色格式无效")
            fields = {key: str(item.get(key) or "") for key in VOICE_FIELDS}
            fields["type"] = fields["type"] or "clone"
            if not fields["name"] or any(len(v) > 8000 for v in fields.values()):
                raise ValueError("音色名称为空或字段过长")
            validate_voice(fields["type"], fields["builtin_voice"], fields["design_prompt"])
            path = ""
            if fields["type"] == "clone":
                if not consent:
                    raise ValueError("请确认克隆音频使用授权")
                audio = item.get("audio")
                if not isinstance(audio, dict) or audio.get("extension") not in {".wav", ".mp3"}:
                    raise ValueError("参考音频格式无效")
                encoded = audio.get("base64")
                if not isinstance(encoded, str) or len(encoded) > 10 * 1024 * 1024:
                    raise ValueError("参考音频过大")
                try:
                    raw = base64.b64decode(encoded, validate=True)
                except (ValueError, binascii.Error) as exc:
                    raise ValueError("参考音频 Base64 无效") from exc
                path = root / f"import_{uuid.uuid4().hex}{audio['extension']}"
                files.append(path)
                path.write_bytes(raw)
                validate_voice_file(path)
            prepared.append((fields, path))
        normalized_styles = []
        for style in styles:
            if not isinstance(style, dict):
                raise ValueError("风格格式无效")
            name, context = str(style.get("name") or ""), str(style.get("context") or "")
            if not name or len(name) > 100 or len(context) > 8000:
                raise ValueError("风格名称或描述无效")
            normalized_styles.append({"name": name, "context": context})
        created = []
        for fields, path in prepared:
            name, description = fields.pop("name"), fields.pop("description")
            voice = voices.add_voice(name, path, description, "import", consent, **fields)
            created.append(voice.id)
        for style in normalized_styles:
            key = uuid.uuid4().hex
            style_ids.append(key)
            studio.save_setting("styles", key, style)
        return created
    except Exception:
        voices._state = old_state
        voices.save()
        for key in style_ids:
            studio.save_setting("styles", key, None)
        for path in files:
            path.unlink(missing_ok=True)
        raise
