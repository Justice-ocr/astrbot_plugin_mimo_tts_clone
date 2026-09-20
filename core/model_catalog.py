"""MiMo capabilities verified against the official guide on 2026-09-20."""

MODELS = {
    "builtin": "mimo-v2.5-tts",
    "design": "mimo-v2.5-tts-voicedesign",
    "clone": "mimo-v2.5-tts-voiceclone",
}
BUILTIN_VOICES = (
    "mimo_default", "冰糖", "茉莉", "苏打", "白桦",
    "Mia", "Chloe", "Milo", "Dean",
)


def validate_voice(kind: str, builtin_voice: str, design_prompt: str) -> None:
    if kind not in MODELS:
        raise ValueError("不支持的音色类型")
    if kind == "builtin" and builtin_voice not in BUILTIN_VOICES:
        raise ValueError("请选择有效的官方预置音色")
    if kind == "design" and not design_prompt.strip():
        raise ValueError("设计音色需要声音描述")


def singing_text(text: str) -> str:
    import re

    text = re.sub(
        r"^(?:(?:\(唱歌\)|（唱歌）|\[唱歌\]|\((?:sing|singing)\))\s*)+",
        "", text.strip(), flags=re.I,
    )
    if not text:
        raise ValueError("请输入歌词")
    return "(唱歌)" + text
