from __future__ import annotations

import asyncio
import base64
import binascii
import inspect
import pathlib

from quart import jsonify, request

from .core.audio_codec import (
    AudioValidationError,
)
from .core.emotion import SUPPORTED_EMOTIONS, normalize_emotion
from .core.pages_upload import store_voice_sample
from .core.text_processing import clean_tts_text
from .studio_api import StudioAPIMixin


class PagesAPIMixin(StudioAPIMixin):
    def _usable_voice(self, voice_id: str):
        voice = self.voice_store.get_voice(voice_id)
        if voice is None or not voice.enabled or (voice.type == "clone" and not voice.consent_confirmed):
            return None
        return voice

    def _delete_voice_audio_file(self, audio_path: str | pathlib.Path) -> bool:
        reference_root = (pathlib.Path(self.data_dir) / "voice_refs").resolve()
        resolved = pathlib.Path(audio_path).resolve()
        if not resolved.is_relative_to(reference_root):
            self.logger.warning(
                "[mimo-tts] refused to delete voice file outside voice_refs: %s",
                resolved,
            )
            return False
        resolved.unlink(missing_ok=True)
        return True

    def _public_config(self) -> dict:
        config = {key: value for key, value in self.config.items() if key != "api_key"}
        api_key = str(self.plugin_config.api_key or "")
        config["api_key_masked"] = f"********{api_key[-4:]}" if api_key else ""
        config["api_key_configured"] = bool(api_key)
        return config

    @staticmethod
    def _pages_error(message: str, status: int = 400, detail: str = ""):
        payload = {"success": False, "error": message}
        if detail:
            payload["detail"] = detail
        return jsonify(payload), status

    def _register_pages_web_api(self) -> None:
        register_web_api = getattr(self.context, "register_web_api", None)
        if not callable(register_web_api):
            self.logger.warning("[mimo-tts] context.register_web_api unavailable")
            return
        plugin_id = "astrbot_plugin_mimo_tts_clone"
        routes = [
            ("studio_catalog", self._pages_studio_catalog, ["GET"], "工作台目录"),
            ("voice_bindings", self._pages_voice_bindings, ["GET"], "音色绑定列表"),
            ("save_voice_binding", self._pages_save_voice_binding, ["POST"], "管理音色绑定"),
            ("session_effective", self._pages_session_effective, ["GET"], "会话生效设置"),
            ("resend_history", self._pages_resend_history, ["POST"], "发送历史音频"),
            ("lyrics", self._pages_lyrics, ["GET"], "歌词版本"),
            ("save_lyrics", self._pages_save_lyrics, ["POST"], "保存歌词版本"),
            ("delete_lyrics", self._pages_delete_lyrics, ["POST"], "删除歌词版本"),
            ("draft_lyrics", self._pages_draft_lyrics, ["POST"], "AI 创作歌词"),
            ("export_studio", self._pages_export_studio, ["POST"], "导出音色与风格"),
            ("import_studio", self._pages_import_studio, ["POST"], "导入音色与风格"),
            ("create_voice", self._pages_create_voice, ["POST"], "创建预置或设计音色"),
            ("duplicate_voice", self._pages_duplicate_voice, ["POST"], "复制音色"),
            ("studio_generate", self._pages_studio_generate, ["POST"], "工作台生成"),
            ("studio_job", self._pages_studio_job, ["GET"], "试听任务状态"),
            ("cancel_studio_job", self._pages_cancel_studio_job, ["POST"], "取消试听"),
            ("generation_history", self._pages_generation_history, ["GET"], "生成历史分页"),
            ("history_audio", self._pages_history_audio, ["GET"], "历史音频"),
            ("delete_history", self._pages_delete_history, ["POST"], "删除历史记录"),
            ("save_studio_setting", self._pages_save_studio_setting, ["POST"], "保存风格与会话设置"),
            ("history_to_clone", self._pages_history_to_clone, ["POST"], "生成结果保存为克隆音色"),
            ("get_config", self._pages_get_config, ["GET"], "获取 MiMo TTS 配置"),
            ("get_queue_status", self._pages_get_queue_status, ["GET"], "获取后台 TTS 队列状态"),
            ("get_tts_tasks", self._pages_get_tts_tasks, ["GET"], "获取后台 TTS 任务"),
            ("cancel_tts_task", self._pages_cancel_tts_task, ["POST"], "取消后台 TTS 任务"),
            ("retry_tts_task", self._pages_retry_tts_task, ["POST"], "重试失败 TTS 任务"),
            ("clear_tts_tasks", self._pages_clear_tts_tasks, ["POST"], "清理后台 TTS 任务"),
            ("save_config", self._pages_save_config, ["POST"], "保存 MiMo TTS 配置"),
            ("list_voices", self._pages_list_voices, ["GET"], "列出音色"),
            ("list_ai_providers", self._pages_list_ai_providers, ["GET"], "列出 AstrBot AI 服务商"),
            ("upload_voice_sample", self._pages_upload_voice_sample, ["POST"], "上传音色样本"),
            ("upload_voice_sample_json", self._pages_upload_voice_sample_json, ["POST"], "上传音色样本(JSON)"),
            ("update_voice", self._pages_update_voice, ["POST"], "更新音色"),
            ("delete_voice", self._pages_delete_voice, ["POST"], "删除音色"),
            ("set_default_voice", self._pages_set_default_voice, ["POST"], "设置默认音色"),
            ("set_emotion_voice", self._pages_set_emotion_voice, ["POST"], "设置情绪默认音色"),
            ("synthesize_preview", self._pages_synthesize_preview, ["POST"], "试听音色"),
            ("test_connection", self._pages_test_connection, ["POST"], "测试 MiMo TTS 连接"),
        ]
        for name, handler, methods, desc in routes:
            register_web_api(f"/{plugin_id}/{name}", handler, methods, desc)

    def _pages_payload(self) -> dict:
        voices = self.voice_store.list_voices()
        enabled_voices = self.voice_store.list_voices(include_disabled=False)
        providers = self._list_ai_providers()
        return {
            "success": True,
            "config": self._public_config(),
            "voices": [voice.to_dict() for voice in voices],
            "defaults": self.voice_store.defaults(),
            "emotions": list(SUPPORTED_EMOTIONS),
            "access_control": self._auto_tts_access_preview(),
            "queue_status": self._queue_snapshot(),
            "tasks": self._task_list(),
            "readiness": {
                "api_key": bool(self.plugin_config.api_key),
                "voices": bool(enabled_voices),
                "ai_director": bool(
                    self.plugin_config.ai_style_director_enabled
                    and (providers or self.plugin_config.ai_style_director_provider_id)
                ),
                "providers": len(providers),
            },
        }

    async def _pages_get_config(self):
        return jsonify(self._pages_payload())

    async def _pages_get_queue_status(self):
        return jsonify({"success": True, "queue_status": self._queue_snapshot()})

    async def _pages_get_tts_tasks(self):
        return jsonify(
            {
                "success": True,
                "tasks": self._task_list(),
                "queue_status": self._queue_snapshot(),
            }
        )

    async def _pages_retry_tts_task(self):
        data = await request.get_json(force=True) or {}
        if data.get("confirm") is not True:
            return self._pages_error("请确认重试；发送状态不确定时可能重复发送")
        retried = await self._background_manager().retry(str(data.get("job_id") or ""))
        if not retried:
            return self._pages_error("只能重试失败任务，或当前队列已满")
        return jsonify({"success": True, "tasks": self._task_list(),
                        "queue_status": self._queue_snapshot()})

    async def _pages_cancel_tts_task(self):
        data = await request.get_json(force=True) or {}
        job_id = str(data.get("job_id") or data.get("id") or "").strip()
        if not job_id:
            return self._pages_error("缺少 job_id")
        cancelled = await self._background_manager().cancel(job_id)
        if not cancelled:
            return self._pages_error("任务不存在或已经结束", 404)
        return jsonify(
            {
                "success": True,
                "tasks": self._task_list(),
                "queue_status": self._queue_snapshot(),
            }
        )

    async def _pages_clear_tts_tasks(self):
        data = await request.get_json(force=True) or {}
        include_active = bool(data.get("include_active", False))
        affected = await self._background_manager().clear(include_active=include_active)
        return jsonify(
            {
                "success": True,
                "affected": affected,
                "tasks": self._task_list(),
                "queue_status": self._queue_snapshot(),
            }
        )

    async def _pages_save_config(self):
        data = await request.get_json(force=True) or {}
        if not isinstance(data, dict):
            return jsonify({"success": False, "error": "Invalid JSON payload"}), 400
        data = dict(data)
        if not str(data.get("api_key") or "").strip():
            data.pop("api_key", None)
        persisted = self._update_runtime_config(data)
        if not persisted.get("local") and not persisted.get("native"):
            return jsonify(
                {
                    "success": False,
                    "error": "配置保存失败：无法写入 AstrBot 配置或插件本地配置文件。",
                    "detail": persisted.get("warning") or "",
                }
            ), 500
        response = {"success": True, "config": self._public_config(), "persisted": persisted}
        if persisted.get("warning"):
            response["warning"] = "配置已保存到插件本地文件，但 AstrBot 原生配置同步失败。"
            response["detail"] = persisted["warning"]
        return jsonify(response)

    async def _pages_list_voices(self):
        return jsonify(self._pages_payload())

    async def _pages_list_ai_providers(self):
        return jsonify(
            {
                "success": True,
                "providers": self._list_ai_providers(),
            }
        )

    def _list_ai_providers(self) -> list[dict[str, str]]:
        candidates: list[Any] = []

        getter = getattr(self.context, "get_all_providers", None)
        if callable(getter):
            try:
                candidates.extend(list(getter() or []))
            except Exception as exc:
                self.logger.warning("[mimo-tts] failed to list AstrBot AI providers: %s", exc)

        provider_manager = getattr(self.context, "provider_manager", None)
        if provider_manager is not None:
            candidates.extend(list(getattr(provider_manager, "provider_insts", []) or []))
            inst_map = getattr(provider_manager, "inst_map", None)
            if isinstance(inst_map, dict):
                candidates.extend(list(inst_map.values()))
            provider_configs = getattr(provider_manager, "providers_config", None)
            if isinstance(provider_configs, list):
                candidates.extend(
                    cfg
                    for cfg in provider_configs
                    if isinstance(cfg, dict)
                    and str(cfg.get("provider_type") or cfg.get("type") or "").strip() in {"", "chat_completion"}
                )

        seen: set[tuple[str, str]] = set()
        items: list[dict[str, str]] = []
        for provider in candidates:
            item = self._normalize_ai_provider(provider)
            if item is None:
                continue
            key = (item["id"], item["model"])
            if key in seen:
                continue
            seen.add(key)
            items.append(item)

        items.sort(key=lambda item: (item["label"], item["id"]))
        return items

    @staticmethod
    def _normalize_ai_provider(provider: Any) -> dict[str, str] | None:
        if isinstance(provider, dict):
            provider_id = str(provider.get("id") or "").strip()
            model = str(provider.get("model") or "").strip()
            if not provider_id:
                return None
            label = provider_id if not model else f"{provider_id} / {model}"
            return {"id": provider_id, "name": provider_id, "model": model, "label": label}

        meta_fn = getattr(provider, "meta", None)
        meta = None
        if callable(meta_fn):
            try:
                meta = meta_fn()
            except Exception:
                meta = None

        provider_id = ""
        model = ""
        if meta is not None:
            provider_id = str(getattr(meta, "id", "") or "").strip()
            model = str(getattr(meta, "model", "") or "").strip()

        provider_config = getattr(provider, "provider_config", None)
        if not provider_id and isinstance(provider_config, dict):
            provider_id = str(provider_config.get("id") or "").strip()
        if not model and isinstance(provider_config, dict):
            model = str(provider_config.get("model") or "").strip()

        if not provider_id:
            return None

        label = provider_id if not model else f"{provider_id} / {model}"
        return {"id": provider_id, "name": provider_id, "model": model, "label": label}

    async def _pages_upload_voice_sample(self):
        files = await request.files
        form = await request.form
        file = files.get("file")
        if file is None:
            return jsonify({"success": False, "error": "未收到音频文件"}), 400

        data = file.read()
        if inspect.isawaitable(data):
            data = await data
        try:
            voice = await store_voice_sample(
                voice_store=self.voice_store,
                data_dir=self.data_dir,
                max_voice_file_bytes=self.plugin_config.max_voice_file_bytes,
                data=data,
                filename=file.filename or "voice.wav",
                metadata=form,
            )
        except AudioValidationError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400

        return jsonify({"success": True, "voice": voice.to_dict()})

    async def _pages_upload_voice_sample_json(self):
        payload = await request.get_json(force=True) or {}
        filename = str(payload.get("filename") or "voice.wav")
        encoded = str(payload.get("audio_base64") or "")
        if "," in encoded and encoded.strip().lower().startswith("data:"):
            encoded = encoded.split(",", 1)[1]
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            return jsonify({"success": False, "error": "音频 Base64 数据无效"}), 400

        try:
            voice = await store_voice_sample(
                voice_store=self.voice_store,
                data_dir=self.data_dir,
                max_voice_file_bytes=self.plugin_config.max_voice_file_bytes,
                data=data,
                filename=filename,
                metadata=payload,
            )
        except AudioValidationError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400

        return jsonify({"success": True, "voice": voice.to_dict()})

    async def _pages_update_voice(self):
        data = await request.get_json(force=True) or {}
        voice_id = str(data.get("voice_id") or data.get("id") or "").strip()
        if not voice_id:
            return jsonify({"success": False, "error": "缺少 voice_id"}), 400
        changes = {}
        for key in ("name", "description", "enabled", "style_context", "style_tags", "emotion",
                    "builtin_voice", "design_prompt"):
            if key in data:
                changes[key] = data[key]
        if "emotion" in changes:
            changes["emotion"] = normalize_emotion(changes["emotion"]) or ""
        try:
            voice = self.voice_store.update_voice(voice_id, **changes)
        except ValueError as exc:
            return self._pages_error(str(exc))
        if voice is None:
            return jsonify({"success": False, "error": "音色不存在"}), 404
        return jsonify({"success": True, "voice": voice.to_dict()})

    async def _pages_delete_voice(self):
        data = await request.get_json(force=True) or {}
        voice_id = str(data.get("voice_id") or data.get("id") or "").strip()
        voice = self.voice_store.get_voice(voice_id)
        references = []
        for category in ("sessions", "defaults"):
            for key, value in self.studio_store.settings(category).items():
                if value.get("voice_id") == voice_id:
                    references.append((category, key, value))
        if references and data.get("clear_references") is not True:
            return jsonify({"success": False, "requires_confirmation": True,
                            "error": "音色正在被会话或唱歌默认设置使用，是否解除这些绑定并删除？"})
        if self._job_manager is not None:
            if self._job_manager.references_voice(voice_id):
                return self._pages_error("此音色仍被后台或失败任务引用，请完成或清理任务后再删除")
        for category, key, value in references:
            value.pop("voice_id", None)
            self.studio_store.save_setting(category, key, value)
        deleted = self.voice_store.delete_voice(voice_id)
        if voice is not None and voice.type == "clone":
            self._delete_voice_audio_file(voice.audio_path)
        return jsonify({"success": deleted, "defaults": self.voice_store.defaults()})

    async def _pages_set_default_voice(self):
        data = await request.get_json(force=True) or {}
        scope = str(data.get("scope") or "global").strip().lower()
        voice_id = str(data.get("voice_id") or "").strip()
        if self._usable_voice(voice_id) is None:
            return jsonify({"success": False, "error": "音色不存在、未启用或未确认授权"}), 404
        if scope == "user":
            self.voice_store.set_user_default(str(data.get("user_id") or ""), voice_id)
        elif scope == "group":
            self.voice_store.set_group_default(str(data.get("group_id") or ""), voice_id)
        else:
            self.voice_store.set_global_default(voice_id)
        return jsonify({"success": True, "defaults": self.voice_store.defaults()})

    async def _pages_set_emotion_voice(self):
        data = await request.get_json(force=True) or {}
        emotion = normalize_emotion(str(data.get("emotion") or ""))
        voice_id = str(data.get("voice_id") or "").strip()
        if emotion not in SUPPORTED_EMOTIONS:
            return jsonify({"success": False, "error": "不支持的情绪"}), 400
        if not voice_id:
            self.voice_store.set_emotion_default(emotion, "")
            return jsonify({"success": True, "defaults": self.voice_store.defaults()})
        if self._usable_voice(voice_id) is None:
            return jsonify({"success": False, "error": "音色不存在、未启用或未确认授权"}), 404
        self.voice_store.set_emotion_default(emotion, voice_id)
        return jsonify({"success": True, "defaults": self.voice_store.defaults()})

    async def _pages_synthesize_preview(self):
        data = await request.get_json(force=True) or {}
        text = clean_tts_text(str(data.get("text") or ""))
        voice_selector = str(data.get("voice_id") or data.get("voice") or "").strip()
        requested_emotion = str(data.get("emotion") or "").strip()
        command_context = str(data.get("context") or "")
        if not text:
            return jsonify({"success": False, "error": "请输入试听文本"}), 400

        emotion = self._resolve_emotion(text, requested_emotion)
        voice = self._select_voice(voice_selector or None, emotion=emotion)
        if voice is None:
            return jsonify({"success": False, "error": "没有可用音色"}), 400

        try:
            output_path = await self.synthesize_text(
                text,
                voice_id=voice.id,
                emotion=emotion,
                context=command_context,
                split=False,
            )
        except AudioValidationError as exc:
            return self._pages_error(f"参考音频不可用：{exc}", 400, str(exc))
        except RuntimeError as exc:
            message = str(exc)
            if "API Key" in message:
                return self._pages_error("MiMo API Key 未配置或未成功保存，请先在页面顶部保存配置。", 400, message)
            if "文本过长" in message or "鏂囨湰杩囬暱" in message:
                return self._pages_error(message, 400, message)
            return self._pages_error(f"MiMo 试听生成失败：{message}", 502, message)
        except Exception as exc:
            return self._pages_error(f"试听生成异常：{exc}", 502, str(exc))
        raw = await asyncio.to_thread(pathlib.Path(output_path).read_bytes)
        return jsonify(
            {
                "success": True,
                "audio_data": "data:audio/wav;base64," + base64.b64encode(raw).decode("utf-8"),
                "voice": voice.to_dict(),
                "emotion": emotion,
            }
        )

    async def _pages_test_connection(self):
        if not self.plugin_config.live_api_test_enabled:
            return self._pages_error(
                "真实 MiMo 联调未启用，请先打开“允许执行真实 MiMo 联调测试”并保存配置。",
                403,
            )
        data = await request.get_json(force=True) or {}
        text = clean_tts_text(str(data.get("text") or "连接测试，声音工作正常。"))
        voice_selector = str(data.get("voice_id") or data.get("voice") or "").strip()
        if not self.plugin_config.api_key:
            return self._pages_error("MiMo API Key 未配置，请先保存配置。", 400)
        if not self.voice_store.list_voices(include_disabled=False):
            return self._pages_error("暂无可用音色，请先上传参考音频。", 400)
        started = asyncio.get_running_loop().time()
        try:
            output = await self.synthesize_text(
                text,
                voice_id=voice_selector or None,
                split=False,
            )
        except Exception as exc:
            return self._pages_error(f"连接测试失败：{exc}", 502, str(exc))
        elapsed_ms = round((asyncio.get_running_loop().time() - started) * 1000)
        pathlib.Path(output).unlink(missing_ok=True)
        return jsonify(
            {
                "success": True,
                "message": "连接测试成功，MiMo 已返回音频。",
                "elapsed_ms": elapsed_ms,
            }
        )
