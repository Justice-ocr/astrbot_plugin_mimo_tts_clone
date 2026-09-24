from __future__ import annotations

import asyncio
import base64
import json
import pathlib
from pathlib import PureWindowsPath
import random
import re
import shlex
import shutil
import hashlib
import time
from collections import OrderedDict
from typing import Any
from contextvars import ContextVar
from dataclasses import asdict, replace

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import File
from astrbot.api.star import Context, Star, StarTools, register

try:
    from astrbot.api.message_components import Plain, Record
except Exception:  # pragma: no cover - AstrBot versions differ here.
    Plain = None
    Record = None

try:
    from astrbot.api.event import MessageChain
except Exception:  # pragma: no cover - older AstrBot/test stubs.
    MessageChain = None

from .core.audio_codec import encode_voice_file_data_url, estimate_base64_chars
from .core.config import build_plugin_config, normalize_config
from .core.emotion import EmotionRouter, SUPPORTED_EMOTIONS, normalize_emotion
from .core.mimo_official_client import (
    MimoOfficialClient,
    MimoRateLimitError,
    MimoTTSConfig,
    MimoTransientError,
)
from .core.style_director import StyleDirectorInput, generate_style_plan, protect_speech_text
from .core.synthesis_context import (
    TTSContextResult,
    build_style_director_cache_key,
    clip_log_text,
    merge_directed_context,
)
from .core.text_processing import clean_tts_text, split_tts_text
from .core.tts_jobs import TTSJob, TTSJobManager
from .core.tts_reliability import CircuitOpenError, ReliabilityController
from .core.wav_utils import merge_wav_files
from .core.voice_store import VoiceProfile, VoiceStore
from .core.model_catalog import BUILTIN_VOICES, singing_text
from .core.studio_store import StudioStore

_JOB_SYNTHESIS_CONFIG = ContextVar("mimo_job_synthesis_config", default=None)
_SYNTHESIS_FIELDS = {
    "default_context", "max_text_chars", "delivery_segment_chars",
    "emotion_routing_enabled", "emotion_contexts", "segment_enabled",
    "segment_threshold_chars", "segment_max_segments", "director_text_mode",
    "ai_style_director_enabled", "ai_style_director_provider_id",
    "ai_style_director_prompt", "ai_style_director_mode", "ai_style_director_max_chars",
    "ai_style_director_optimize_text", "ai_style_director_fallback_to_emotion",
}
from .pages_api import PagesAPIMixin


_FINAL_OUTPUT_RE = re.compile(r"^mimo_tts_(?!.*\.part).+\.wav$")
_PENDING_BACKGROUND_JOB_EXTRA = "mimo_tts_pending_background_job_v1"


