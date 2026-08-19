"""duo_audio.py için birim testleri.

Not: Gerçek TTS üretimi router.coklu_ses_uret çağrısı yaptığından mock gerektirir.
Bu testler yalnızca transcript hazırlığı ve performance tag döngüsünü doğrular;
TTS network çağrısını GitHub Actions üzerinde izole çalıştırmak pahalıdır.
"""
import unittest

import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from duo.duo_audio import _duo_transcript, _performance_tag


class DuoAudioTests(unittest.TestCase):
    def test_performance_tag_cycles_through_female_tags(self):
        tags = [_performance_tag("female", i) for i in range(5)]
        self.assertEqual(tags[0], "[curious]")
        self.assertEqual(tags[4], "[curious]")  # 4 mod 4 = 0
        self.assertNotEqual(tags[0], tags[1])

    def test_performance_tag_cycles_through_male_tags(self):
        tags = [_performance_tag("male", i) for i in range(5)]
        self.assertEqual(tags[0], "[confident]")
        self.assertEqual(tags[4], "[confident]")

    def test_transcript_marks_speakers_and_first_pause(self):
        segments = [
            {"speaker": "female", "text": "Merhaba."},
            {"speaker": "male", "text": "Selam, nasılsın?"},
            {"speaker": "female", "text": "İyiyim."},
        ]
        transcript = _duo_transcript(segments)
        lines = transcript.split("\n")
        self.assertEqual(len(lines), 3)
        # İlk satırda pause yok (önceki yok); sonraki satırlarda [short pause] var.
        self.assertNotIn("[short pause]", lines[0])
        self.assertIn("[short pause]", lines[1])
        self.assertIn("[short pause]", lines[2])
        # Speaker etiketleri doğru.
        self.assertTrue(lines[0].startswith("Autonoe:"))
        self.assertTrue(lines[1].startswith("Charon:"))
        self.assertTrue(lines[2].startswith("Autonoe:"))
        # Performance tag'ler eklenmiş.
        self.assertIn("[curious]", lines[0])
        self.assertIn("[confident]", lines[1])

    def test_transcript_skips_invalid_segments(self):
        segments = [
            {"speaker": "female", "text": "Geçerli."},
            {"speaker": "robot", "text": "Atılmalı."},
            {"speaker": "male", "text": ""},
            {"speaker": "male", "text": "Sonraki geçerli."},
        ]
        transcript = _duo_transcript(segments)
        lines = transcript.split("\n")
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("Autonoe:"))
        self.assertTrue(lines[1].startswith("Charon:"))

    def test_transcript_empty_for_no_segments(self):
        self.assertEqual(_duo_transcript([]), "")
        self.assertEqual(_duo_transcript(None), "")


if __name__ == "__main__":
    unittest.main()
