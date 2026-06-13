from __future__ import annotations

import asyncio
import json
import pathlib
import random
import shlex
import time
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import File
from astrbot.api.star import Context, Star, StarTools, register

try:
    from astrbot.api.message_components import Plain, Record
except Exception:  # pragma: no cover - AstrBot versions differ here.
    Plain = None
    Record = None

from .core.audio_codec import encode_voice_file_data_url, estimate_base64_chars
from .core.config import build_plugin_config, normalize_config
from .core.emotion import EmotionRouter, SUPPORTED_EMOTIONS, normalize_emotion
from .core.mimo_official_client import MimoOfficialClient, MimoTTSConfig
from .core.text_processing import clean_tts_text, split_tts_text
from .core.voice_store import VoiceProfile, VoiceStore
from .pages_api import PagesAPIMixin


@register(
    "astrbot_plugin_mimo_tts_clone",
    "Justice-ocr",
    "MiMo 官方 TTS 音色克隆与多音色切换",
    "0.2.0",
)
class MimoTTSClonePlugin(PagesAPIMixin, Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.context = context
        self.logger = logger
        self._native_config = config if hasattr(config, "save_config") else None
        self.data_dir = StarTools.get_data_dir("astrbot_plugin_mimo_tts_clone")
        pathlib.Path(self.data_dir).mkdir(parents=True, exist_ok=True)
        self._config_file = pathlib.Path(self.data_dir) / "config.json"
        native_config = self._coerce_config(config)
        persisted_config = self._load_persisted_config()
        self.config = normalize_config({**native_config, **persisted_config})
        self.plugin_config = build_plugin_config(self.config)
        self.emotion_router = EmotionRouter(emotion_contexts=self.plugin_config.emotion_contexts)
        self.voice_store = VoiceStore(self.data_dir)
        self._tts_sem = asyncio.Semaphore(self.plugin_config.max_concurrency)
        self._register_pages_web_api()

    @staticmethod
    def _coerce_config(config: Any) -> dict[str, Any]:
        if isinstance(config, dict):
            return dict(config)
        items = getattr(config, "items", None)
        if callable(items):
            try:
                return dict(items())
            except Exception:
                return {}
        getter = getattr(config, "get", None)
        if callable(getter):
            values = {}
            for key in normalize_config({}):
                try:
                    value = getter(key)
                except Exception:
                    continue
                if value is not None:
                    values[key] = value
            return values
        return {}

    def _load_persisted_config(self) -> dict[str, Any]:
        try:
            if not self._config_file.is_file():
                return {}
            data = json.loads(self._config_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            self.logger.warning("[mimo-tts] failed to read persisted config: %s", exc)
            return {}

    def _persist_local_config(self) -> None:
        self._config_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._config_file.with_suffix(".json.tmp")
        tmp_path.write_text(
            json.dumps(self.config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(self._config_file)

    def _update_runtime_config(self, changes: dict[str, Any]) -> dict[str, Any]:
        merged = dict(self.config)
        for key in normalize_config({}):
            if key in changes:
                merged[key] = changes[key]
        self.config = normalize_config(merged)
        self.plugin_config = build_plugin_config(self.config)
        self.emotion_router = EmotionRouter(emotion_contexts=self.plugin_config.emotion_contexts)
        self._tts_sem = asyncio.Semaphore(self.plugin_config.max_concurrency)
        persisted = {"local": False, "native": False, "warning": ""}
        try:
            self._persist_local_config()
            persisted["local"] = True
        except Exception as exc:
            persisted["warning"] = f"failed to persist local config: {exc}"
            self.logger.warning("[mimo-tts] failed to persist local config: %s", exc)
        if self._native_config is not None:
            try:
                self._native_config.update(self.config)
                self._native_config.save_config()
                persisted["native"] = True
            except Exception as exc:
                persisted["warning"] = f"failed to persist native config: {exc}"
                self.logger.warning("[mimo-tts] failed to persist native config: %s", exc)
        return persisted

    def _client(self) -> MimoOfficialClient:
        return MimoOfficialClient(
            MimoTTSConfig(
                api_key=self.plugin_config.api_key,
                base_url=self.plugin_config.base_url,
                model=self.plugin_config.model,
                output_format=self.plugin_config.output_format,
            )
        )

    async def _synthesize_text_to_file(
        self,
        text: str,
        voice: VoiceProfile,
        *,
        context: str = "",
    ) -> pathlib.Path:
        assistant_text = text.strip()
        if voice.style_tags.strip():
            assistant_text = f"{voice.style_tags.strip()} {assistant_text}".strip()
        if len(assistant_text) > self.plugin_config.max_text_chars:
            raise RuntimeError(f"文本过长，最大 {self.plugin_config.max_text_chars} 字")

        voice_data_url = await asyncio.to_thread(
            encode_voice_file_data_url,
            pathlib.Path(voice.audio_path),
            max_bytes=self.plugin_config.max_voice_file_bytes,
            max_base64_chars=estimate_base64_chars(self.plugin_config.max_voice_file_bytes),
        )
        output_dir = pathlib.Path(self.data_dir) / "outputs"
        output_path = output_dir / f"mimo_tts_{time.time_ns()}.wav"
        async with self._tts_sem:
            result = await self._client().synthesize_to_file(
                text=assistant_text,
                voice_data_url=voice_data_url,
                output_path=output_path,
                context=context or self.plugin_config.default_context,
            )
        await asyncio.to_thread(self._cleanup_outputs)
        return result

    def _cleanup_outputs(self) -> None:
        output_dir = pathlib.Path(self.data_dir) / "outputs"
        if not output_dir.is_dir():
            return
        files = [path for path in output_dir.glob("mimo_tts_*") if path.is_file()]
        now = time.time()
        retention_days = self.plugin_config.output_retention_days
        if retention_days > 0:
            cutoff = now - retention_days * 86400
            for path in files:
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink(missing_ok=True)
                except OSError:
                    pass
            files = [path for path in files if path.exists()]
        max_files = self.plugin_config.output_max_files
        if max_files > 0 and len(files) > max_files:
            files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
            for path in files[max_files:]:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

    def list_available_voices(self, *, include_disabled: bool = False) -> list[dict[str, Any]]:
        """Public service helper for other plugins that need voice metadata."""
        return [
            voice.to_dict()
            for voice in self.voice_store.list_voices(include_disabled=include_disabled)
        ]

    def resolve_voice_id(
        self,
        voice_selector: str | None = None,
        *,
        user_id: str = "",
        group_id: str = "",
        emotion: str | None = None,
    ) -> str | None:
        """Resolve the effective voice id using the same priority as chat commands."""
        return self.voice_store.resolve_voice_id(
            voice_selector,
            user_id,
            group_id,
            emotion=emotion,
        )

    def _select_voice(
        self,
        voice_selector: str | None = None,
        *,
        user_id: str = "",
        group_id: str = "",
        emotion: str | None = None,
    ) -> VoiceProfile | None:
        voice = self.voice_store.find_voice(voice_selector or "") if voice_selector else None
        if voice is not None:
            return voice
        resolved_voice_id = self.resolve_voice_id(
            voice_selector,
            user_id=user_id,
            group_id=group_id,
            emotion=emotion,
        )
        return self.voice_store.get_voice(resolved_voice_id or "") if resolved_voice_id else None

    async def synthesize_text(
        self,
        text: str,
        *,
        voice_id: str | None = None,
        voice_name: str | None = None,
        emotion: str | None = None,
        context: str = "",
        user_id: str = "",
        group_id: str = "",
        split: bool = True,
    ) -> list[pathlib.Path]:
        """Public service helper for commands, Pages, and other plugins."""
        cleaned = clean_tts_text(text)
        if not cleaned:
            raise RuntimeError("请输入要合成的文本")
        resolved_emotion = self._resolve_emotion(cleaned, emotion)
        selector = voice_id or voice_name
        voice = self._select_voice(
            selector,
            user_id=user_id,
            group_id=group_id,
            emotion=resolved_emotion,
        )
        if voice is None:
            raise RuntimeError("暂无可用音色，请先在插件 Pages 中上传参考音频。")
        tts_context = self._build_tts_context(voice, resolved_emotion, context)
        segments = self._split_for_tts(cleaned) if split else [cleaned]
        return [
            await self._synthesize_text_to_file(segment, voice, context=tts_context)
            for segment in segments
        ]

    async def text_to_speech(
        self,
        text: str,
        *,
        emotion: str = "",
        target_umo: str = "",
        session: str = "",
        session_id: str = "",
        voice: str = "",
        voice_name: str = "",
        context: str = "",
        session_state: Any = None,
    ) -> str:
        """Compatibility helper for generic TTS callers such as daily_sharing."""
        del session_state
        group_id = str(target_umo or session or session_id or "").strip()
        outputs = await self.synthesize_text(
            text,
            voice_name=voice_name or voice or None,
            emotion=emotion or None,
            context=context,
            group_id=group_id,
        )
        return str(outputs[0]) if outputs else ""

    if hasattr(filter, "llm_tool"):

        @filter.llm_tool(name="mimo_tts_speak")
        async def mimo_tts_speak(
            self,
            event: AstrMessageEvent,
            text: str,
            emotion: str = "neutral",
            voice: str = "",
            style: str = "",
        ):
            """Generate and send MiMo TTS voice audio.

            Args:
                text(string): Text that should be converted to speech.
                emotion(string): Optional emotion, one of happy, sad, angry, neutral.
                voice(string): Optional voice name or voice id.
                style(string): Optional temporary style instruction.

            Returns:
                string: Generated audio path or a short failure message.
            """
            content = str(text or "").strip()
            if not content:
                yield "empty text"
                return
            try:
                outputs = await self.synthesize_text(
                    content,
                    voice_name=str(voice or "").strip() or None,
                    emotion=emotion,
                    context=style,
                    user_id=str(getattr(event, "get_sender_id", lambda: "")() or "").strip(),
                    group_id=str(getattr(event, "unified_msg_origin", "") or "").strip()
                    or self._conversation_id(event),
                )
            except Exception as exc:
                yield f"tts failed: {exc}"
                return
            sent = 0
            for output in outputs:
                await self._send_audio_result(event, output)
                sent += 1
            if hasattr(event, "clear_result"):
                event.clear_result()
            yield str(outputs[0]) if outputs else f"sent {sent} audio"
            return

    @staticmethod
    def _conversation_id(event: AstrMessageEvent) -> str:
        try:
            provider_request = event.get_extra("provider_request")
            conversation = getattr(provider_request, "conversation", None)
            return str(getattr(conversation, "cid", "") or "").strip()
        except Exception:
            return ""

    @staticmethod
    def _tail(message: str, command: str) -> str:
        text = str(message or "").strip()
        for prefix in ("", "/", "!", "！", ".", "。"):
            token = f"{prefix}{command}"
            if text.startswith(token):
                return text[len(token) :].strip()
        return text

    @classmethod
    def _tail_any(cls, message: str, commands: tuple[str, ...]) -> str:
        text = str(message or "").strip()
        for command in commands:
            tail = cls._tail(text, command)
            if tail != text:
                return tail
        return text

    @staticmethod
    def _parse_tts_args(raw: str) -> tuple[str | None, str | None, str, str]:
        voice = None
        emotion = None
        context = ""
        try:
            parts = shlex.split(raw)
        except Exception:
            parts = raw.split()
        text_parts: list[str] = []
        i = 0
        while i < len(parts):
            item = parts[i]
            if item in {"-v", "--voice"} and i + 1 < len(parts):
                voice = parts[i + 1]
                i += 2
                continue
            if item in {"-e", "--emotion"} and i + 1 < len(parts):
                emotion = parts[i + 1]
                i += 2
                continue
            if item in {"-c", "--context"} and i + 1 < len(parts):
                context = parts[i + 1]
                i += 2
                continue
            text_parts.append(item)
            i += 1
        return voice, emotion, " ".join(text_parts).strip(), context

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        user_id = str(event.get_sender_id() or "").strip()
        return user_id in set(self.plugin_config.admin_users)

    def _resolve_emotion(self, text: str, requested: str | None) -> str:
        if not self.plugin_config.emotion_routing_enabled:
            return normalize_emotion(requested) or "neutral"
        return self.emotion_router.resolve(text, requested=requested)

    def _build_tts_context(self, voice: VoiceProfile, emotion: str, command_context: str) -> str:
        return self.emotion_router.build_context(
            base_context=self.plugin_config.default_context,
            emotion=emotion,
            voice_context=voice.style_context,
            command_context=command_context,
        )

    def _split_for_tts(self, text: str) -> list[str]:
        if not self.plugin_config.segment_enabled:
            return [text]
        if len(text) < self.plugin_config.segment_threshold_chars:
            return [text]
        return split_tts_text(
            text,
            max_chars=self.plugin_config.segment_threshold_chars,
            max_segments=self.plugin_config.segment_max_segments,
        )

    def _audio_component(self, audio_path: pathlib.Path):
        if Record is not None:
            return Record(file=str(audio_path))
        if self.plugin_config.file_fallback_enabled:
            return File(name=audio_path.name, file=str(audio_path))
        return None

    @staticmethod
    def _is_plain_component(component: Any) -> bool:
        if Plain is not None and isinstance(component, Plain):
            return True
        return component.__class__.__name__ == "Plain"

    async def _send_audio_result(self, event: AstrMessageEvent, audio_path: pathlib.Path) -> None:
        if Record is not None:
            try:
                await event.send(event.chain_result([Record(file=str(audio_path))]))
                return
            except Exception as exc:
                self.logger.warning("[mimo-tts] Record send failed, fallback to file: %s", exc)
        if self.plugin_config.file_fallback_enabled:
            await event.send(event.chain_result([File(name=audio_path.name, file=str(audio_path))]))

    @filter.command("tts", alias={"朗读", "语音"})
    async def tts_command(self, event: AstrMessageEvent):
        raw = self._tail_any(event.message_str, ("tts", "朗读", "语音"))
        voice_name, requested_emotion, text, command_context = self._parse_tts_args(raw)
        text = clean_tts_text(text)
        if not text:
            yield event.plain_result(
                "用法：/tts [-v 音色名] [-e happy|sad|angry|neutral] [-c 风格指令] 文本"
            )
            return

        if self.plugin_config.reply_mode in {"text_only", "text_and_audio"}:
            yield event.plain_result(text)
        if self.plugin_config.reply_mode == "text_only":
            return

        try:
            outputs = await self.synthesize_text(
                text,
                voice_name=voice_name,
                emotion=requested_emotion,
                context=command_context,
                user_id=str(event.get_sender_id() or "").strip(),
                group_id=self._conversation_id(event),
            )
        except Exception as exc:
            yield event.plain_result(f"语音生成失败：{exc}")
            return

        for output in outputs:
            await self._send_audio_result(event, output)

    @filter.command("tts音色列表", alias={"音色列表"})
    async def list_voices_command(self, event: AstrMessageEvent):
        voices = self.voice_store.list_voices(include_disabled=False)
        if not voices:
            yield event.plain_result("暂无可用音色，请先在插件 Pages 中上传参考音频。")
            return

        defaults = self.voice_store.defaults()
        emotion_defaults = defaults.get("emotion_defaults") or {}
        lines = ["可用音色："]
        for voice in voices:
            marker = " *" if voice.id == defaults.get("global_default_voice_id") else ""
            emotions = [key for key, value in emotion_defaults.items() if value == voice.id]
            emotion_note = f" [{','.join(emotions)}]" if emotions else ""
            desc = f" - {voice.description}" if voice.description else ""
            lines.append(f"{voice.name}{marker}{emotion_note} ({voice.id}){desc}")
        yield event.plain_result("\n".join(lines))

    @filter.on_decorating_result()
    async def auto_tts_reply(self, event: AstrMessageEvent):
        if not self.plugin_config.auto_tts_enabled:
            return
        if self.plugin_config.reply_mode == "text_only":
            return
        if random.random() > self.plugin_config.auto_tts_probability:
            return
        result = event.get_result()
        if result is None or not getattr(result, "chain", None):
            return
        is_llm_result = getattr(result, "is_llm_result", None)
        if callable(is_llm_result) and not is_llm_result():
            return
        text = clean_tts_text(result.get_plain_text())
        if not text:
            return
        try:
            outputs = await self.synthesize_text(
                text,
                user_id=str(event.get_sender_id() or "").strip(),
                group_id=self._conversation_id(event),
            )
        except Exception as exc:
            self.logger.warning("[mimo-tts] auto tts failed: %s", exc)
            return

        audio_components = [
            component
            for component in (self._audio_component(output) for output in outputs)
            if component is not None
        ]
        if not audio_components:
            return
        if self.plugin_config.reply_mode == "audio_only":
            result.chain = [comp for comp in result.chain if not self._is_plain_component(comp)]
        result.chain.extend(audio_components)

    @filter.command("tts设置音色", alias={"设置音色"})
    async def set_user_voice_command(self, event: AstrMessageEvent):
        selector = self._tail_any(event.message_str, ("tts设置音色", "设置音色"))
        voice = self.voice_store.find_voice(selector)
        if voice is None:
            yield event.plain_result("找不到该音色，可发送 /tts音色列表 查看。")
            return
        self.voice_store.set_user_default(str(event.get_sender_id() or ""), voice.id)
        yield event.plain_result(f"已将你的默认音色设为：{voice.name}")

    @filter.command("tts默认音色", alias={"默认音色"})
    async def set_global_voice_command(self, event: AstrMessageEvent):
        if not self._is_admin(event):
            yield event.plain_result("只有插件管理员可以设置全局默认音色。")
            return
        selector = self._tail_any(event.message_str, ("tts默认音色", "默认音色"))
        voice = self.voice_store.find_voice(selector)
        if voice is None:
            yield event.plain_result("找不到该音色，可发送 /tts音色列表 查看。")
            return
        self.voice_store.set_global_default(voice.id)
        yield event.plain_result(f"已将全局默认音色设为：{voice.name}")

    @filter.command("tts群默认音色", alias={"群默认音色"})
    async def set_group_voice_command(self, event: AstrMessageEvent):
        if not self._is_admin(event):
            yield event.plain_result("只有插件管理员可以设置群默认音色。")
            return
        group_id = self._conversation_id(event)
        if not group_id:
            yield event.plain_result("当前会话无法识别群/会话 ID。")
            return
        selector = self._tail_any(event.message_str, ("tts群默认音色", "群默认音色"))
        voice = self.voice_store.find_voice(selector)
        if voice is None:
            yield event.plain_result("找不到该音色，可发送 /tts音色列表 查看。")
            return
        self.voice_store.set_group_default(group_id, voice.id)
        yield event.plain_result(f"已将本会话默认音色设为：{voice.name}")

    @filter.command("tts情绪音色", alias={"情绪音色"})
    async def set_emotion_voice_command(self, event: AstrMessageEvent):
        if not self._is_admin(event):
            yield event.plain_result("只有插件管理员可以设置情绪默认音色。")
            return
        raw = self._tail_any(event.message_str, ("tts情绪音色", "情绪音色"))
        parts = raw.split(maxsplit=1)
        if len(parts) != 2:
            yield event.plain_result("用法：/tts情绪音色 <happy|sad|angry|neutral> <音色名>")
            return
        emotion = normalize_emotion(parts[0])
        if emotion not in SUPPORTED_EMOTIONS:
            yield event.plain_result("情绪只支持：happy, sad, angry, neutral")
            return
        voice = self.voice_store.find_voice(parts[1])
        if voice is None:
            yield event.plain_result("找不到该音色，可发送 /tts音色列表 查看。")
            return
        self.voice_store.set_emotion_default(emotion, voice.id)
        yield event.plain_result(f"已将 {emotion} 情绪默认音色设为：{voice.name}")

    @filter.command("tts状态", alias={"tts狀態"})
    async def status_command(self, event: AstrMessageEvent):
        defaults = self.voice_store.defaults()
        lines = [
            "MiMo TTS 状态",
            f"model: {self.plugin_config.model}",
            f"voices: {len(self.voice_store.list_voices(include_disabled=False))}",
            f"emotion_routing: {self.plugin_config.emotion_routing_enabled}",
            f"segment: {self.plugin_config.segment_enabled}, threshold={self.plugin_config.segment_threshold_chars}",
            f"reply_mode: {self.plugin_config.reply_mode}",
            f"auto_tts: {self.plugin_config.auto_tts_enabled}, probability={self.plugin_config.auto_tts_probability}",
            f"file_fallback: {self.plugin_config.file_fallback_enabled}",
            f"output_cleanup: days={self.plugin_config.output_retention_days}, max_files={self.plugin_config.output_max_files}",
            f"emotion_defaults: {defaults.get('emotion_defaults') or {}}",
        ]
        yield event.plain_result("\n".join(lines))

    async def terminate(self):
        pass
