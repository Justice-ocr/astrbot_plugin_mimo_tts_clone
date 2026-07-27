from __future__ import annotations

import re


_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<(?!#\s*\d{1,2}(?:\.\d{1,2})?\s*#>)[^>\n]{1,80}>")
_CONTROL_LINE_RE = re.compile(r"^\s*(?:\[CQ:[^\]]+\]|\[[A-Z][A-Z0-9_-]{0,24}\])\s*$")
_WHITESPACE_RE = re.compile(r"[ \t\r\n]+")
_SENTENCE_RE = re.compile(r"[^。！？!?；;\n]+[。！？!?；;]?", re.MULTILINE)


class TextSegmentationError(ValueError):
    """Raised when text cannot fit within the configured segment budget."""


def clean_tts_text(text: str) -> str:
    content = str(text or "")
    if not content:
        return ""

    content = _CODE_BLOCK_RE.sub(" ", content)
    content = _INLINE_CODE_RE.sub(" ", content)
    content = _URL_RE.sub(" ", content)
    content = _HTML_TAG_RE.sub(" ", content)
    lines = [
        line
        for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if not _CONTROL_LINE_RE.match(line)
    ]
    content = " ".join(lines)
    content = _WHITESPACE_RE.sub(" ", content)
    return content.strip()


def split_tts_text(
    text: str,
    *,
    max_chars: int = 500,
    max_segments: int = 6,
) -> list[str]:
    content = clean_tts_text(text)
    if not content:
        return []
    if max_chars <= 0 or len(content) <= max_chars:
        return [content]

    raw_parts = [match.group(0).strip() for match in _SENTENCE_RE.finditer(content)]
    raw_parts = [part for part in raw_parts if part]
    if not raw_parts:
        raw_parts = [content]
    bounded_parts: list[str] = []
    for part in raw_parts:
        if len(part) <= max_chars:
            bounded_parts.append(part)
            continue
        bounded_parts.extend(
            part[index : index + max_chars]
            for index in range(0, len(part), max_chars)
        )

    segments: list[str] = []
    current = ""
    for part in bounded_parts:
        if not current:
            current = part
            continue
        if len(current) + len(part) <= max_chars:
            current += part
            continue
        segments.append(current)
        current = part
    if current:
        segments.append(current)

    if max_segments > 0 and len(segments) > max_segments:
        raise TextSegmentationError(
            f"Text requires {len(segments)} segments, exceeding the configured maximum of {max_segments}."
        )

    return segments
