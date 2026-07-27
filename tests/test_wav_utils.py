from pathlib import Path
import sys
import tempfile
import unittest
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_mimo_tts_clone.core.wav_utils import WavMergeError, merge_wav_files


def _write_wav(path: Path, frames: int, *, rate: int = 8000) -> None:
    with wave.open(str(path), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(b"\1\0" * frames)


class WavUtilsTests(unittest.TestCase):
    def test_merge_preserves_all_frames(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first, second, output = root / "a.wav", root / "b.wav", root / "out.wav"
            _write_wav(first, 4)
            _write_wav(second, 7)

            merge_wav_files([first, second], output)

            with wave.open(str(output), "rb") as merged:
                self.assertEqual(merged.getnframes(), 11)
                self.assertEqual(merged.getframerate(), 8000)
            self.assertFalse((root / "out.wav.part").exists())

    def test_merge_rejects_incompatible_parameters_without_residue(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first, second, output = root / "a.wav", root / "b.wav", root / "out.wav"
            _write_wav(first, 2, rate=8000)
            _write_wav(second, 2, rate=16000)

            with self.assertRaises(WavMergeError):
                merge_wav_files([first, second], output)

            self.assertFalse(output.exists())
            self.assertFalse((root / "out.wav.part").exists())
