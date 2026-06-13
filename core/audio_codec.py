from __future__ import annotations

import base64
import mimetypes
from pathlib import Path


class AudioValidationError(ValueError):
    """Raised when a voice sample cannot be sent to the MiMo API."""


SUPPORTED_AUDIO_MIME = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
}


def detect_audio_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in SUPPORTED_AUDIO_MIME:
        return SUPPORTED_AUDIO_MIME[suffix]
    guessed = mimetypes.guess_type(str(path))[0] or ""
    if guessed in SUPPORTED_AUDIO_MIME.values():
        return guessed
    raise AudioValidationError("Unsupported audio format. Use mp3 or wav.")


def estimate_base64_chars(byte_count: int) -> int:
    return 4 * ((max(0, int(byte_count)) + 2) // 3)


def validate_audio_header(path: Path, mime_type: str) -> None:
    header = path.read_bytes()[:12]
    if mime_type == "audio/wav":
        if len(header) < 12 or not (header.startswith(b"RIFF") and header[8:12] == b"WAVE"):
            raise AudioValidationError("Invalid wav audio header.")
        return
    if mime_type == "audio/mpeg":
        has_id3 = header.startswith(b"ID3")
        has_frame_sync = len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0
        if not (has_id3 or has_frame_sync):
            raise AudioValidationError("Invalid mp3 audio header.")
        return


def validate_voice_file(
    path: Path,
    *,
    max_bytes: int = 10 * 1024 * 1024,
    max_base64_chars: int | None = None,
) -> None:
    if not path.is_file():
        raise AudioValidationError(f"Voice file not found: {path}")
    mime_type = detect_audio_mime(path)
    size = path.stat().st_size
    if size <= 0:
        raise AudioValidationError("Voice file is empty.")
    if size > max_bytes:
        raise AudioValidationError(f"Voice file too large (max {max_bytes} bytes).")
    validate_audio_header(path, mime_type)
    base64_limit = max_base64_chars if max_base64_chars is not None else max_bytes
    if estimate_base64_chars(size) > base64_limit:
        raise AudioValidationError(f"Base64 audio payload too large (max {base64_limit} chars).")


def encode_voice_file_data_url(
    path: Path | str,
    *,
    max_bytes: int = 10 * 1024 * 1024,
    max_base64_chars: int | None = None,
) -> str:
    audio_path = Path(path)
    validate_voice_file(
        audio_path,
        max_bytes=max_bytes,
        max_base64_chars=max_base64_chars,
    )
    mime_type = detect_audio_mime(audio_path)
    data = audio_path.read_bytes()
    encoded = base64.b64encode(data).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"
