import os
import tempfile
import wave
import unittest

from duo_audio import _wavleri_birlestir


def _wav(path, frames, rate=48000):
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * frames)


class DuoAudioTests(unittest.TestCase):
    def test_wav_segments_are_concatenated_without_reencoding(self):
        with tempfile.TemporaryDirectory() as d:
            a = os.path.join(d, "a.wav")
            b = os.path.join(d, "b.wav")
            out = os.path.join(d, "out.wav")
            _wav(a, 4800)
            _wav(b, 9600)
            self.assertTrue(_wavleri_birlestir([a, b], out))
            with wave.open(out, "rb") as wf:
                self.assertEqual(wf.getnchannels(), 1)
                self.assertEqual(wf.getsampwidth(), 2)
                self.assertEqual(wf.getframerate(), 48000)
                self.assertEqual(wf.getnframes(), 14400)

    def test_mismatched_formats_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            a = os.path.join(d, "a.wav")
            b = os.path.join(d, "b.wav")
            out = os.path.join(d, "out.wav")
            _wav(a, 100, 48000)
            _wav(b, 100, 44100)
            self.assertFalse(_wavleri_birlestir([a, b], out))


if __name__ == "__main__":
    unittest.main()
