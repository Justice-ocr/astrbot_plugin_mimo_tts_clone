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


class PagesAPIMixin:
    def _register_pages_web_api(self) -> None:
        register_web_api = getattr(self.context, "register_web_api", None)
        if not callable(register_web_api):
            self.logger.warning("[mimo-tts] context.register_web_api unavailable")
            return
        plugin_id = "astrbot_plugin_mimo_tts_clone"
        routes = [
            ("get_config", self._pages_get_config, ["GET"], "获取 MiMo TTS 配置"),
            ("save_config", self._pages_save_config, ["POST"], "保存 MiMo TTS 配置"),
            ("list_voices", self._pages_list_voices, ["GET"], "列出音色"),
            ("upload_voice_sample", self._pages_upload_voice_sample, ["POST"], "上传音色样本"),
            ("upload_voice_sample_json", self._pages_upload_voice_sample_json, ["POST"], "上传音色样本(JSON)"),
            ("update_voice", self._pages_update_voice, ["POST"], "更新音色"),
            ("delete_voice", self._pages_delete_voice, ["POST"], "删除音色"),
            ("set_default_voice", self._pages_set_default_voice, ["POST"], "设置默认音色"),
            ("set_emotion_voice", self._pages_set_emotion_voice, ["POST"], "设置情绪默认音色"),
            ("synthesize_preview", self._pages_synthesize_preview, ["POST"], "试听音色"),
        ]
        for name, handler, methods, desc in routes:
            register_web_api(f"/{plugin_id}/{name}", handler, methods, desc)

    def _pages_payload(self) -> dict:
        return {
            "success": True,
            "config": dict(self.config),
            "voices": [voice.to_dict() for voice in self.voice_store.list_voices()],
            "defaults": self.voice_store.defaults(),
            "emotions": list(SUPPORTED_EMOTIONS),
        }

    async def _pages_get_config(self):
        return jsonify(self._pages_payload())

    async def _pages_save_config(self):
        data = await request.get_json(force=True) or {}
        if not isinstance(data, dict):
            return jsonify({"success": False, "error": "Invalid JSON payload"}), 400
        persisted = self._update_runtime_config(data)
        if not persisted.get("local") and not persisted.get("native"):
            return jsonify(
                {
                    "success": False,
                    "error": "配置保存失败：无法写入 AstrBot 配置或插件本地配置文件。",
                    "detail": persisted.get("warning") or "",
                }
            ), 500
        response = {"success": True, "config": dict(self.config), "persisted": persisted}
        if persisted.get("warning"):
            response["warning"] = "配置已保存到插件本地文件，但 AstrBot 原生配置同步失败。"
            response["detail"] = persisted["warning"]
        return jsonify(response)

    async def _pages_list_voices(self):
        return jsonify(self._pages_payload())

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
        for key in ("name", "description", "enabled", "style_context", "style_tags", "emotion"):
            if key in data:
                changes[key] = data[key]
        if "emotion" in changes:
            changes["emotion"] = normalize_emotion(changes["emotion"]) or ""
        voice = self.voice_store.update_voice(voice_id, **changes)
        if voice is None:
            return jsonify({"success": False, "error": "音色不存在"}), 404
        return jsonify({"success": True, "voice": voice.to_dict()})

    async def _pages_delete_voice(self):
        data = await request.get_json(force=True) or {}
        voice_id = str(data.get("voice_id") or data.get("id") or "").strip()
        voice = self.voice_store.get_voice(voice_id)
        deleted = self.voice_store.delete_voice(voice_id)
        if voice is not None:
            pathlib.Path(voice.audio_path).unlink(missing_ok=True)
        return jsonify({"success": deleted, "defaults": self.voice_store.defaults()})

    async def _pages_set_default_voice(self):
        data = await request.get_json(force=True) or {}
        scope = str(data.get("scope") or "global").strip().lower()
        voice_id = str(data.get("voice_id") or "").strip()
        if self.voice_store.get_voice(voice_id) is None:
            return jsonify({"success": False, "error": "音色不存在"}), 404
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
        if self.voice_store.get_voice(voice_id) is None:
            return jsonify({"success": False, "error": "音色不存在"}), 404
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
        voice_id = self.voice_store.resolve_voice_id(None, "", "", emotion=emotion) or ""
        voice = self.voice_store.find_voice(voice_selector) or self.voice_store.get_voice(voice_id)
        if voice is None:
            return jsonify({"success": False, "error": "没有可用音色"}), 400

        tts_context = self._build_tts_context(voice, emotion, command_context)
        try:
            output_path = await self._synthesize_text_to_file(text, voice, context=tts_context)
        except AudioValidationError as exc:
            return jsonify(
                {
                    "success": False,
                    "error": f"参考音频不可用：{exc}",
                    "detail": str(exc),
                }
            ), 400
        except RuntimeError as exc:
            message = str(exc)
            if "API Key" in message:
                return jsonify(
                    {
                        "success": False,
                        "error": "MiMo API Key 未配置或未成功保存，请先在页面顶部保存配置。",
                        "detail": message,
                    }
                ), 400
            if "文本过长" in message or "鏂囨湰杩囬暱" in message:
                return jsonify({"success": False, "error": message, "detail": message}), 400
            return jsonify(
                {
                    "success": False,
                    "error": f"MiMo 试听生成失败：{message}",
                    "detail": message,
                }
            ), 502
        except Exception as exc:
            return jsonify(
                {
                    "success": False,
                    "error": f"试听生成异常：{exc}",
                    "detail": str(exc),
                }
            ), 502
        raw = await asyncio.to_thread(pathlib.Path(output_path).read_bytes)
        return jsonify(
            {
                "success": True,
                "audio_data": "data:audio/wav;base64," + base64.b64encode(raw).decode("utf-8"),
                "voice": voice.to_dict(),
                "emotion": emotion,
            }
        )
