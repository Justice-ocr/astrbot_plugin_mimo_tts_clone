from __future__ import annotations

import asyncio
import base64
import binascii
import io
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from .model_catalog import MODELS, BUILTIN_VOICES


@dataclass(slots=True)
class MimoTTSConfig:
    api_key: str
    base_url: str = "https://api.xiaomimimo.com/v1"
    model: str = "mimo-v2.5-tts-voiceclone"
    output_format: str = "wav"
    timeout: float = 120.0
    max_retries: int = 2


class MimoAPIError(RuntimeError):
    pass


class MimoAuthenticationError(MimoAPIError):
    pass


class MimoRateLimitError(MimoAPIError):
    pass


class MimoTransientError(MimoAPIError):
    pass


class MimoInvalidResponseError(MimoAPIError):
    pass


class MimoOfficialClient:
    def __init__(self, config: MimoTTSConfig):
        self.config = config
        self._openai_client: Any = None
        self._client_lock = asyncio.Lock()

    async def _get_client(self):
        if self._openai_client is not None:
            return self._openai_client
        async with self._client_lock:
            if self._openai_client is not None:
                return self._openai_client
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:
                raise RuntimeError("openai package is required. Install requirements.txt.") from exc
            self._openai_client = AsyncOpenAI(
                api_key=self.config.api_key,
                base_url=self.config.base_url,
                timeout=self.config.timeout,
                max_retries=self.config.max_retries,
            )
            return self._openai_client

    async def close(self) -> None:
        async with self._client_lock:
            client = self._openai_client
            self._openai_client = None
        if client is not None:
            await client.close()

    async def stream_pcm(self, *, text: str, voice: str, context: str = ""):
        """Yield bounded PCM16LE mono chunks; callers own playback and persistence."""
        if not self.config.api_key:
            raise RuntimeError("MIMO API Key is not configured.")
        payload = self.build_payload(
            text=text, voice_data_url=voice, context=context, model=MODELS["builtin"],
        )
        payload["audio"]["format"] = "pcm16"
        payload["stream"] = True
        client = await self._get_client()
        stream = await client.chat.completions.create(**payload)
        total = 0
        remainder = b""
        try:
            async for chunk in stream:
                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue
                if getattr(choices[0], "finish_reason", None) == "content_filter":
                    raise MimoInvalidResponseError("MiMo declined the request due to content filtering.")
                audio = getattr(getattr(choices[0], "delta", None), "audio", None)
                if not audio:
                    continue
                encoded = audio.get("data") if isinstance(audio, dict) else getattr(audio, "data", None)
                if not encoded:
                    continue
                try:
                    raw = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError, TypeError) as exc:
                    raise MimoInvalidResponseError("MiMo returned invalid streaming audio.") from exc
                total += len(raw)
                if total > 24 * 1024 * 1024:
                    raise MimoInvalidResponseError("Streaming audio exceeds the preview limit.")
                raw = remainder + raw
                boundary = len(raw) - len(raw) % 2
                remainder = raw[boundary:]
                # Bound each bridge/playback chunk even if the provider sends one large result.
                for offset in range(0, boundary, 48000):
                    yield raw[offset:min(offset + 48000, boundary)]
            if not total or remainder:
                raise MimoInvalidResponseError("MiMo returned empty or incomplete PCM audio.")
        finally:
            await stream.close()

    def build_payload(
        self,
        *,
        text: str,
        voice_data_url: str,
        context: str = "",
        model: str | None = None,
    ) -> dict[str, Any]:
        model = model or self.config.model
        if model not in MODELS.values():
            raise ValueError("Unsupported MiMo TTS model.")
        if model == MODELS["design"] and not context.strip():
            raise ValueError("VoiceDesign requires a voice description.")
        if model == MODELS["builtin"] and voice_data_url not in BUILTIN_VOICES:
            raise ValueError("Invalid built-in voice.")
        messages: list[dict[str, str]] = []
        if context.strip():
            messages.append({"role": "user", "content": context.strip()})
        messages.append({"role": "assistant", "content": text})
        payload = {
            "model": model,
            "messages": messages,
            "audio": {
                "format": self.config.output_format,
                "voice": voice_data_url,
            },
        }
        if model == MODELS["design"]:
            payload["audio"].pop("voice")
        return payload

    async def synthesize_to_file(
        self,
        *,
        text: str,
        voice_data_url: str,
        output_path: str | Path,
        context: str = "",
        model: str | None = None,
    ) -> Path:
        if not self.config.api_key:
            raise RuntimeError("MIMO API Key is not configured.")
        payload = self.build_payload(
            text=text,
            voice_data_url=voice_data_url,
            context=context,
            model=model,
        )
        client = await self._get_client()
        try:
            completion = await client.chat.completions.create(**payload)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status in {401, 402, 403}:
                raise MimoAuthenticationError(f"MiMo authentication or account error ({status}).") from exc
            if status == 429:
                raise MimoRateLimitError("MiMo rate limit exceeded after retries.") from exc
            if status in {408, 409} or (isinstance(status, int) and status >= 500):
                raise MimoTransientError(f"MiMo service is temporarily unavailable ({status}).") from exc
            if isinstance(exc, (TimeoutError, ConnectionError)) or type(exc).__name__ in {
                "APIConnectionError",
                "APITimeoutError",
            }:
                raise MimoTransientError("MiMo request timed out or the connection failed.") from exc
            raise
        choices = getattr(completion, "choices", None)
        if not choices:
            raise MimoInvalidResponseError("MiMo API returned no completion choices.")
        finish_reason = getattr(choices[0], "finish_reason", None)
        if finish_reason == "content_filter":
            raise MimoInvalidResponseError("MiMo declined the request due to content filtering.")
        message = getattr(choices[0], "message", None)
        if message is None:
            raise MimoInvalidResponseError("MiMo API returned no completion message.")
        audio = getattr(message, "audio", None)
        audio_data = getattr(audio, "data", None)
        if not isinstance(audio_data, str) or not audio_data:
            raise MimoInvalidResponseError("MiMo API returned no audio data.")

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            raw = base64.b64decode(audio_data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise MimoInvalidResponseError("MiMo API returned invalid Base64 audio data.") from exc
        if not raw.startswith(b"RIFF") or raw[8:12] != b"WAVE":
            raise MimoInvalidResponseError("MiMo API returned invalid WAV audio data.")
        try:
            with wave.open(io.BytesIO(raw), "rb") as source:
                if (
                    source.getnchannels() < 1
                    or source.getsampwidth() < 1
                    or source.getframerate() < 1
                    or source.getnframes() < 1
                ):
                    raise wave.Error("empty or invalid WAV parameters")
                source.readframes(1)
        except (EOFError, wave.Error) as exc:
            raise MimoInvalidResponseError("MiMo API returned malformed WAV audio data.") from exc
        temporary = out.with_suffix(out.suffix + ".part")
        try:
            await asyncio.to_thread(temporary.write_bytes, raw)
            await asyncio.to_thread(temporary.replace, out)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return out
