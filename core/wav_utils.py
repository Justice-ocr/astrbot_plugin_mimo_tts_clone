from __future__ import annotations

import wave
from pathlib import Path


class WavMergeError(ValueError):
    """Raised when generated WAV parts cannot be combined safely."""


def merge_wav_files(parts: list[Path], output_path: Path) -> Path:
    if not parts:
        raise WavMergeError("No WAV parts were provided.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".part")
    params: tuple[int, int, int, str, str] | None = None
    try:
        for part in parts:
            with wave.open(str(part), "rb") as source:
                current = (
                    source.getnchannels(),
                    source.getsampwidth(),
                    source.getframerate(),
                    source.getcomptype(),
                    source.getcompname(),
                )
                if params is None:
                    params = current
                elif current != params:
                    raise WavMergeError("Generated WAV parts use incompatible audio parameters.")

        assert params is not None
        with wave.open(str(temporary), "wb") as destination:
            destination.setnchannels(params[0])
            destination.setsampwidth(params[1])
            destination.setframerate(params[2])
            destination.setcomptype(params[3], params[4])
            for part in parts:
                with wave.open(str(part), "rb") as source:
                    while payload := source.readframes(65536):
                        destination.writeframesraw(payload)
        temporary.replace(output_path)
        return output_path
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