@register(
    "astrbot_plugin_mimo_tts_clone",
    "Justice-ocr",
    "MiMo 官方 TTS 音色克隆、多音色切换与 AI 语音导演",
    "0.8.0-dev",
)
class MimoTTSClonePlugin(PagesAPIMixin, Star):
    @property
    def plugin_config(self):
        active = _JOB_SYNTHESIS_CONFIG.get()
        return active[1] if active is not None and active[0] is self else self._plugin_config

    @plugin_config.setter
    def plugin_config(self, value):
        self._plugin_config = value

    def _synthesis_snapshot(self) -> dict:
        return {key: value for key, value in asdict(self.plugin_config).items()
                if key in _SYNTHESIS_FIELDS}

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
        self.studio_store = StudioStore(self.data_dir)
        self._tts_sem = asyncio.Semaphore(self.plugin_config.max_concurrency)
        self._style_director_cache: OrderedDict[str, tuple[float, TTSContextResult]] = OrderedDict()
        self._mimo_client: MimoOfficialClient | None = None
        self._mimo_client_signature: tuple[Any, ...] | None = None
        self._client_users: dict[MimoOfficialClient, int] = {}
        self._retired_clients: set[MimoOfficialClient] = set()
        self._client_close_tasks: set[asyncio.Task] = set()
        self._voice_data_cache: OrderedDict[tuple[str, int, int], str] = OrderedDict()
        self._job_manager: TTSJobManager | None = None
        self._reliability = self._build_reliability_controller()
        self._register_pages_web_api()

    def _build_reliability_controller(self) -> ReliabilityController:
        return ReliabilityController(
            requests_per_minute=self.plugin_config.tts_rate_limit_rpm,
            max_retries=self.plugin_config.tts_max_retries,
            backoff_base_seconds=self.plugin_config.tts_retry_backoff_base_seconds,
            backoff_max_seconds=self.plugin_config.tts_retry_backoff_max_seconds,
            circuit_failure_threshold=self.plugin_config.circuit_failure_threshold,
            circuit_recovery_seconds=self.plugin_config.circuit_recovery_seconds,
        )

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
        previous_queue_settings = (
            self.plugin_config.background_queue_size,
            self.plugin_config.max_concurrency,
            self.plugin_config.job_persistence_enabled,
            self.plugin_config.job_recovery_max_age_hours,
            self.plugin_config.job_history_size,
        )
        merged = dict(self.config)
        for key in normalize_config({}):
            if key in changes:
                if key == "api_key" and not str(changes[key] or "").strip():
                    continue
                merged[key] = changes[key]
        self.config = normalize_config(merged)
        self.plugin_config = build_plugin_config(self.config)
        self.emotion_router = EmotionRouter(emotion_contexts=self.plugin_config.emotion_contexts)
        self._tts_sem = asyncio.Semaphore(self.plugin_config.max_concurrency)
        self._style_director_cache.clear()
        self._voice_data_cache.clear()
        self._reliability = self._build_reliability_controller()
        current_queue_settings = (
            self.plugin_config.background_queue_size,
            self.plugin_config.max_concurrency,
            self.plugin_config.job_persistence_enabled,
            self.plugin_config.job_recovery_max_age_hours,
            self.plugin_config.job_history_size,
        )
        if self._job_manager is not None and current_queue_settings != previous_queue_settings:
            queue = self._job_manager.snapshot()
            if not queue["queued_jobs"] and not queue["running_jobs"]:
                previous_manager = self._job_manager
                self._job_manager = None
                self._schedule_manager_stop(previous_manager)
            else:
                self.logger.warning(
                    "[mimo-tts] queue runtime setting change is deferred until plugin reload because jobs are active"
                )
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
        signature = (
            self.plugin_config.api_key,
            self.plugin_config.base_url,
            self.plugin_config.model,
            self.plugin_config.output_format,
            self.plugin_config.tts_timeout_seconds,
        )
        if self._mimo_client is not None and self._mimo_client_signature == signature:
            return self._mimo_client
        previous = self._mimo_client
        self._mimo_client = MimoOfficialClient(
            MimoTTSConfig(
                api_key=self.plugin_config.api_key,
                base_url=self.plugin_config.base_url,
                model=self.plugin_config.model,
                output_format=self.plugin_config.output_format,
                timeout=float(self.plugin_config.tts_timeout_seconds),
                max_retries=0,
            )
        )
        self._mimo_client_signature = signature
        if previous is not None:
            if self._client_users.get(previous, 0):
                self._retired_clients.add(previous)
            else:
                self._schedule_client_close(previous)
        return self._mimo_client

    def _acquire_client(self) -> MimoOfficialClient:
        client = self._client()
        self._client_users[client] = self._client_users.get(client, 0) + 1
        return client

    def _release_client(self, client: MimoOfficialClient) -> None:
        remaining = self._client_users.get(client, 1) - 1
        if remaining > 0:
            self._client_users[client] = remaining
            return
        self._client_users.pop(client, None)
        if client in self._retired_clients:
            self._retired_clients.discard(client)
            self._schedule_client_close(client)

    def _schedule_client_close(self, client: MimoOfficialClient) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._retired_clients.add(client)
            return
        task = loop.create_task(client.close(), name="mimo-tts-client-close")
        self._client_close_tasks.add(task)
        task.add_done_callback(self._client_close_tasks.discard)

    def _schedule_manager_stop(self, manager: TTSJobManager) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(manager.stop(drain_timeout=30.0), name="mimo-tts-queue-reconfigure")
        self._client_close_tasks.add(task)
        task.add_done_callback(self._client_close_tasks.discard)

    async def _voice_data_url(self, voice: VoiceProfile) -> str:
        if voice.type == "builtin":
            return voice.builtin_voice
        if voice.type == "design":
            return ""
        path = pathlib.Path(voice.audio_path).resolve()
        stat = await asyncio.to_thread(path.stat)
        key = (str(path), stat.st_size, stat.st_mtime_ns)
        cached = self._voice_data_cache.get(key)
        if cached is not None:
            self._voice_data_cache.move_to_end(key)
            return cached
        encoded = await asyncio.to_thread(
            encode_voice_file_data_url,
            path,
            max_bytes=self.plugin_config.max_voice_file_bytes,
            max_base64_chars=estimate_base64_chars(self.plugin_config.max_voice_file_bytes),
        )
        self._voice_data_cache[key] = encoded
        self._voice_data_cache.move_to_end(key)
        while len(self._voice_data_cache) > 8:
            self._voice_data_cache.popitem(last=False)
        return encoded

    async def _synthesize_text_to_file(
        self,
        text: str,
        voice: VoiceProfile,
        *,
        context: str = "",
        voice_data_url: str | None = None,
        output_path: pathlib.Path | None = None,
    ) -> pathlib.Path:
        assistant_text = text.strip()
        if voice.style_tags.strip():
            assistant_text = f"{voice.style_tags.strip()} {assistant_text}".strip()
        if len(assistant_text) > self.plugin_config.max_text_chars:
            raise RuntimeError(f"文本过长，最大 {self.plugin_config.max_text_chars} 字")

        voice_data_url = voice_data_url or await self._voice_data_url(voice)
        model_args = {"model": voice.model} if getattr(voice, "type", "clone") != "clone" else {}
        if getattr(voice, "type", "clone") == "design":
            context = f"声音描述：{voice.design_prompt}\n演绎要求：{context}"
        output_dir = pathlib.Path(self.data_dir) / "outputs"
        output_path = output_path or output_dir / f"mimo_tts_{time.time_ns()}.wav"
        async with self._tts_sem:
            client = self._acquire_client()
            try:
                result = await self._reliability.execute(
                    lambda: client.synthesize_to_file(
                        text=assistant_text,
                        voice_data_url=voice_data_url,
                        output_path=output_path,
                        context=context or self.plugin_config.default_context,
                        **model_args,
                    ),
                    retryable=lambda exc: isinstance(
                        exc, (MimoRateLimitError, MimoTransientError)
                    ),
                )
            finally:
                self._release_client(client)
        return result

    def _cleanup_outputs(self) -> None:
        output_dir = pathlib.Path(self.data_dir) / "outputs"
        if not output_dir.is_dir():
            return
        protected = (
            self._job_manager.protected_output_paths()
            if self._job_manager is not None
            else set()
        )
        files = [
            path
            for path in output_dir.glob("mimo_tts_*.wav")
            if path.is_file()
            and _FINAL_OUTPUT_RE.fullmatch(path.name)
            and path.resolve() not in protected
        ]
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

        protected_dirs = {
            path.parent.resolve()
            for path in protected
            if path.name == "manifest.json"
        }
        if self._job_manager is not None:
            protected_dirs.update(
                (output_dir / f"mimo_tts_job_{hashlib.sha256(key.encode()).hexdigest()[:24]}").resolve()
                for key in self._job_manager.recoverable_job_ids()
            )
        bundle_dirs = [
            path
            for path in output_dir.glob("mimo_tts_job_*")
            if path.is_dir() and path.resolve() not in protected_dirs
        ]
        if retention_days > 0:
            cutoff = now - retention_days * 86400
            for path in bundle_dirs:
                try:
                    if path.stat().st_mtime < cutoff:
                        shutil.rmtree(path, ignore_errors=True)
                except OSError:
                    pass
            bundle_dirs = [path for path in bundle_dirs if path.exists()]
        if max_files > 0 and len(bundle_dirs) > max_files:
            bundle_dirs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
            for path in bundle_dirs[max_files:]:
                shutil.rmtree(path, ignore_errors=True)

        if self.plugin_config.audio_transport == "shared_path":
            shared_root = pathlib.Path(self.plugin_config.shared_path_linux).expanduser()
            if shared_root.is_dir() and retention_days > 0:
                cutoff = now - retention_days * 86400
                for path in shared_root.glob("mimo_tts_transport_*"):
                    if not path.is_file():
                        continue
                    try:
                        if path.stat().st_mtime < cutoff:
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
            return self._usable_voice(voice.id)
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
        style_director_enabled: bool | None = None,
        mode: str = "speech",
        voice_profile: VoiceProfile | None = None,
    ) -> pathlib.Path:
        """Synthesize one complete WAV, merging internal segments when needed."""
        started = time.monotonic()
        voice, tts_result, segments, voice_data_url = await self._prepare_synthesis(
            text,
            voice_id=voice_id,
            voice_name=voice_name,
            emotion=emotion,
            context=context,
            user_id=user_id,
            group_id=group_id,
            split=split,
            style_director_enabled=style_director_enabled,
            mode=mode,
            voice_profile=voice_profile,
        )

        output_dir = pathlib.Path(self.data_dir) / "outputs"
        operation_id = time.time_ns()
        final_path = output_dir / f"mimo_tts_{operation_id}.wav"
        part_paths = [
            output_dir / f"mimo_tts_{operation_id}.part{index:03d}.wav"
            for index in range(len(segments))
        ]
        try:
            for segment, part_path in zip(segments, part_paths, strict=True):
                await self._synthesize_text_to_file(
                    segment,
                    voice,
                    context=tts_result.context,
                    voice_data_url=voice_data_url,
                    output_path=part_path,
                )
            if len(part_paths) == 1:
                await asyncio.to_thread(part_paths[0].replace, final_path)
            else:
                await asyncio.to_thread(merge_wav_files, part_paths, final_path)
            await asyncio.to_thread(self._cleanup_outputs)
            self.studio_store.add_history(
                text=text, mode=mode, voice_id=voice.id, voice_name=voice.name,
                voice_type=voice.type, model=voice.model, context=tts_result.context,
                speech_text=tts_result.speech_text, file=final_path.name,
                elapsed_ms=round((time.monotonic() - started) * 1000),
            )
            return final_path
        except Exception:
            final_path.unlink(missing_ok=True)
            final_path.with_suffix(final_path.suffix + ".part").unlink(missing_ok=True)
            raise
        finally:
            for part_path in part_paths:
                part_path.unlink(missing_ok=True)
                part_path.with_suffix(part_path.suffix + ".part").unlink(missing_ok=True)

    async def _prepare_synthesis(
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
        max_segment_chars: int | None = None,
        style_director_enabled: bool | None = None,
        mode: str = "speech",
        voice_profile: VoiceProfile | None = None,
    ) -> tuple[VoiceProfile, TTSContextResult, list[str], str]:
        if mode not in {"speech", "sing"}:
            raise ValueError("不支持的生成模式")
        cleaned = text.strip() if mode == "sing" else clean_tts_text(text)
        if not cleaned:
            raise RuntimeError("请输入要合成的文本")
        resolved_emotion = self._resolve_emotion(cleaned, emotion)
        selector = voice_id or voice_name
        active_snapshot = _JOB_SYNTHESIS_CONFIG.get()
        session_options = (
            {} if active_snapshot is not None and active_snapshot[0] is self
            else self.studio_store.settings("sessions").get(group_id, {})
        )
        if not selector:
            selector = session_options.get("voice_id") or None
        if style_director_enabled is None and "director" in session_options:
            style_director_enabled = session_options["director"]
        if not context:
            style = self.studio_store.settings("styles").get(session_options.get("style_id"), {})
            context = str(style.get("context") or "")
        voice = voice_profile or self._select_voice(
            selector,
            user_id=user_id,
            group_id=group_id,
            emotion=resolved_emotion,
        )
        if voice is None:
            raise RuntimeError("暂无可用音色，请先在插件 Pages 中上传参考音频。")
        if not voice.enabled or (voice.type == "clone" and not voice.consent_confirmed):
            raise ValueError("音色未启用或未确认授权")
        if not voice_profile and selector and voice.id != selector and voice.name != selector:
            raise ValueError(f"指定音色不可用：{selector}")
        if mode == "sing":
            if voice.type != "builtin":
                raise ValueError("唱歌仅支持预置音色")
            voice = replace(voice, style_tags="")
            cleaned = singing_text(cleaned)
            split = False
            style_director_enabled = False
        tts_result = await self._build_tts_context(
            voice,
            resolved_emotion,
            context,
            text=cleaned,
            style_director_enabled=style_director_enabled,
        )
        final_text = tts_result.speech_text or cleaned
        style_prefix_chars = len(voice.style_tags.strip()) + 1 if voice.style_tags.strip() else 0
        segment_limit = self.plugin_config.max_text_chars - style_prefix_chars
        if segment_limit < 1:
            raise RuntimeError("音色风格标签超过 MiMo 单次请求文本上限。")
        if max_segment_chars is not None:
            segment_limit = min(segment_limit, max(1, int(max_segment_chars)))
        segments = self._split_for_tts(final_text, max_chars=segment_limit) if split else [final_text]
        if not segments:
            raise RuntimeError("没有可合成的文本。")
        for segment in segments:
            assistant_length = len(segment) + (len(voice.style_tags.strip()) + 1 if voice.style_tags.strip() else 0)
            if assistant_length > self.plugin_config.max_text_chars:
                raise RuntimeError(
                    f"文本分段超过 MiMo 单次请求上限 {self.plugin_config.max_text_chars} 字。"
                )
        voice_data_url = await self._voice_data_url(voice)
        return voice, tts_result, segments, voice_data_url

    async def _synthesize_delivery_bundle(self, job: TTSJob) -> pathlib.Path:
        started = time.monotonic()
        output_dir = pathlib.Path(self.data_dir) / "outputs"
        bundle_dir = output_dir / f"mimo_tts_job_{hashlib.sha256(job.id.encode()).hexdigest()[:24]}"
        plan_path = bundle_dir / "generation.json"
        if plan_path.is_file():
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            voice = VoiceProfile.from_dict(plan["voice"])
            segments = plan["segments"]
            if not isinstance(segments, list) or not segments or not all(isinstance(s, str) for s in segments):
                raise RuntimeError("分段生成检查点无效")
            tts_result = TTSContextResult(context=plan["context"], speech_text=plan["speech_text"])
            voice_data_url = await self._voice_data_url(voice)
        else:
            voice, tts_result, segments, voice_data_url = await self._prepare_synthesis(
            job.text,
            voice_name=job.voice or None,
            emotion=job.emotion or None,
            context=job.context,
            user_id=job.user_id,
            group_id=job.group_id,
            max_segment_chars=self.plugin_config.delivery_segment_chars,
            voice_profile=VoiceProfile.from_dict(job.voice_snapshot) if job.voice_snapshot else None,
            style_director_enabled=job.director_enabled,
            mode=job.mode,
            )
            bundle_dir.mkdir(parents=True, exist_ok=True)
            temporary_plan = plan_path.with_suffix(".json.tmp")
            temporary_plan.write_text(json.dumps({
                "voice": voice.to_dict(), "segments": segments,
                "context": tts_result.context, "speech_text": tts_result.speech_text,
            }, ensure_ascii=False), encoding="utf-8")
            temporary_plan.replace(plan_path)
        part_paths = [bundle_dir / f"part{index:03d}.wav" for index in range(len(segments))]
        tasks = [
            asyncio.create_task(
                self._synthesize_text_to_file(
                    segment,
                    voice,
                    context=tts_result.context,
                    voice_data_url=voice_data_url,
                    output_path=part_path,
                ),
                name=f"mimo-tts-segment-{job.id}-{index}",
            )
            for index, (segment, part_path) in enumerate(zip(segments, part_paths, strict=True))
            if not part_path.is_file()
        ]
        try:
            await asyncio.gather(*tasks)
            manifest = bundle_dir / "manifest.json"
            temporary = bundle_dir / "manifest.json.tmp"
            payload = {
                "kind": "mimo_tts_delivery_v1",
                "segments": [path.name for path in part_paths],
                "delivered_segments": 0,
            }
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temporary.replace(manifest)
            history_path = output_dir / f"mimo_tts_history_{job.id}_{time.time_ns()}.wav"
            try:
                await asyncio.to_thread(merge_wav_files, part_paths, history_path)
                self.studio_store.add_history(
                    text=job.text, mode=job.mode, voice_id=voice.id, voice_name=voice.name,
                    voice_type=voice.type, model=voice.model, context=tts_result.context,
                    speech_text=tts_result.speech_text, file=history_path.name,
                    elapsed_ms=round((time.monotonic() - started) * 1000),
                    job_id=job.id, source=job.source, session=job.session,
                )
            except Exception as exc:
                history_path.unlink(missing_ok=True)
                self.logger.warning("[mimo-tts] history recording failed: %s", exc)
            return manifest
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

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
        output = await self.synthesize_text(
            text,
            voice_name=voice_name or voice or None,
            emotion=emotion or None,
            context=context,
            group_id=group_id,
        )
        return str(output)

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
            """Speak text to the user in your own MiMo TTS voice.

            The voice message is your own speech: the user hears you talking, not a
            generated file. The audio is delivered to the user directly — do not
            resend it via send_message_to_user. Afterwards, continue the
            conversation naturally; do not review, quote, or describe the voice
            message. If delivery fails, tell the user plainly that the voice did
            not go through; never name internal software, protocols, or file
            details.

            Args:
                text(string): What you want to say out loud.
                emotion(string): Optional emotion, one of happy, sad, angry, neutral.
                voice(string): Optional voice name or voice id.
                style(string): Optional temporary style instruction.

            Returns:
                string: Short delivery confirmation, a brief notice that the
                voice did not reach the user, or a note that there was nothing
                to say.
            """
            content = str(text or "").strip()
            if not content:
                yield "文本为空，没有可说的话"
                return
            try:
                output = await self.synthesize_text(
                    content,
                    voice_name=str(voice or "").strip() or None,
                    emotion=emotion,
                    context=style,
                    user_id=str(getattr(event, "get_sender_id", lambda: "")() or "").strip(),
                    group_id=str(getattr(event, "unified_msg_origin", "") or "").strip()
                    or self._conversation_id(event),
                )
                await self._send_audio_result(event, output)
            except Exception as exc:
                self.logger.warning("[mimo-tts] mimo_tts_speak failed: %s", exc)
                yield "语音没能送达用户"
                return
            if hasattr(event, "clear_result"):
                event.clear_result()
            yield "已用你的声音把这句话说给用户，语音已送达"
            return

    def _conversation_id(self, event: AstrMessageEvent) -> str:
        origin = str(getattr(event, "unified_msg_origin", "") or "").strip()
        try:
            provider_request = event.get_extra("provider_request")
            conversation = getattr(provider_request, "conversation", None)
            legacy = str(getattr(conversation, "cid", "") or "").strip()
        except Exception:
            legacy = ""
        if origin and legacy and origin != legacy:
            bindings = self.voice_store.defaults().get("group_defaults", {})
            if legacy in bindings and origin not in bindings:
                self.voice_store.set_group_default(origin, bindings[legacy])
        return origin or legacy

    def _chat_scope(self, event: AstrMessageEvent) -> tuple[str, str]:
        conversation_id = self._conversation_id(event)
        origin = str(getattr(event, "unified_msg_origin", "") or "").strip()
        if "FriendMessage" in origin or "FriendMessage" in conversation_id:
            return "private", origin or conversation_id
        if "GroupMessage" in origin or "GroupMessage" in conversation_id:
            return "group", origin or conversation_id
        return "unknown", origin or conversation_id

    @staticmethod
    def _matches_scope(target: str, candidate: str) -> bool:
        target = str(target or "").strip()
        candidate = str(candidate or "").strip()
        if not target or not candidate:
            return False
        return target == candidate or target in candidate or candidate in target

    def _auto_tts_access_preview(self) -> dict[str, Any]:
        group_whitelist = list(self.plugin_config.auto_tts_group_whitelist)
        group_blacklist = list(self.plugin_config.auto_tts_group_blacklist)
        private_whitelist = list(self.plugin_config.auto_tts_private_whitelist)
        private_blacklist = list(self.plugin_config.auto_tts_private_blacklist)
        admins = list(self.plugin_config.admin_users)

        def describe_scope(whitelist: list[str], blacklist: list[str], scope_name: str) -> str:
            if whitelist and blacklist:
                return (
                    f"白名单 {len(whitelist)} 条，黑名单 {len(blacklist)} 条，"
                    "黑名单优先拦截，未命中黑名单后需命中白名单才放行"
                )
            if whitelist:
                return f"白名单 {len(whitelist)} 条，未设置黑名单，仅命中后放行"
            if blacklist:
                return f"未设置白名单，黑名单 {len(blacklist)} 条，默认放行，命中即拦截"
            return f"未设置{scope_name}名单，默认放行"

        return {
            "admins": {
                "count": len(admins),
                "detail": f"{len(admins)} 个管理员，始终放行" if admins else "未配置管理员",
            },
            "group": {
                "whitelist_count": len(group_whitelist),
                "blacklist_count": len(group_blacklist),
                "detail": describe_scope(group_whitelist, group_blacklist, "群聊"),
            },
            "private": {
                "whitelist_count": len(private_whitelist),
                "blacklist_count": len(private_blacklist),
                "detail": describe_scope(private_whitelist, private_blacklist, "私聊"),
            },
            "summary": "管理员始终放行；黑名单优先于白名单；白名单留空表示不限制。",
        }

    def _auto_tts_access_decision(self, event: AstrMessageEvent) -> dict[str, Any]:
        user_id = str(event.get_sender_id() or "").strip()
        if self._is_admin(event):
            return {
                "allowed": True,
                "reason": f"admin bypass: {clip_log_text(user_id)}",
                "scope": "admin",
                "scope_id": user_id,
                "matched_rule": "admin",
            }

        scope, scope_id = self._chat_scope(event)
        if scope not in {"group", "private"}:
            return {
                "allowed": True,
                "reason": f"scope bypass: {clip_log_text(scope or 'unknown')}",
                "scope": scope,
                "scope_id": scope_id,
                "matched_rule": "scope",
            }

        whitelist = (
            self.plugin_config.auto_tts_group_whitelist
            if scope == "group"
            else self.plugin_config.auto_tts_private_whitelist
        )
        blacklist = (
            self.plugin_config.auto_tts_group_blacklist
            if scope == "group"
            else self.plugin_config.auto_tts_private_blacklist
        )

        for item in blacklist:
            if self._matches_scope(scope_id, item):
                return {
                    "allowed": False,
                    "reason": f"{scope} blacklist matched: {clip_log_text(item)}",
                    "scope": scope,
                    "scope_id": scope_id,
                    "matched_rule": item,
                }

        if whitelist:
            for item in whitelist:
                if self._matches_scope(scope_id, item):
                    return {
                        "allowed": True,
                        "reason": f"{scope} whitelist matched: {clip_log_text(item)}",
                        "scope": scope,
                        "scope_id": scope_id,
                        "matched_rule": item,
                    }
            return {
                "allowed": False,
                "reason": f"{scope} whitelist missed: {clip_log_text(scope_id)}",
                "scope": scope,
                "scope_id": scope_id,
                "matched_rule": "",
            }

        return {
            "allowed": True,
            "reason": f"{scope} unrestricted: {clip_log_text(scope_id)}",
            "scope": scope,
            "scope_id": scope_id,
            "matched_rule": "",
        }

    def _scope_allowed(self, event: AstrMessageEvent) -> bool:
        return bool(self._auto_tts_access_decision(event)["allowed"])

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
    def _parse_command_options(raw: str, aliases: dict[str, str]) -> tuple[dict[str, str], str]:
        values = {}
        remaining = raw.strip()
        while remaining:
            token = remaining.split(maxsplit=1)[0]
            if token == "--":
                return values, remaining[2:].lstrip()
            if token not in aliases:
                if token.startswith("-"):
                    raise ValueError(f"未知参数：{token}；正文以 - 开头时请先加 --")
                break
            remaining = remaining[len(token):].lstrip()
            if not remaining or remaining.split(maxsplit=1)[0] in aliases:
                raise ValueError(f"{token} 缺少参数值")
            if remaining[0] in {"'", '"'}:
                match = re.match(r"""("(?:\\.|[^"\\])*"|'[^']*')(?=\s|$)""", remaining)
                if not match:
                    raise ValueError(f"{token} 的引号未闭合")
                value = shlex.split(match[0])[0]
                remaining = remaining[match.end():].lstrip()
            else:
                value = remaining.split(maxsplit=1)[0]
                remaining = remaining[len(value):].lstrip()
            values[aliases[token]] = value
        return values, remaining

    @classmethod
    def _parse_tts_args(cls, raw: str) -> tuple[str | None, str | None, str, str]:
        values, text = cls._parse_command_options(raw, {
            "-v": "voice", "--voice": "voice", "-e": "emotion", "--emotion": "emotion",
            "-c": "context", "--context": "context",
        })
        emotion = values.get("emotion")
        if emotion and normalize_emotion(emotion) not in SUPPORTED_EMOTIONS:
            raise ValueError("情绪只支持：happy, sad, angry, neutral")
        return values.get("voice"), emotion, text, values.get("context", "")

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        user_id = str(event.get_sender_id() or "").strip()
        return user_id in set(self.plugin_config.admin_users)

    def _resolve_emotion(self, text: str, requested: str | None) -> str:
        if not self.plugin_config.emotion_routing_enabled:
            return normalize_emotion(requested) or "neutral"
        return self.emotion_router.resolve(text, requested=requested)

    async def _build_tts_context(
        self,
        voice: VoiceProfile,
        emotion: str,
        command_context: str,
        *,
        text: str = "",
        style_director_enabled: bool | None = None,
    ) -> TTSContextResult:
        base_context = EmotionRouter(emotion_contexts=self.plugin_config.emotion_contexts).build_context(
            base_context=self.plugin_config.default_context,
            emotion=emotion,
            voice_context=voice.style_context,
            command_context=command_context,
        )
        use_style_director = (
            self.plugin_config.ai_style_director_enabled
            if style_director_enabled is None
            else bool(style_director_enabled)
        )
        text_mode = self.plugin_config.director_text_mode
        if not use_style_director or text_mode == "off":
            return TTSContextResult(context=base_context, speech_text=text)

        cache_key = build_style_director_cache_key(
            voice_id=voice.id,
            emotion=emotion,
            text=text,
            optimize_text=self.plugin_config.ai_style_director_optimize_text,
            command_context=command_context,
            voice_context=voice.style_context,
            voice_description=f"{voice.model}:{voice.design_prompt}:{voice.description}",
            provider_id=self.plugin_config.ai_style_director_provider_id,
            prompt=self.plugin_config.ai_style_director_prompt,
            mode=f"{self.plugin_config.ai_style_director_mode}:{text_mode}:{self.plugin_config.ai_style_director_max_chars}",
        )
        cache_entry = self._style_director_cache.get(cache_key)
        if cache_entry and time.monotonic() - cache_entry[0] <= 600:
            cached = cache_entry[1]
            self._style_director_cache.move_to_end(cache_key)
            result = TTSContextResult(
                context=self._merge_directed_context(base_context, cached.style_context),
                speech_text=cached.speech_text or text,
                style_context=cached.style_context,
                cached=True,
            )
            self._log_style_director_plan(
                voice=voice,
                emotion=emotion,
                text=text,
                style_context=result.style_context,
                speech_text=result.speech_text,
                cached=True,
            )
            return result
        if cache_entry:
            self._style_director_cache.pop(cache_key, None)

        directive = ""
        speech_text = text
        try:
            plan = await generate_style_plan(
                self.context,
                StyleDirectorInput(
                    text=text,
                    emotion=emotion,
                    voice_name=voice.name,
                    voice_description=voice.description,
                    voice_style_context=voice.style_context,
                    existing_context=command_context or base_context,
                    max_chars=self.plugin_config.ai_style_director_max_chars,
                    optimize_speech_text=self.plugin_config.ai_style_director_optimize_text,
                    max_speech_chars=self.plugin_config.max_text_chars,
                    text_mode=text_mode,
                ),
                template=self.plugin_config.ai_style_director_prompt,
                provider_id=self.plugin_config.ai_style_director_provider_id,
            )
            directive = plan.style_context
            speech_text = protect_speech_text(text, plan.speech_text, text_mode)
        except Exception as exc:
            error_type, error_message = self._style_director_error_summary(exc)
            fallback_enabled = self.plugin_config.ai_style_director_fallback_to_emotion
            self.logger.warning(
                "[mimo-tts] style director failed: provider=%s voice=%s(%s) emotion=%s text=%s error_type=%s error=%s fallback=%s",
                self.plugin_config.ai_style_director_provider_id or "default",
                clip_log_text(voice.name),
                clip_log_text(voice.id),
                clip_log_text(emotion or "neutral"),
                clip_log_text(text),
                error_type,
                clip_log_text(error_message),
                str(bool(fallback_enabled)).lower(),
            )
            if not self.plugin_config.ai_style_director_fallback_to_emotion:
                raise RuntimeError(f"AI 风格导演失败：{error_type}: {error_message}") from exc

        if directive:
            result = TTSContextResult(
                context=self._merge_directed_context(base_context, directive),
                speech_text=speech_text,
                style_context=directive,
            )
            self._style_director_cache[cache_key] = (time.monotonic(), result)
            self._style_director_cache.move_to_end(cache_key)
            while len(self._style_director_cache) > 128:
                self._style_director_cache.popitem(last=False)
            self._log_style_director_plan(
                voice=voice,
                emotion=emotion,
                text=text,
                style_context=result.style_context,
                speech_text=result.speech_text,
                cached=False,
            )
            return result
        return TTSContextResult(context=base_context, speech_text=speech_text)

    def _log_style_director_plan(
        self,
        *,
        voice: VoiceProfile,
        emotion: str,
        text: str,
        style_context: str,
        speech_text: str,
        cached: bool,
    ) -> None:
        if not self.plugin_config.ai_style_director_debug_log:
            return
        self.logger.info(
            "[mimo-tts] AI导演 provider=%s voice=%s(%s) emotion=%s cached=%s text=%s style_context=%s speech_text=%s",
            self.plugin_config.ai_style_director_provider_id or "default",
            clip_log_text(voice.name),
            clip_log_text(voice.id),
            clip_log_text(emotion or "neutral"),
            str(bool(cached)).lower(),
            clip_log_text(text),
            clip_log_text(style_context),
            clip_log_text(speech_text),
        )

    @staticmethod
    def _style_director_error_summary(exc: Exception) -> tuple[str, str]:
        error_type = type(exc).__name__
        message = str(exc).strip()
        if not message:
            message = "empty exception message"
        if isinstance(exc, TimeoutError):
            message = "timeout while waiting for AstrBot AI provider"
        return error_type, message

    def _merge_directed_context(self, base_context: str, directive: str) -> str:
        return merge_directed_context(
            base_context,
            directive,
            self.plugin_config.ai_style_director_mode,
        )

    def _split_for_tts(self, text: str, *, max_chars: int | None = None) -> list[str]:
        if not self.plugin_config.segment_enabled:
            return [text]
        limit = min(
            self.plugin_config.segment_threshold_chars,
            max_chars or self.plugin_config.max_text_chars,
        )
        if len(text) <= limit:
            return [text]
        return split_tts_text(
            text,
            max_chars=limit,
            max_segments=self.plugin_config.segment_max_segments,
        )

    def _audio_component(self, audio_path: pathlib.Path, umo: str = ""):
        if self.plugin_config.audio_transport == "shared_path":
            self._ensure_shared_path_platform(umo)
        source, _staged = self._transport_source(audio_path)
        if Record is not None:
            try:
                return Record(file=source)
            except Exception as exc:
                self.logger.warning(
                    "[mimo-tts] failed to build Record payload, fallback to file: %s", exc
                )
        if self.plugin_config.file_fallback_enabled:
            return File(name=audio_path.name, file=source)
        return None

    def _audio_source(self, audio_path: pathlib.Path) -> str:
        source, _staged = self._transport_source(audio_path)
        return source

    def _transport_source(self, audio_path: pathlib.Path) -> tuple[str, pathlib.Path | None]:
        """Return the platform-visible source and an optional temporary staged file."""
        if self.plugin_config.audio_transport == "path":
            return str(audio_path), None
        if self.plugin_config.audio_transport == "shared_path":
            return self._stage_shared_audio(audio_path)
        size = audio_path.stat().st_size
        if size > self.plugin_config.base64_max_bytes:
            raise RuntimeError(
                f"音频 {audio_path.name} 大小 {size / 1024 / 1024:.1f} MB，"
                f"超过 Base64 上限 {self.plugin_config.base64_max_mb} MB。"
            )
        payload = base64.b64encode(audio_path.read_bytes()).decode("ascii")
        return f"base64://{payload}", None

    def _stage_shared_audio(
        self,
        audio_path: pathlib.Path,
    ) -> tuple[str, pathlib.Path]:
        source = audio_path.resolve()
        if not source.is_file():
            raise RuntimeError(f"音频文件不存在：{source}")
        linux_root = pathlib.Path(self.plugin_config.shared_path_linux).expanduser()
        windows_root = self.plugin_config.shared_path_windows
        if not str(linux_root) or not windows_root:
            raise RuntimeError("shared_path 模式需要同时配置 Linux 和 Windows 共享目录。")
        try:
            linux_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(f"无法创建 Linux 共享目录：{linux_root} ({exc})") from exc

        try:
            relative = source.relative_to(linux_root.resolve())
            staged = source
        except ValueError:
            staged = linux_root / f"mimo_tts_transport_{time.time_ns()}_{source.name}"
            temporary = staged.with_suffix(staged.suffix + ".tmp")
            try:
                shutil.copy2(source, temporary)
                temporary.replace(staged)
            except OSError as exc:
                temporary.unlink(missing_ok=True)
                raise RuntimeError(
                    f"无法将音频复制到 Linux 共享目录：{linux_root} ({exc})"
                ) from exc
            relative = staged.resolve().relative_to(linux_root.resolve())

        windows_path = str(PureWindowsPath(windows_root) / PureWindowsPath(relative.as_posix()))
        return windows_path, staged

    @staticmethod
    def _delivery_audio_paths(output: pathlib.Path) -> list[pathlib.Path]:
        if output.name != "manifest.json":
            return [output]
        data = json.loads(output.read_text(encoding="utf-8"))
        if data.get("kind") != "mimo_tts_delivery_v1":
            raise RuntimeError("后台语音分段清单格式无效。")
        parent = output.parent.resolve()
        paths: list[pathlib.Path] = []
        for name in data.get("segments") or []:
            path = (output.parent / str(name)).resolve()
            if path.parent != parent or not path.is_file():
                raise RuntimeError("后台语音分段文件缺失或路径无效。")
            paths.append(path)
        if not paths:
            raise RuntimeError("后台语音分段清单为空。")
        return paths

    @staticmethod
    def _delete_delivery_output(output: pathlib.Path, paths: list[pathlib.Path]) -> None:
        if output.name == "manifest.json":
            shutil.rmtree(output.parent, ignore_errors=True)
            return
        for path in paths:
            path.unlink(missing_ok=True)
            path.with_suffix(".delivery.json").unlink(missing_ok=True)

    @staticmethod
    def _is_plain_component(component: Any) -> bool:
        if Plain is not None and isinstance(component, Plain):
            return True
        return component.__class__.__name__ == "Plain"

    @staticmethod
    def _parse_aiocqhttp_session(session: str) -> tuple[bool, int]:
        """Parse an AstrBot aiocqhttp UMO into message type and numeric ID."""
        parts = str(session or "").split(":", 2)
        if len(parts) != 3 or not parts[0].strip():
            raise RuntimeError("shared_path 无法解析目标会话。")
        message_type = parts[1]
        if message_type not in {"GroupMessage", "FriendMessage"}:
            raise RuntimeError(f"shared_path 不支持会话类型：{message_type}")
        raw_id = parts[2].strip()
        if not raw_id.isdigit():
            raise RuntimeError(f"shared_path 目标 ID 无效：{raw_id}")
        return message_type == "GroupMessage", int(raw_id)

    def _find_aiocqhttp_client(self, session: str) -> Any:
        """Find the aiocqhttp bot used by a background session."""
        manager = getattr(self.context, "platform_manager", None)
        platforms = []
        get_insts = getattr(manager, "get_insts", None)
        if callable(get_insts):
            try:
                platforms = list(get_insts() or [])
            except Exception:
                platforms = []
        if not platforms:
            platforms = list(getattr(manager, "platform_insts", []) or [])
        platform_id = str(session or "").split(":", 1)[0]
        for platform in platforms:
            try:
                meta = platform.meta()
                identifiers = {
                    str(getattr(meta, "id", "") or ""),
                    str(getattr(meta, "name", "") or ""),
                }
                if platform_id not in identifiers:
                    continue
            except Exception:
                continue
            get_client = getattr(platform, "get_client", None)
            client = get_client() if callable(get_client) else getattr(platform, "bot", None)
            if client is not None:
                return client
        raise RuntimeError(f"找不到 aiocqhttp 平台客户端：{platform_id or session}")

    @staticmethod
    async def _call_onebot_message(
        bot: Any,
        *,
        is_group: bool,
        target_id: int,
        source: str,
        self_id: Any = None,
    ) -> None:
        """Send a raw record segment without AstrBot File/Record conversion."""
        action = "send_group_msg" if is_group else "send_private_msg"
        params = {
            "group_id" if is_group else "user_id": target_id,
            "message": [{"type": "record", "data": {"file": source}}],
        }
        if self_id not in (None, ""):
            params["self_id"] = self_id
        method = getattr(bot, action, None)
        if callable(method):
            await method(**params)
            return
        call_action = getattr(bot, "call_action", None)
        if callable(call_action):
            await call_action(action, **params)
            return
        raise RuntimeError("aiocqhttp 客户端不支持 OneBot 消息发送接口。")

    def _ensure_shared_path_platform(self, umo: str) -> None:
        """Reject shared_path for platforms that cannot consume raw OneBot paths.

        Args:
            umo: Unified message origin or session string carrying the platform id.

        Raises:
            RuntimeError: When the resolved platform is not aiocqhttp.
        """
        # shared_path is only consumable through the aiocqhttp OneBot client; fail
        # early with the supported alternatives instead of a numeric-ID parse error.
        platform_id = str(umo or "").split(":", 1)[0].strip()
        matches = [
            item
            for item in self._platform_capabilities()
            if platform_id and platform_id in {item["id"], item["name"]}
        ]
        if matches and not any(item["name"] == "aiocqhttp" for item in matches):
            raise RuntimeError(
                f"shared_path 模式仅支持 aiocqhttp 平台，当前平台为 {platform_id}；"
                "请改用 audio_transport=base64 或 path。"
            )

    async def _send_shared_path_audio(
        self,
        *,
        source: str,
        event: AstrMessageEvent | None = None,
        session: str = "",
    ) -> None:
        """Send a shared Windows path directly through the OneBot client."""
        self._ensure_shared_path_platform(
            str(getattr(event, "unified_msg_origin", "") or "")
            if event is not None
            else str(session or "")
        )
        if event is not None:
            bot = getattr(event, "bot", None)
            if bot is None:
                raise RuntimeError("当前事件没有可用的 aiocqhttp 底层客户端。")
            get_group_id = getattr(event, "get_group_id", None)
            group_id = str(get_group_id() or "") if callable(get_group_id) else ""
            get_sender_id = getattr(event, "get_sender_id", None)
            sender_id = str(get_sender_id() or "") if callable(get_sender_id) else ""
            raw_message = getattr(getattr(event, "message_obj", None), "raw_message", None)
            self_id = raw_message.get("self_id") if isinstance(raw_message, dict) else None
            if group_id.isdigit():
                await self._call_onebot_message(
                    bot,
                    is_group=True,
                    target_id=int(group_id),
                    source=source,
                    self_id=self_id,
                )
                return
            if sender_id.isdigit():
                await self._call_onebot_message(
                    bot,
                    is_group=False,
                    target_id=int(sender_id),
                    source=source,
                    self_id=self_id,
                )
                return
            raise RuntimeError("当前事件缺少有效的群号或用户号。")

        is_group, target_id = self._parse_aiocqhttp_session(session)
        bot = self._find_aiocqhttp_client(session)
        await self._call_onebot_message(
            bot,
            is_group=is_group,
            target_id=target_id,
            source=source,
        )

    async def _send_audio_result(self, event: AstrMessageEvent, audio_path: pathlib.Path) -> None:
        source, _staged = self._transport_source(audio_path)
        if self.plugin_config.audio_transport == "shared_path":
            await self._send_shared_path_audio(source=source, event=event)
            return
        if Record is not None:
            try:
                await event.send(event.chain_result([Record(file=source)]))
                return
            except Exception as exc:
                self.logger.warning(
                    "[mimo-tts] Record send failed, fallback to file: %s", exc
                )
        if self.plugin_config.audio_transport == "base64":
            raise RuntimeError("Base64 语音未能发送，已禁止回退为本地路径（audio_transport=base64）。")
        if not self.plugin_config.file_fallback_enabled:
            raise RuntimeError("语音未能发送，且 file_fallback_enabled 已禁用文件回退。")
        await event.send(event.chain_result([File(name=audio_path.name, file=source)]))

    def _background_manager(self) -> TTSJobManager:
        if self._job_manager is None:
            persistence_path = (
                pathlib.Path(self.data_dir) / "tts_jobs.json"
                if self.plugin_config.job_persistence_enabled
                else None
            )
            self._job_manager = TTSJobManager(
                processor=self._process_tts_job,
                deliverer=self._deliver_tts_job,
                failure_handler=self._handle_tts_job_failure,
                max_queue_size=self.plugin_config.background_queue_size,
                worker_count=self.plugin_config.max_concurrency,
                persistence_path=persistence_path,
                history_size=self.plugin_config.job_history_size,
                recovery_max_age_seconds=self.plugin_config.job_recovery_max_age_hours * 3600,
            )
        return self._job_manager

    def _queue_snapshot(self) -> dict[str, object]:
        queue = self._job_manager.snapshot() if self._job_manager is not None else {
            "queued_jobs": 0,
            "running_jobs": 0,
            "completed_jobs": 0,
            "failed_jobs": 0,
            "cancelled_jobs": 0,
            "recovered_jobs": 0,
            "dropped_jobs": 0,
            "average_latency_seconds": 0.0,
            "last_error": "",
        }
        persistence_path = pathlib.Path(self.data_dir) / "tts_jobs.json"
        return {
            **queue,
            **self._reliability.snapshot(),
            "persistence_enabled": self.plugin_config.job_persistence_enabled,
            "persistence_path": str(persistence_path),
            "recovery_max_age_hours": self.plugin_config.job_recovery_max_age_hours,
            "audio_cleanup": self.plugin_config.background_audio_cleanup,
            "audio_transport": self.plugin_config.audio_transport,
            "delivery_segment_chars": self.plugin_config.delivery_segment_chars,
            "base64_max_mb": self.plugin_config.base64_max_mb,
            "platform_preflight": self.plugin_config.platform_preflight_enabled,
            "platforms": self._platform_capabilities(),
        }

    def _task_list(self, *, limit: int = 50) -> list[dict[str, Any]]:
        return self._job_manager.list_tasks(limit=limit) if self._job_manager is not None else []

    async def _process_tts_job(self, job: TTSJob) -> pathlib.Path:
        config = replace(self._plugin_config, **{
            key: value for key, value in job.synthesis_snapshot.items() if key in _SYNTHESIS_FIELDS
        })
        token = _JOB_SYNTHESIS_CONFIG.set((self, config))
        try:
            return await self._process_tts_job_snapshot(job)
        finally:
            _JOB_SYNTHESIS_CONFIG.reset(token)

    async def _process_tts_job_snapshot(self, job: TTSJob) -> pathlib.Path:
        if job.mode == "sing":
            return await self.synthesize_text(
                job.text, voice_name=job.voice, context=job.context, mode="sing", split=False,
                voice_profile=VoiceProfile.from_dict(job.voice_snapshot) if job.voice_snapshot else None,
            )
        if self.plugin_config.audio_transport in {"base64", "shared_path"}:
            return await self._synthesize_delivery_bundle(job)
        return await self.synthesize_text(
            job.text,
            voice_name=job.voice or None,
            emotion=job.emotion or None,
            context=job.context,
            user_id=job.user_id,
            group_id=job.group_id,
            voice_profile=VoiceProfile.from_dict(job.voice_snapshot) if job.voice_snapshot else None,
            style_director_enabled=job.director_enabled,
        )

    def _message_chain(self, components: list[Any]):
        if MessageChain is None:
            raise RuntimeError("当前 AstrBot 版本不支持主动消息 MessageChain。")
        return MessageChain(chain=components)

    async def _send_to_session(self, session: str, components: list[Any]) -> None:
        sent = await self.context.send_message(session, self._message_chain(components))
        if sent is False:
            raise RuntimeError("AstrBot 未找到可主动发送该会话的平台。")

    async def _send_audio_path_to_session(
        self,
        session: str,
        audio_path: pathlib.Path,
    ) -> None:
        source, _staged = self._transport_source(audio_path)
        if self.plugin_config.audio_transport == "shared_path":
            await self._send_shared_path_audio(source=source, session=session)
            return
        record_error: Exception | None = None
        if Record is not None:
            try:
                await self._send_to_session(session, [Record(file=source)])
            except Exception as exc:
                record_error = exc
                self.logger.warning(
                    "[mimo-tts] proactive Record send failed, retrying as File: %s", exc
                )
            else:
                return
        else:
            record_error = RuntimeError("Record component unavailable")
        if self.plugin_config.audio_transport == "base64":
            raise record_error
        if not self.plugin_config.file_fallback_enabled:
            raise record_error
        await self._send_to_session(
            session, [File(name=audio_path.name, file=source)]
        )

    def _platform_capabilities(self) -> list[dict[str, Any]]:
        manager = getattr(self.context, "platform_manager", None)
        platforms = list(getattr(manager, "platform_insts", []) or [])
        result = []
        for platform in platforms:
            try:
                meta = platform.meta()
                result.append({
                    "id": str(getattr(meta, "id", "") or ""),
                    "name": str(getattr(meta, "name", "") or ""),
                    "proactive": bool(getattr(meta, "support_proactive_message", True)),
                    "record_component": Record is not None,
                    "file_fallback": self.plugin_config.file_fallback_enabled,
                })
            except Exception:
                continue
        return result

    def _preflight_session(self, session: str) -> None:
        if not self.plugin_config.platform_preflight_enabled:
            return
        platform_id = str(session or "").split(":", 1)[0]
        matches = [
            item for item in self._platform_capabilities()
            if platform_id in {item["id"], item["name"]}
        ]
        if matches and not any(item["proactive"] for item in matches):
            raise RuntimeError(f"平台 {platform_id} 不支持主动消息，无法后台补发语音。")

    async def _deliver_tts_job(self, job: TTSJob, output: pathlib.Path) -> None:
        self._preflight_session(job.session)
        paths = self._delivery_audio_paths(output)
        checkpoint = output if output.name == "manifest.json" else output.with_suffix(".delivery.json")
        manifest = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.exists() else {"delivered_segments": 0}
        delivered = int(manifest.get("delivered_segments", 0))
        if delivered < 0 or delivered > len(paths):
            raise RuntimeError("分段交付进度无效")
        for index, path in enumerate(paths[delivered:], start=delivered):
            await self._send_audio_path_to_session(job.session, path)
            manifest["delivered_segments"] = index + 1
            temporary = checkpoint.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            temporary.replace(checkpoint)
        self.logger.info(
            "[mimo-tts] background audio delivered: job=%s source=%s session=%s segments=%s",
            job.id,
            job.source,
            clip_log_text(job.session),
            len(paths),
        )
        if self.plugin_config.background_audio_cleanup == "after_delivery":
            await asyncio.to_thread(self._delete_delivery_output, output, paths)
        else:
            await asyncio.to_thread(self._cleanup_outputs)

    async def _handle_tts_job_failure(self, job: TTSJob, exc: Exception) -> None:
        self.logger.warning(
            "[mimo-tts] background job failed: job=%s source=%s session=%s error=%s",
            job.id,
            job.source,
            clip_log_text(job.session),
            exc,
        )
        if not job.notify_on_failure or Plain is None:
            return
        await self._send_to_session(job.session, [Plain(f"语音生成失败：{exc}")])

    def _failure_notice_enabled(self, source: str) -> bool:
        mode = self.plugin_config.async_failure_notice
        return mode == "always" or (mode == "command_only" and source == "command")

    async def _submit_background_job(
        self,
        *,
        event: AstrMessageEvent,
        text: str,
        source: str,
        voice: str = "",
        emotion: str = "",
        context: str = "",
    ) -> bool:
        session = str(getattr(event, "unified_msg_origin", "") or "").strip()
        if not session:
            raise RuntimeError("当前消息缺少 unified_msg_origin，无法后台补发语音。")
        self._preflight_session(session)
        self._conversation_id(event)
        options = self.studio_store.settings("sessions").get(session, {})
        if voice and self.voice_store.find_voice(voice) is None:
            raise ValueError(f"指定音色不可用：{voice}")
        chosen = self._select_voice(
            voice or options.get("voice_id"),
            user_id=str(event.get_sender_id() or ""),
            group_id=session, emotion=self._resolve_emotion(text, emotion),
        )
        if chosen is None:
            raise RuntimeError("暂无可用音色")
        style = self.studio_store.settings("styles").get(options.get("style_id"), {})
        job = TTSJob(
            session=session,
            text=text,
            voice=chosen.id,
            emotion=emotion,
            context=context or str(style.get("context") or ""),
            user_id=str(event.get_sender_id() or "").strip(),
            group_id=session,
            source="command" if source == "command" else "auto",
            notify_on_failure=self._failure_notice_enabled(source),
            voice_snapshot=chosen.to_dict(),
            director_enabled=options.get("director", self.plugin_config.ai_style_director_enabled),
            synthesis_snapshot=self._synthesis_snapshot(),
        )
        return await self._background_manager().submit(job)

    def _defer_background_job_until_message_sent(
        self,
        *,
        event: AstrMessageEvent,
        text: str,
        source: str,
        voice: str = "",
        emotion: str = "",
        context: str = "",
    ) -> None:
        if not hasattr(filter, "after_message_sent"):
            raise RuntimeError(
                "当前 AstrBot 版本不支持消息发送后钩子，无法保证文字先于语音发送。"
            )
        session = str(getattr(event, "unified_msg_origin", "") or "").strip()
        if not session:
            raise RuntimeError(
                "当前消息缺少 unified_msg_origin，无法后台补发语音。"
            )
        self._preflight_session(session)
        event.set_extra(
            _PENDING_BACKGROUND_JOB_EXTRA,
            {
                "text": text,
                "source": "command" if source == "command" else "auto",
                "voice": voice,
                "emotion": emotion,
                "context": context,
            },
        )

    async def _notify_deferred_submission_failure(
        self,
        event: AstrMessageEvent,
        message: str,
    ) -> None:
        session = str(getattr(event, "unified_msg_origin", "") or "").strip()
        if not session or Plain is None:
            return
        try:
            await self._send_to_session(session, [Plain(message)])
        except Exception as exc:
            self.logger.warning(
                "[mimo-tts] failed to deliver deferred queue notice: %s", exc
            )

    if hasattr(filter, "on_platform_loaded"):

        @filter.on_platform_loaded()
        async def resume_persisted_tts_jobs(self):
            manager = self._background_manager()
            manager.start()
            recovered = manager.snapshot().get("recovered_jobs", 0)
            if recovered:
                self.logger.info("[mimo-tts] resumed %s persisted TTS jobs", recovered)

    if hasattr(filter, "after_message_sent"):

        @filter.after_message_sent()
        async def submit_tts_after_text_sent(self, event: AstrMessageEvent):
            pending = event.get_extra(_PENDING_BACKGROUND_JOB_EXTRA)
            if not isinstance(pending, dict):
                return

            # Consume before awaiting so another hook invocation cannot submit twice.
            event.set_extra(_PENDING_BACKGROUND_JOB_EXTRA, None)
            text = clean_tts_text(str(pending.get("text") or ""))
            if not text:
                self.logger.info(
                    "[mimo-tts] deferred tts skipped: pending text is empty"
                )
                return

            source = str(pending.get("source") or "auto")
            try:
                accepted = await self._submit_background_job(
                    event=event,
                    text=text,
                    source=source,
                    voice=str(pending.get("voice") or ""),
                    emotion=str(pending.get("emotion") or ""),
                    context=str(pending.get("context") or ""),
                )
            except Exception as exc:
                self.logger.warning(
                    "[mimo-tts] deferred queue submit failed: %s", exc
                )
                if source == "command":
                    await self._notify_deferred_submission_failure(
                        event, f"语音任务提交失败：{exc}"
                    )
                return
            if not accepted:
                self.logger.warning(
                    "[mimo-tts] deferred tts skipped: background queue full"
                )
                if source == "command":
                    await self._notify_deferred_submission_failure(
                        event, "语音队列已满，请稍后重试。"
                    )

    @filter.command("tts", alias={"朗读", "语音"})
    async def tts_command(self, event: AstrMessageEvent):
        """朗读指定文本，可选择音色、情绪和演绎风格。

        用法：/tts [-v 音色名或ID] [-e happy|sad|angry|neutral] [-c 风格] 文本
        别名：/朗读、/语音。所有用户可用，输出遵循插件回复模式。
        参数须放在正文前，含空格的参数值用引号包裹，-- 后全部作为正文。
        """
        raw = self._tail_any(event.message_str, ("tts", "朗读", "语音"))
        try:
            voice_name, requested_emotion, text, command_context = self._parse_tts_args(raw)
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return
        text = clean_tts_text(text)
        if not text:
            yield event.plain_result(
                "用法：/tts [-v 音色名] [-e happy|sad|angry|neutral] [-c 风格指令] 文本"
            )
            return

        if self.plugin_config.reply_mode == "text_only":
            yield event.plain_result(text)
            return
        if self.plugin_config.delivery_mode == "background":
            if self.plugin_config.reply_mode == "text_and_audio":
                try:
                    self._defer_background_job_until_message_sent(
                        event=event,
                        text=text,
                        source="command",
                        voice=voice_name or "",
                        emotion=requested_emotion or "",
                        context=command_context,
                    )
                except Exception as exc:
                    yield event.plain_result(text)
                    yield event.plain_result(f"语音任务提交失败：{exc}")
                    return
                yield event.plain_result(text)
                return
            try:
                accepted = await self._submit_background_job(
                    event=event,
                    text=text,
                    source="command",
                    voice=voice_name or "",
                    emotion=requested_emotion or "",
                    context=command_context,
                )
            except Exception as exc:
                yield event.plain_result(f"语音任务提交失败：{exc}")
                return
            if not accepted:
                yield event.plain_result("语音队列已满，请稍后重试。")
            return

        if self.plugin_config.reply_mode == "text_and_audio":
            yield event.plain_result(text)

        try:
            output = await self.synthesize_text(
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

        await self._send_audio_result(event, output)

    @filter.command("tts音色列表", alias={"音色列表"})
    async def list_voices_command(self, event: AstrMessageEvent):
        """查看可用音色、类型、音色ID及默认绑定标记。

        用法：/tts音色列表；别名：/音色列表。所有用户可用。
        """
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
            kind = {"builtin": "内置/可唱歌", "design": "设计", "clone": "克隆"}.get(voice.type, voice.type)
            lines.append(f"{voice.name}{marker}{emotion_note} [{kind}] ({voice.id}){desc}")
        yield event.plain_result("\n".join(lines))

    def _singing_voice(self, selector: str = "") -> VoiceProfile:
        selector = selector or str(
            self.studio_store.settings("defaults").get("sing", {}).get("voice_id") or ""
        )
        voice = self.voice_store.find_voice(selector)
        if voice is None or voice.type != "builtin":
            raise ValueError("请在工作台设置预置类型的默认唱歌音色，或显式指定预置音色")
        return voice

    async def _submit_song(self, event, lyrics: str, voice: str = "", style: str = "") -> bool:
        selected = self._singing_voice(voice)
        lyrics = lyrics.strip()
        if len(singing_text(lyrics)) > self.plugin_config.max_text_chars:
            raise ValueError("歌词过长，请缩短或手动分节")
        session = str(getattr(event, "unified_msg_origin", "") or "")
        if not session:
            raise ValueError("缺少目标会话")
        self._preflight_session(session)
        return await self._background_manager().submit(TTSJob(
            session=session, text=lyrics, voice=selected.id, context=style,
            mode="sing", source="command", notify_on_failure=self._failure_notice_enabled("command"),
            voice_snapshot=selected.to_dict(), director_enabled=False,
            synthesis_snapshot=self._synthesis_snapshot(),
        ))

    @filter.command("唱歌")
    async def sing_command(self, event: AstrMessageEvent):
        """使用内置音色演唱歌词，保留歌词换行并后台发送音频。

        用法：/唱歌 [--音色 音色名或ID] [--风格 风格] 歌词
        所有用户可用；省略音色时使用工作台的默认唱歌音色。
        音色、风格也可用 -v、-c 指定；参数放在歌词前，-- 后全部作为歌词。
        """
        try:
            args, lyrics = self._parse_command_options(
                self._tail_any(event.message_str, ("唱歌",)),
                {"--音色": "voice", "-v": "voice", "--风格": "style", "-c": "style"},
            )
            accepted = await self._submit_song(event, lyrics, args.get("voice", ""), args.get("style", ""))
            yield event.plain_result("歌曲已加入队列。" if accepted else "语音队列已满。")
        except (ValueError, RuntimeError) as exc:
            yield event.plain_result(str(exc))

    if hasattr(filter, "llm_tool"):
        @filter.llm_tool(name="mimo_tts_sing")
        async def mimo_tts_sing(self, event: AstrMessageEvent, lyrics: str, style: str = "", voice: str = ""):
            """Sing supplied lyrics using a built-in voice; never change chat defaults.

            Args:
                lyrics(string): Complete lyrics explicitly requested by the user.
                style(string): Optional singing style.
                voice(string): Optional built-in voice profile name or ID.
            """
            try:
                accepted = await self._submit_song(event, lyrics, voice, style)
                yield "歌曲已加入队列。" if accepted else "语音队列已满。"
            except (ValueError, RuntimeError) as exc:
                self.logger.warning("[mimo-tts] mimo_tts_sing failed: %s", exc)
                yield "歌曲未能加入队列"

    @filter.on_decorating_result()
    async def auto_tts_reply(self, event: AstrMessageEvent):
        options = self.studio_store.settings("sessions").get(
            str(getattr(event, "unified_msg_origin", "") or ""), {}
        )
        if not options.get("auto_tts_enabled", self.plugin_config.auto_tts_enabled):
            self.logger.info("[mimo-tts] auto tts skipped: feature disabled")
            return
        if self.plugin_config.reply_mode == "text_only":
            self.logger.info("[mimo-tts] auto tts skipped: reply mode text_only")
            return
        access_decision = self._auto_tts_access_decision(event)
        if not access_decision["allowed"]:
            self.logger.info("[mimo-tts] auto tts skipped: %s", access_decision["reason"])
            return
        self.logger.info("[mimo-tts] auto tts allowed: %s", access_decision["reason"])
        roll = random.random()
        probability = options.get("auto_tts_probability", self.plugin_config.auto_tts_probability)
        if roll > probability:
            self.logger.info(
                "[mimo-tts] auto tts skipped: probability gate roll=%.3f threshold=%.3f scope=%s matched=%s",
                roll,
                probability,
                access_decision["scope"],
                clip_log_text(access_decision["matched_rule"] or "none"),
            )
            return
        result = event.get_result()
        if result is None or not getattr(result, "chain", None):
            self.logger.info("[mimo-tts] auto tts skipped: no result chain")
            return
        is_llm_result = getattr(result, "is_llm_result", None)
        if callable(is_llm_result) and not is_llm_result():
            self.logger.info("[mimo-tts] auto tts skipped: non-LLM result")
            return
        text = clean_tts_text(result.get_plain_text())
        if not text:
            self.logger.info("[mimo-tts] auto tts skipped: empty plain text")
            return
        if self.plugin_config.delivery_mode == "background":
            if self.plugin_config.reply_mode == "text_and_audio":
                try:
                    self._defer_background_job_until_message_sent(
                        event=event,
                        text=text,
                        source="auto",
                    )
                except Exception as exc:
                    self.logger.warning(
                        "[mimo-tts] auto tts deferral failed: %s", exc
                    )
                return
            try:
                accepted = await self._submit_background_job(
                    event=event,
                    text=text,
                    source="auto",
                )
            except Exception as exc:
                self.logger.warning("[mimo-tts] auto tts queue submit failed: %s", exc)
                return
            if not accepted:
                self.logger.warning("[mimo-tts] auto tts skipped: background queue full")
            elif self.plugin_config.reply_mode == "audio_only":
                result.chain = [comp for comp in result.chain if not self._is_plain_component(comp)]
            return
        try:
            output = await self.synthesize_text(
                text,
                user_id=str(event.get_sender_id() or "").strip(),
                group_id=self._conversation_id(event),
            )
        except Exception as exc:
            self.logger.warning("[mimo-tts] auto tts failed: %s", exc)
            return

        self.logger.info(
            "[mimo-tts] auto tts generated: scope=%s matched=%s text=%s",
            access_decision["scope"],
            clip_log_text(access_decision["matched_rule"] or "none"),
            clip_log_text(text),
        )

        audio_component = self._audio_component(
            output, str(getattr(event, "unified_msg_origin", "") or "")
        )
        if audio_component is None:
            return
        if self.plugin_config.reply_mode == "audio_only":
            result.chain = [comp for comp in result.chain if not self._is_plain_component(comp)]
        result.chain.append(audio_component)

    @filter.command("tts设置音色", alias={"设置音色"})
    async def set_user_voice_command(self, event: AstrMessageEvent):
        """设置个人默认音色，或解除个人音色绑定。

        用法：/tts设置音色 <音色名或ID|默认>；别名：/设置音色。
        所有用户可用，仅修改自己的绑定；填 默认 后跟随会话或全局设置。
        """
        selector = self._tail_any(event.message_str, ("tts设置音色", "设置音色"))
        if selector == "默认":
            self.voice_store.set_binding("user", str(event.get_sender_id() or ""), "")
            yield event.plain_result("已清除你的个人音色绑定，将跟随会话或全局设置。")
            return
        voice = self.voice_store.find_voice(selector)
        if voice is None:
            yield event.plain_result("找不到该音色，可发送 /tts音色列表 查看。")
            return
        self.voice_store.set_user_default(str(event.get_sender_id() or ""), voice.id)
        yield event.plain_result(f"已将你的默认音色设为：{voice.name}")

    @filter.command("tts默认音色", alias={"默认音色"})
    async def set_global_voice_command(self, event: AstrMessageEvent):
        """设置全局默认音色，仅插件管理员可用。

        用法：/tts默认音色 <音色名或ID>；别名：/默认音色。
        """
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
        """设置当前会话的默认音色，仅插件管理员可用。

        用法：/tts群默认音色 <音色名或ID>；别名：/群默认音色。
        绑定当前会话，支持群聊和私聊。
        """
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
        """设置指定情绪的默认音色，仅插件管理员可用。

        用法：/tts情绪音色 <happy|sad|angry|neutral> <音色名或ID>
        别名：/情绪音色。
        """
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

    @filter.command("tts会话")
    async def studio_session_command(self, event: AstrMessageEvent):
        """查看、修改或重置当前会话的 TTS 设置，仅插件管理员可用。

        用法：/tts会话 [查看|重置]，或 /tts会话 <设置项> <值>。
        设置项：音色、风格、自动、概率、导演。
        自动和导演填 开/关，概率填 0 到 1；值填 默认 可移除该项覆盖。
        重置会清除当前会话的工作台覆盖设置，不清除独立的音色绑定。
        """
        if not self._is_admin(event):
            yield event.plain_result("只有插件管理员可以管理会话设置。")
            return
        session = str(event.unified_msg_origin or "")
        raw = self._tail_any(event.message_str, ("tts会话",)).strip()
        options = self.studio_store.settings("sessions").get(session, {})
        parts = raw.split(maxsplit=1)
        try:
            if not parts or parts[0] == "查看":
                effective = self._effective_session(session)
                yield event.plain_result(
                    f"音色：{effective['voice_name']}；风格：{effective['style']}；"
                    f"自动语音：{effective['auto_tts_enabled']}；"
                    f"概率：{effective['auto_tts_probability']}；导演：{effective['director']}"
                )
                return
            if parts[0] == "重置":
                self.studio_store.save_setting("sessions", session, None)
            elif len(parts) == 2:
                key, value = parts
                if key == "音色":
                    voice = self.voice_store.find_voice(value)
                    if value == "默认":
                        options.pop("voice_id", None)
                    elif voice is None or self._usable_voice(voice.id) is None:
                        raise ValueError("音色不可用")
                    else:
                        options["voice_id"] = voice.id
                elif key in {"自动", "导演"}:
                    field = "director" if key == "导演" else "auto_tts_enabled"
                    if value == "默认":
                        options.pop(field, None)
                    elif value in {"开", "关"}:
                        options[field] = value == "开"
                    else:
                        raise ValueError("开关值应为 开、关 或 默认")
                elif key == "概率":
                    if value == "默认":
                        options.pop("auto_tts_probability", None)
                    else:
                        probability = float(value)
                        if not 0 <= probability <= 1:
                            raise ValueError("概率应在 0 到 1 之间")
                        options["auto_tts_probability"] = probability
                elif key == "风格":
                    styles = self.studio_store.settings("styles")
                    selected = next((k for k, v in styles.items() if k == value or v.get("name") == value), None)
                    if value == "默认":
                        options.pop("style_id", None)
                    elif selected:
                        options["style_id"] = selected
                    else:
                        raise ValueError("风格不存在")
                else:
                    raise ValueError("支持：音色、风格、自动、概率、导演、重置、查看")
                self.studio_store.save_setting("sessions", session, options)
            else:
                raise ValueError("用法：/tts会话 查看|重置，或 /tts会话 音色|风格|自动|概率|导演 <值>")
            yield event.plain_result("会话设置已更新。")
        except ValueError as exc:
            yield event.plain_result(str(exc))

    @filter.command("tts状态", alias={"tts狀態"})
    async def status_command(self, event: AstrMessageEvent):
        """查看插件状态，普通用户显示简要信息，插件管理员显示完整诊断。

        用法：/tts状态；别名：/tts狀態。所有用户可用。
        """
        if not self._is_admin(event):
            yield event.plain_result(
                f"MiMo TTS：可用音色 {len(self.voice_store.list_voices(include_disabled=False))} 个\n"
                f"回复模式：{self.plugin_config.reply_mode}\n"
                "指令帮助：/tts帮助"
            )
            return
        defaults = self.voice_store.defaults()
        queue = self._queue_snapshot()
        lines = [
            "MiMo TTS 状态",
            f"model: {self.plugin_config.model}",
            f"voices: {len(self.voice_store.list_voices(include_disabled=False))}",
            f"emotion_routing: {self.plugin_config.emotion_routing_enabled}",
            f"segment: {self.plugin_config.segment_enabled}, threshold={self.plugin_config.segment_threshold_chars}",
            f"reply_mode: {self.plugin_config.reply_mode}",
            f"delivery_mode: {self.plugin_config.delivery_mode}",
            (
                "audio_transport: "
                f"{self.plugin_config.audio_transport}, "
                f"segment_chars={self.plugin_config.delivery_segment_chars}, "
                f"base64_max={self.plugin_config.base64_max_mb}MB"
            ),
            (
                "queue: "
                f"queued={queue['queued_jobs']}, running={queue['running_jobs']}, "
                f"completed={queue['completed_jobs']}, failed={queue['failed_jobs']}, "
                f"cancelled={queue['cancelled_jobs']}, recovered={queue['recovered_jobs']}, "
                f"dropped={queue['dropped_jobs']}, avg_latency={queue['average_latency_seconds']}s"
            ),
            f"queue_last_error: {queue['last_error'] or '-'}",
            (
                "reliability: "
                f"circuit={queue['circuit_state']}, failures={queue['circuit_failures']}, "
                f"retry_after={queue['circuit_retry_after_seconds']}s, attempts={queue['attempts']}, "
                f"retries={queue['retries']}, rpm={queue['rate_limit_rpm']}, "
                f"limiter_waits={queue['rate_limit_wait_count']}"
            ),
            (
                "jobs: "
                f"persistence={queue['persistence_enabled']}, recovery_age={queue['recovery_max_age_hours']}h, "
                f"audio_cleanup={queue['audio_cleanup']}"
            ),
            f"platforms: {queue['platforms'] or 'unknown (send will be attempted)'}",
            f"auto_tts: {self.plugin_config.auto_tts_enabled}, probability={self.plugin_config.auto_tts_probability}",
            f"auto_tts_access: {self._auto_tts_access_preview()['summary']}",
            f"file_fallback: {self.plugin_config.file_fallback_enabled}",
            f"output_cleanup: days={self.plugin_config.output_retention_days}, max_files={self.plugin_config.output_max_files}",
            f"emotion_defaults: {defaults.get('emotion_defaults') or {}}",
        ]
        yield event.plain_result("\n".join(lines))

    @filter.command("tts任务", alias={"tts任務"})
    async def tasks_command(self, event: AstrMessageEvent):
        """查看最近 20 条后台 TTS 任务及任务ID，仅插件管理员可用。

        用法：/tts任务；别名：/tts任務。
        """
        if not self._is_admin(event):
            yield event.plain_result("只有插件管理员可以查看 TTS 任务。")
            return
        tasks = self._task_list(limit=20)
        if not tasks:
            yield event.plain_result("当前没有 TTS 任务记录。")
            return
        lines = ["最近 TTS 任务"]
        for task in tasks:
            recovered = " recovered" if task["recovered"] else ""
            error = f" error={task['error']}" if task["error"] else ""
            lines.append(
                f"{task['id']} {task['status']} {task['source']}{recovered} "
                f"{task['text_preview']}{error}"
            )
        yield event.plain_result("\n".join(lines))

    @filter.command("tts取消")
    async def cancel_task_command(self, event: AstrMessageEvent):
        """取消指定后台 TTS 任务，仅插件管理员可用。

        用法：/tts取消 <任务ID>；任务ID可通过 /tts任务 查看。
        """
        if not self._is_admin(event):
            yield event.plain_result("只有插件管理员可以取消 TTS 任务。")
            return
        arguments = self._tail_any(event.message_str, ("tts取消",)).split(maxsplit=1)
        job_id = arguments[0] if arguments else ""
        if not job_id:
            yield event.plain_result("用法：/tts取消 <任务 ID>")
            return
        cancelled = await self._background_manager().cancel(job_id)
        yield event.plain_result("任务已取消。" if cancelled else "任务不存在或已经结束。")

    @filter.command("tts清空")
    async def clear_tasks_command(self, event: AstrMessageEvent):
        """清理后台任务记录，可同时取消活动任务，仅插件管理员可用。

        用法：/tts清空 [全部|all|active]。
        不带参数只清理已结束任务；指定 全部、all 或 active 也会取消活动任务。
        此指令不清理工作台生成历史。
        """
        if not self._is_admin(event):
            yield event.plain_result("只有插件管理员可以清理 TTS 任务。")
            return
        argument = self._tail_any(event.message_str, ("tts清空",)).strip().lower()
        include_active = argument in {"all", "全部", "active"}
        affected = await self._background_manager().clear(include_active=include_active)
        scope = "历史记录与活动任务" if include_active else "已结束任务记录"
        yield event.plain_result(f"已清理{scope}，影响 {affected} 项。")

    @filter.command("tts帮助")
    async def help_command(self, event: AstrMessageEvent):
        """查看 TTS 指令用法，插件管理员同时可见管理指令。

        用法：/tts帮助。所有用户可用。
        """
        text = (
            "/tts [-v 音色] [-e 情绪] [-c 风格] 文本\n"
            "/唱歌 [--音色 音色] [--风格 风格] 歌词\n"
            "参数放在正文之前；含空格的参数值用双引号；-- 后全部作为正文。\n"
            "/tts音色列表\n/tts设置音色 <音色名或ID>；填 默认 可解除个人绑定\n"
            "/tts状态"
        )
        if self._is_admin(event):
            text += (
                "\n管理员：\n/tts默认音色 <音色>\n/tts群默认音色 <音色>\n"
                "/tts情绪音色 <happy|sad|angry|neutral> <音色>\n"
                "/tts会话 查看|重置\n/tts会话 音色|风格|自动|概率|导演 <值>\n"
                "/tts任务\n/tts取消 <任务ID>\n/tts清空 [全部]"
            )
        yield event.plain_result(text)

    async def terminate(self):
        await self._stop_studio_jobs()
        manager = self._job_manager
        self._job_manager = None
        if manager is not None:
            await manager.stop(drain_timeout=5.0)
        client = self._mimo_client
        self._mimo_client = None
        self._mimo_client_signature = None
        if client is not None:
            await client.close()
        retired = tuple(self._retired_clients)
        self._retired_clients.clear()
        if retired:
            await asyncio.gather(*(item.close() for item in retired), return_exceptions=True)
        if self._client_close_tasks:
            await asyncio.gather(*tuple(self._client_close_tasks), return_exceptions=True)
            self._client_close_tasks.clear()
