from __future__ import annotations

import asyncio
import base64
import pathlib
import time
import uuid
import io
import wave
import math
from contextlib import aclosing

from quart import jsonify, request

from .core.model_catalog import BUILTIN_VOICES, MODELS, validate_voice
from .core.voice_store import VoiceProfile
from .core.pages_upload import store_voice_sample
from .core.studio_transfer import export_package, import_package
from .core.style_director import _call_llm


class StudioAPIMixin:
    async def _pages_voice_bindings(self):
        return jsonify({"success": True, "bindings": self.voice_store.defaults()})

    async def _pages_save_voice_binding(self):
        data = await request.get_json(force=True) or {}
        try:
            scope = str(data.get("scope") or "")
            key = str(data.get("key") or "").strip()
            if scope == "emotion" and key not in {"happy", "sad", "angry", "neutral"}:
                raise ValueError("情绪类型无效")
            self.voice_store.set_binding(scope, key, str(data.get("voice_id") or ""))
        except ValueError as exc:
            return self._pages_error(str(exc))
        return jsonify({"success": True, "bindings": self.voice_store.defaults()})

    def _effective_session(self, session: str) -> dict:
        options = self.studio_store.settings("sessions").get(session, {})
        parts = session.split(":", 2)
        user = parts[2] if len(parts) == 3 and parts[1] == "FriendMessage" else ""
        voice = self._select_voice(options.get("voice_id"), user_id=user, group_id=session)
        style = self.studio_store.settings("styles").get(options.get("style_id"), {})
        return {
            "voice_id": voice.id if voice else "",
            "voice_name": voice.name if voice else "未设置",
            "voice_source": "会话" if options.get("voice_id") else "用户／会话绑定／全局",
            "style": style.get("name") or "音色默认",
            "auto_tts_enabled": options.get("auto_tts_enabled", self.plugin_config.auto_tts_enabled),
            "auto_tts_probability": options.get("auto_tts_probability", self.plugin_config.auto_tts_probability),
            "director": options.get("director", self.plugin_config.ai_style_director_enabled),
        }

    async def _pages_session_effective(self):
        return jsonify({"success": True, "effective": self._effective_session(
            str(request.args.get("session") or ""))})

    async def _pages_resend_history(self):
        data = await request.get_json(force=True) or {}
        if data.get("confirm") is not True:
            return self._pages_error("请确认发送目标")
        item = self.studio_store.history_item(str(data.get("id") or ""))
        path = self.studio_store.audio_path(item) if item else None
        session = str(data.get("session") or "").strip()
        if path is None:
            return self._pages_error("音频已过期", 404)
        if len(session.split(":", 2)) != 3:
            return self._pages_error("请输入完整目标会话")
        try:
            self._preflight_session(session)
            await self._send_audio_path_to_session(session, path)
        except Exception as exc:
            return self._pages_error(f"发送未确认成功，请检查目标端再决定是否重试：{exc}")
        return jsonify({"success": True})

    async def _pages_lyrics(self):
        return jsonify({"success": True, "items": self.studio_store.lyrics()})

    async def _pages_save_lyrics(self):
        data = await request.get_json(force=True) or {}
        try:
            item = self.studio_store.save_lyrics(
                str(data.get("text") or ""), title=str(data.get("title") or ""),
                parent_id=str(data.get("parent_id") or ""),
            )
        except ValueError as exc:
            return self._pages_error(str(exc))
        return jsonify({"success": True, "item": item})

    async def _pages_delete_lyrics(self):
        data = await request.get_json(force=True) or {}
        self.studio_store.delete_lyrics(str(data.get("id") or ""))
        return jsonify({"success": True})

    async def _pages_draft_lyrics(self):
        data = await request.get_json(force=True) or {}
        prompt = str(data.get("prompt") or "").strip()
        text = str(data.get("text") or "").strip()
        if not prompt or len(prompt) > 4000 or len(text) > 20000:
            return self._pages_error("请填写创作要求（最多 4000 字），原歌词最多 20000 字")
        if getattr(self, "_lyrics_generating", False):
            return self._pages_error("已有歌词正在创作，请稍后")
        self._lyrics_generating = True
        try:
            response = await asyncio.wait_for(_call_llm(
                self.context, f"创作要求：\n{prompt}\n\n待改写歌词（如有）：\n{text}",
                "创作原创歌词，只输出歌词正文，不输出解释或 Markdown 代码块。"
                f"保留适当换行，全文不得超过 {max(1, self.plugin_config.max_text_chars - 4)} 字。",
                provider_id=self.plugin_config.ai_style_director_provider_id,
            ), timeout=90)
            output = getattr(response, "completion_text", response)
            if not isinstance(output, str) or not output.strip():
                raise ValueError("AI 未返回有效歌词")
            if len(output.strip()) + 4 > self.plugin_config.max_text_chars:
                raise ValueError("AI 返回的歌词超过单次唱歌长度，请缩短创作要求后重试")
            item = self.studio_store.save_lyrics(
                output, title=str(data.get("title") or ""),
                parent_id=str(data.get("parent_id") or ""), prompt=prompt,
            )
            return jsonify({"success": True, "item": item})
        except Exception as exc:
            return self._pages_error(f"歌词创作失败：{exc}")
        finally:
            self._lyrics_generating = False

    def _studio_jobs(self):
        if not hasattr(self, "_studio_preview_jobs"):
            self._studio_preview_jobs = {}
        jobs = self._studio_preview_jobs
        for key, job in list(jobs.items()):
            if job["task"].done() and time.monotonic() - job["updated"] > 600:
                del jobs[key]
        return jobs

    async def _stop_studio_jobs(self):
        jobs = getattr(self, "_studio_preview_jobs", {})
        tasks = [job["task"] for job in jobs.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        jobs.clear()

    async def _pages_studio_job(self):
        job = self._studio_jobs().get(str(request.args.get("id") or ""))
        if job is None:
            return self._pages_error("试听任务不存在或已过期", 404)
        try:
            cursor = max(0, int(request.args.get("cursor", 0)))
        except (ValueError, TypeError):
            return self._pages_error("音频游标无效")
        chunks = job["chunks"][cursor:cursor + 8]
        return jsonify({"success": True, "status": job["status"],
                        "chunks": chunks, "cursor": cursor + len(chunks),
                        "has_more": cursor + len(chunks) < len(job["chunks"]),
                        "audio_data": job.get("audio_data", "") if not chunks else "",
                        "error": job.get("error", "")})

    async def _pages_cancel_studio_job(self):
        data = await request.get_json(force=True) or {}
        job = self._studio_jobs().get(str(data.get("id") or ""))
        if job is None:
            return self._pages_error("试听任务不存在或已过期", 404)
        if not job["task"].done():
            job["task"].cancel()
            await asyncio.gather(job["task"], return_exceptions=True)
            if job["status"] == "queued":
                job["status"] = "cancelled"
                job["updated"] = time.monotonic()
        return jsonify({"success": True, "status": job["status"]})

    async def _run_studio_preview(self, job, text, options, streaming):
        job["status"] = "running"
        started = time.monotonic()
        try:
            async with asyncio.timeout(300):
                if streaming:
                    voice, result, segments, _ = await self._prepare_synthesis(text, **options)
                    if voice.type != "builtin":
                        raise ValueError("流式试听仅支持预置音色")
                    chunks = []
                    async with self._tts_sem:
                        client = self._acquire_client()
                        try:
                            async def consume_stream():
                                async with aclosing(client.stream_pcm(
                                    text=(voice.style_tags + " " + segments[0]).strip(),
                                    voice=voice.builtin_voice, context=result.context,
                                )) as stream:
                                    async for chunk in stream:
                                        chunks.append(chunk)
                                        job["chunks"].append(base64.b64encode(chunk).decode("ascii"))
                            # Shared limiter/circuit admission, without replay after partial audio.
                            await self._reliability.execute(consume_stream, retryable=lambda exc: False)
                        finally:
                            self._release_client(client)
                    buffer = io.BytesIO()
                    with wave.open(buffer, "wb") as audio:
                        audio.setnchannels(1)
                        audio.setsampwidth(2)
                        audio.setframerate(24000)
                        audio.writeframes(b"".join(chunks))
                    raw = buffer.getvalue()
                    output = pathlib.Path(self.data_dir) / "outputs" / f"mimo_tts_studio_{uuid.uuid4().hex}.wav"
                    output.parent.mkdir(parents=True, exist_ok=True)
                    output.write_bytes(raw)
                    self.studio_store.add_history(
                        text=text, mode=options["mode"], voice_id=voice.id, voice_name=voice.name,
                        voice_type=voice.type, model=voice.model, context=result.context,
                        speech_text=result.speech_text, file=output.name,
                        elapsed_ms=round((time.monotonic() - started) * 1000),
                    )
                else:
                    output = await self.synthesize_text(text, **options)
                    if output.stat().st_size > 24 * 1024 * 1024:
                        raise ValueError("音频超过 Web 试听 24 MB 上限，可在生成历史中管理")
                    raw = await asyncio.to_thread(output.read_bytes)
                job["audio_data"] = "data:audio/wav;base64," + base64.b64encode(raw).decode("ascii")
                job["status"] = "completed"
                await asyncio.to_thread(self._cleanup_outputs)
        except asyncio.CancelledError:
            job["status"] = "cancelled"
            job["chunks"].clear()
        except Exception as exc:
            job["status"] = "failed"
            job["error"] = str(exc)
        finally:
            job["updated"] = time.monotonic()

    async def _pages_export_studio(self):
        data = await request.get_json(force=True) or {}
        ids = data.get("voice_ids", [])
        if not isinstance(ids, list) or any(not isinstance(key, str) for key in ids):
            return self._pages_error("音色列表无效")
        try:
            package = export_package(self.voice_store, self.studio_store, ids,
                                     include_audio=data.get("include_audio") is True)
        except (ValueError, OSError) as exc:
            return self._pages_error(str(exc))
        return jsonify({"success": True, "package": package})

    async def _pages_import_studio(self):
        data = await request.get_json(force=True) or {}
        try:
            ids = import_package(self.voice_store, self.studio_store, data.get("package"),
                                 consent=data.get("consent_confirmed") is True)
        except (ValueError, OSError) as exc:
            return self._pages_error(str(exc))
        return jsonify({"success": True, "voice_ids": ids})

    async def _pages_duplicate_voice(self):
        data = await request.get_json(force=True) or {}
        try:
            voice = self.voice_store.duplicate_voice(str(data.get("id") or ""))
        except ValueError as exc:
            return self._pages_error(str(exc))
        return jsonify({"success": True, "voice": voice.to_dict()})

    async def _pages_studio_catalog(self):
        return jsonify({
            "success": True, "models": MODELS, "builtin_voices": BUILTIN_VOICES,
            "styles": self.studio_store.settings("styles"),
            "sessions": self.studio_store.settings("sessions"),
            "defaults": self.studio_store.settings("defaults"),
            "history_policy": self.studio_store.history_policy(),
        })

    async def _pages_create_voice(self):
        data = await request.get_json(force=True) or {}
        try:
            kind = str(data.get("type") or "builtin")
            if kind == "clone":
                return self._pages_error("克隆音色请使用参考音频上传")
            voice = self.voice_store.add_voice(
                name=str(data.get("name") or "").strip(), audio_path="",
                description=str(data.get("description") or ""), created_by="pages",
                consent_confirmed=False, type=kind,
                builtin_voice=str(data.get("builtin_voice") or ""),
                design_prompt=str(data.get("design_prompt") or ""),
                style_context=str(data.get("style_context") or ""),
            )
        except ValueError as exc:
            return self._pages_error(str(exc))
        return jsonify({"success": True, "voice": voice.to_dict()})

    async def _pages_studio_generate(self):
        data = await request.get_json(force=True) or {}
        try:
            draft = data.get("draft")
            voice = None
            if draft:
                if not isinstance(draft, dict) or draft.get("type") not in {"builtin", "design"}:
                    raise ValueError("未保存试听仅支持预置或设计音色")
                validate_voice(draft["type"], str(draft.get("builtin_voice") or ""),
                               str(draft.get("design_prompt") or ""))
                voice = VoiceProfile.from_dict({
                    **draft, "id": "draft", "name": draft.get("name") or "未保存音色",
                    "audio_path": "", "enabled": True,
                })
            style_id = str(data.get("style_id") or "")
            style = self.studio_store.settings("styles").get(style_id, {})
            context = str(data.get("context") or style.get("context") or "")
            jobs = self._studio_jobs()
            count = int(data.get("candidates", 1))
            selected = voice or self._usable_voice(str(data.get("voice_id") or ""))
            if count not in {1, 2, 3} or (count > 1 and (selected is None or selected.type != "design")):
                raise ValueError("批量候选仅支持设计音色，数量为 1–3")
            if sum(not job["task"].done() for job in jobs.values()) + count > 4:
                raise ValueError("试听任务已满，请等待或取消已有任务")
            # Keep completed audio bounded even when a client never polls its result.
            while len(jobs) + count > 8:
                for key, old in list(jobs.items()):
                    if old["task"].done():
                        del jobs[key]
                        break
            options = dict(voice_id=str(data.get("voice_id") or "") or None,
                emotion=str(data.get("emotion") or "") or None,
                voice_profile=VoiceProfile.from_dict(selected.to_dict()) if selected else voice,
                mode=str(data.get("mode") or "speech"),
                context=context, split=False,
                style_director_enabled=bool(data.get("director", False)),
            )
            keys = []
            for _ in range(count):
                key = uuid.uuid4().hex
                job = {"status": "queued", "chunks": [], "updated": time.monotonic()}
                job["task"] = asyncio.create_task(self._run_studio_preview(
                    job, str(data.get("text") or ""), dict(options), data.get("stream") is True,
                ))
                jobs[key] = job
                keys.append(key)
            return jsonify({"success": True, "job_id": keys[0], "job_ids": keys})
        except (ValueError, RuntimeError, TypeError) as exc:
            return self._pages_error(str(exc))

    async def _pages_generation_history(self):
        try:
            page = int(request.args.get("page", 1))
        except (TypeError, ValueError):
            return self._pages_error("页码无效")
        return jsonify({"success": True, **self.studio_store.history(page)})

    async def _pages_history_audio(self):
        item = self.studio_store.history_item(str(request.args.get("id") or ""))
        path = self.studio_store.audio_path(item) if item else None
        if path is None:
            return self._pages_error("音频不存在或已过期", 404)
        raw = await asyncio.to_thread(path.read_bytes)
        return jsonify({"success": True,
                        "audio_data": "data:audio/wav;base64," + base64.b64encode(raw).decode()})

    async def _pages_delete_history(self):
        data = await request.get_json(force=True) or {}
        if data.get("all") and data.get("confirm") is True:
            self.studio_store.delete_history()
        elif data.get("id"):
            self.studio_store.delete_history(str(data["id"]))
        else:
            return self._pages_error("清空历史需要确认")
        return jsonify({"success": True})

    async def _pages_save_studio_setting(self):
        data = await request.get_json(force=True) or {}
        category = str(data.get("category") or "")
        value = data.get("value")
        key = str(data.get("id") or "")
        if category not in {"styles", "sessions", "defaults"}:
            return self._pages_error("设置类型无效")
        if value is not None:
            if not isinstance(value, dict):
                return self._pages_error("设置格式无效")
            allowed = {
                "styles": {"name", "context"},
                "sessions": {"voice_id", "style_id", "auto_tts_enabled", "auto_tts_probability", "director"},
                "defaults": {"max_records", "retention_days"} if key == "history" else {"voice_id", "style_id"},
            }[category]
            value = {k: v for k, v in value.items() if k in allowed}
            if category == "defaults" and key == "history":
                try:
                    value = {"max_records": int(value.get("max_records", 1000)),
                             "retention_days": int(value.get("retention_days", 30))}
                    if not 10 <= value["max_records"] <= 10000 or not 1 <= value["retention_days"] <= 365:
                        raise ValueError()
                except (TypeError, ValueError):
                    return self._pages_error("历史保留条数应为 10–10000，天数应为 1–365")
            if value.get("style_id") and value["style_id"] not in self.studio_store.settings("styles"):
                return self._pages_error("风格不存在")
            if category == "styles":
                if not str(value.get("name") or "").strip() or len(str(value.get("context") or "")) > 4000:
                    return self._pages_error("风格需填写名称，演绎要求最多 4000 字")
            if category == "sessions":
                if "auto_tts_probability" in value:
                    try:
                        probability = float(value["auto_tts_probability"])
                        if not math.isfinite(probability) or not 0 <= probability <= 1:
                            raise ValueError()
                        value["auto_tts_probability"] = probability
                    except (TypeError, ValueError):
                        return self._pages_error("概率无效")
                for field in ("auto_tts_enabled", "director"):
                    if field in value and not isinstance(value[field], bool):
                        return self._pages_error("开关值必须为布尔值")
            if value.get("voice_id"):
                voice = self._usable_voice(str(value["voice_id"]))
                if voice is None or (category == "defaults" and key == "sing" and voice.type != "builtin"):
                    return self._pages_error("音色不可用")
        try:
            if category == "styles" and value is None:
                for owner in ("sessions", "defaults"):
                    if any(item.get("style_id") == key for item in self.studio_store.settings(owner).values()):
                        return self._pages_error("此风格仍被会话或默认设置引用，请先解除绑定")
            self.studio_store.save_setting(category, key, value)
        except ValueError as exc:
            return self._pages_error(str(exc))
        return jsonify({"success": True})

    async def _pages_history_to_clone(self):
        data = await request.get_json(force=True) or {}
        item = self.studio_store.history_item(str(data.get("id") or ""))
        path = self.studio_store.audio_path(item) if item else None
        if path is None:
            return self._pages_error("音频不存在或已过期", 404)
        if data.get("consent_confirmed") is not True:
            return self._pages_error("请确认参考音频使用授权")
        try:
            voice = await store_voice_sample(
                voice_store=self.voice_store, data_dir=self.data_dir,
                max_voice_file_bytes=self.plugin_config.max_voice_file_bytes,
                data=await asyncio.to_thread(path.read_bytes), filename=path.name,
                metadata={"name": str(data.get("name") or item["voice_name"]),
                          "consent_confirmed": True,
                          "description": f"生成历史来源：{item['id']}"},
            )
            voice = self.voice_store.update_voice(voice.id, source_history_id=item["id"])
        except ValueError as exc:
            return self._pages_error(str(exc))
        return jsonify({"success": True, "voice": voice.to_dict()})
