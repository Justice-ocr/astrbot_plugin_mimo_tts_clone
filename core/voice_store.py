from __future__ import annotations

import json
import shutil
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


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
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class VoiceStore:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "voices.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)
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
            voices = [voice for voice in voices if voice.enabled and voice.consent_confirmed]
        return voices

    def get_voice(self, voice_id: str) -> VoiceProfile | None:
        for voice in self.list_voices():
            if voice.id == voice_id:
                return voice
        return None

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
    ) -> VoiceProfile:
        if not consent_confirmed:
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
            for key in ("name", "description", "enabled", "style_context", "style_tags", "emotion"):
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
        if emotion_key:
            candidates.append(self._state["emotion_defaults"].get(emotion_key))
        if user_id:
            candidates.append(self._state["user_defaults"].get(str(user_id)))
        if group_id:
            candidates.append(self._state["group_defaults"].get(str(group_id)))
        candidates.append(self._state.get("global_default_voice_id"))

        enabled_ids = {voice.id for voice in self.list_voices(include_disabled=False)}
        for candidate in candidates:
            if candidate and candidate in enabled_ids:
                return candidate
        return None
