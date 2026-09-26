from __future__ import annotations

import json
import shutil
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from .model_catalog import MODELS, validate_voice


@dataclass(slots=True)
class VoiceProfile:
    id: str
    name: str
    audio_path: str
    description: str
    created_by: str
    created_at: str
    enabled: bool
    consent_confirmed: bool
    style_context: str = ""
    style_tags: str = ""
    emotion: str = ""
    type: str = "clone"
    builtin_voice: str = ""
    design_prompt: str = ""
    source_history_id: str = ""

    @property
    def model(self) -> str:
        return MODELS[self.type]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "VoiceProfile":
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or ""),
            audio_path=str(data.get("audio_path") or ""),
            description=str(data.get("description") or ""),
            created_by=str(data.get("created_by") or ""),
            created_at=str(data.get("created_at") or ""),
            enabled=bool(data.get("enabled", True)),
            consent_confirmed=bool(data.get("consent_confirmed", False)),
            style_context=str(data.get("style_context") or ""),
            style_tags=str(data.get("style_tags") or ""),
            emotion=str(data.get("emotion") or ""),
            type=str(data.get("type") or "clone"),
            builtin_voice=str(data.get("builtin_voice") or ""),
            design_prompt=str(data.get("design_prompt") or ""),
            source_history_id=str(data.get("source_history_id") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class VoiceStore:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "voices.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        migration_backup = self.data_dir / "voices.pre-v080.json"
        if self.path.is_file() and not migration_backup.exists():
            shutil.copyfile(self.path, migration_backup)
        self._state = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {
                "voices": [],
                "global_default_voice_id": "",
                "user_defaults": {},
                "group_defaults": {},
                "emotion_defaults": {},
            }
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        return {
            "voices": list(raw.get("voices") or []),
            "global_default_voice_id": str(raw.get("global_default_voice_id") or ""),
            "user_defaults": dict(raw.get("user_defaults") or {}),
            "group_defaults": dict(raw.get("group_defaults") or {}),
            "emotion_defaults": dict(raw.get("emotion_defaults") or {}),
        }

    def save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        backup = self.path.with_suffix(self.path.suffix + ".bak")
        try:
            temporary.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if self.path.is_file():
                shutil.copyfile(self.path, backup)
            temporary.replace(self.path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    def list_voices(self, *, include_disabled: bool = True) -> list[VoiceProfile]:
        voices = [VoiceProfile.from_dict(item) for item in self._state["voices"]]
        if not include_disabled:
            voices = [voice for voice in voices if voice.enabled and (
                voice.type != "clone" or voice.consent_confirmed
            )]
        return voices

    def get_voice(self, voice_id: str) -> VoiceProfile | None:
        for voice in self.list_voices():
            if voice.id == voice_id:
                return voice
        return None

    def duplicate_voice(self, voice_id: str) -> VoiceProfile:
        voice = self.get_voice(voice_id)
        if voice is None:
            raise ValueError("音色不存在")
        audio = voice.audio_path
        copied = None
        if voice.type == "clone":
            source = Path(audio).resolve()
            root = (self.data_dir / "voice_refs").resolve()
            if not source.is_relative_to(root) or not source.is_file():
                raise ValueError("参考音频不可用")
            copied = root / f"copy_{uuid.uuid4().hex}{source.suffix}"
            shutil.copyfile(source, copied)
            audio = str(copied)
        try:
            return self.add_voice(
                voice.name + " 副本", audio, voice.description, voice.created_by,
                voice.consent_confirmed, type=voice.type, builtin_voice=voice.builtin_voice,
                design_prompt=voice.design_prompt, style_context=voice.style_context,
                style_tags=voice.style_tags, emotion=voice.emotion,
                source_history_id=voice.source_history_id,
            )
        except Exception:
            if copied:
                copied.unlink(missing_ok=True)
            raise

    def find_voice(self, selector: str | None) -> VoiceProfile | None:
        needle = str(selector or "").strip()
        if not needle:
            return None
        for voice in self.list_voices(include_disabled=False):
            if voice.id == needle or voice.name == needle:
                return voice
        lowered = needle.lower()
        for voice in self.list_voices(include_disabled=False):
            if voice.name.lower() == lowered:
                return voice
        return None

    def add_voice(
        self,
        name: str,
        audio_path: str | Path,
        description: str,
        created_by: str,
        consent_confirmed: bool,
        *,
        style_context: str = "",
        style_tags: str = "",
        emotion: str = "",
        type: str = "clone",
        builtin_voice: str = "",
        design_prompt: str = "",
        source_history_id: str = "",
    ) -> VoiceProfile:
        validate_voice(type, builtin_voice, design_prompt)
        if type == "clone" and not consent_confirmed:
            raise ValueError("Voice consent must be explicitly confirmed.")
        voice_id = f"voice_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        voice = VoiceProfile(
            id=voice_id,
            name=str(name or voice_id).strip() or voice_id,
            audio_path=str(audio_path),
            description=str(description or ""),
            created_by=str(created_by or ""),
            created_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            enabled=True,
            consent_confirmed=bool(consent_confirmed),
            style_context=str(style_context or ""),
            style_tags=str(style_tags or ""),
            emotion=str(emotion or ""),
            type=type,
            builtin_voice=builtin_voice,
            design_prompt=design_prompt.strip(),
            source_history_id=source_history_id,
        )
        self._state["voices"].append(voice.to_dict())
        if not self._state.get("global_default_voice_id"):
            self._state["global_default_voice_id"] = voice.id
        self.save()
        return voice

    def update_voice(self, voice_id: str, **changes: Any) -> VoiceProfile | None:
        for item in self._state["voices"]:
            if item.get("id") != voice_id:
                continue
            validate_voice(
                str(item.get("type") or "clone"),
                str(changes.get("builtin_voice", item.get("builtin_voice", ""))),
                str(changes.get("design_prompt", item.get("design_prompt", ""))),
            )
            for key in ("name", "description", "enabled", "style_context", "style_tags", "emotion",
                        "builtin_voice", "design_prompt", "source_history_id"):
                if key in changes:
                    item[key] = changes[key]
            self.save()
            return VoiceProfile.from_dict(item)
        return None

    def delete_voice(self, voice_id: str) -> bool:
        before = len(self._state["voices"])
        self._state["voices"] = [
            item for item in self._state["voices"] if item.get("id") != voice_id
        ]
        deleted = len(self._state["voices"]) != before
        if deleted:
            if self._state.get("global_default_voice_id") == voice_id:
                self._state["global_default_voice_id"] = ""
            self._state["user_defaults"] = {
                key: value
                for key, value in self._state["user_defaults"].items()
                if value != voice_id
            }
            self._state["group_defaults"] = {
                key: value
                for key, value in self._state["group_defaults"].items()
                if value != voice_id
            }
            self._state["emotion_defaults"] = {
                key: value
                for key, value in self._state["emotion_defaults"].items()
                if value != voice_id
            }
            self.save()
        return deleted

    def set_global_default(self, voice_id: str) -> None:
        self._state["global_default_voice_id"] = voice_id
        self.save()

    def set_user_default(self, user_id: str, voice_id: str) -> None:
        self._state["user_defaults"][str(user_id)] = voice_id
        self.save()

    def set_group_default(self, group_id: str, voice_id: str) -> None:
        self._state["group_defaults"][str(group_id)] = voice_id
        self.save()

    def set_emotion_default(self, emotion: str, voice_id: str) -> None:
        key = str(emotion or "").strip().lower()
        if not key:
            return
        if not voice_id:
            self._state["emotion_defaults"].pop(key, None)
        else:
            self._state["emotion_defaults"][key] = voice_id
        self.save()

    def defaults(self) -> dict[str, Any]:
        return {
            "global_default_voice_id": self._state.get("global_default_voice_id", ""),
            "user_defaults": dict(self._state.get("user_defaults") or {}),
            "group_defaults": dict(self._state.get("group_defaults") or {}),
            "emotion_defaults": dict(self._state.get("emotion_defaults") or {}),
        }

    def set_binding(self, scope: str, key: str, voice_id: str) -> None:
        if scope not in {"global", "user", "group", "emotion"}:
            raise ValueError("绑定类型无效")
        if voice_id:
            voice = self.get_voice(voice_id)
            if voice is None or not voice.enabled or (voice.type == "clone" and not voice.consent_confirmed):
                raise ValueError("绑定音色不可用")
        if scope == "global":
            self._state["global_default_voice_id"] = voice_id
        else:
            if not key.strip() or len(key) > 256:
                raise ValueError("绑定目标不能为空或超过 256 字")
            mapping = self._state[f"{scope}_defaults"]
            if voice_id:
                mapping[key] = voice_id
            else:
                mapping.pop(key, None)
        self.save()

    def resolve_voice_id(
        self,
        requested_voice: str | None,
        user_id: str | None,
        group_id: str | None,
        *,
        emotion: str | None = None,
    ) -> str | None:
        requested = self.find_voice(requested_voice)
        if requested is not None:
            return requested.id

        candidates = []
        emotion_key = str(emotion or "").strip().lower()
        if user_id:
            candidates.append(self._state["user_defaults"].get(str(user_id)))
        if group_id:
            candidates.append(self._state["group_defaults"].get(str(group_id)))
        if emotion_key:
            candidates.append(self._state["emotion_defaults"].get(emotion_key))
        candidates.append(self._state.get("global_default_voice_id"))

        enabled_ids = {voice.id for voice in self.list_voices(include_disabled=False)
                       if voice.type != "clone" or voice.consent_confirmed}
        for candidate in candidates:
            if candidate and candidate in enabled_ids:
                return candidate
        return None
