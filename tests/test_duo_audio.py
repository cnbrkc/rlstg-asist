"""duo_audio.py clean multi-speaker transcript tests.

The TTS network call is isolated elsewhere. These tests ensure the approved
spoken text is speaker-labelled without imposing repetitive performance tags
or identical pauses that can create a staged duet cadence.
"""
import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from duo.duo_audio import _duo_transcript


class DuoAudioTests(unittest.TestCase):
    def test_transcript_marks_speakers_without_forced_per_turn_direction(self):
        segments = [
            {"speaker": "female", "text": "Bu fiyat gerçek mi?"},
            {"speaker": "male", "text": "Fiyat gerçek, ama Türkiye fiyatı değil."},
            {"speaker": "female", "text": "Tamam, asıl fark da orada zaten."},
        ]
        lines = _duo_transcript(segments).split("\n")
        self.assertEqual([
            "Autonoe: Bu fiyat gerçek mi?",
            "Charon: Fiyat gerçek, ama Türkiye fiyatı değil.",
            "Autonoe: Tamam, asıl fark da orada zaten.",
        ], lines)
        transcript = "\n".join(lines)
        self.assertNotIn("[short pause]", transcript)
        self.assertNotIn("[curious]", transcript)
        self.assertNotIn("[confident]", transcript)

    def test_transcript_preserves_consecutive_speaker_turns(self):
        segments = [
            {"speaker": "male", "text": "Bir saniye."},
            {"speaker": "male", "text": "Rakamın devamı daha ilginç."},
            {"speaker": "female", "text": "İşte şimdi oldu."},
        ]
        self.assertEqual(
            _duo_transcript(segments).split("\n"),
            [
                "Charon: Bir saniye.",
                "Charon: Rakamın devamı daha ilginç.",
                "Autonoe: İşte şimdi oldu.",
            ],
        )

    def test_transcript_skips_invalid_segments(self):
        segments = [
            {"speaker": "female", "text": "Geçerli."},
            {"speaker": "robot", "text": "Atılmalı."},
            {"speaker": "male", "text": ""},
            {"speaker": "male", "text": "Sonraki geçerli."},
        ]
        lines = _duo_transcript(segments).split("\n")
        self.assertEqual(["Autonoe: Geçerli.", "Charon: Sonraki geçerli."], lines)

    def test_transcript_empty_for_no_segments(self):
        self.assertEqual(_duo_transcript([]), "")
        self.assertEqual(_duo_transcript(None), "")


if __name__ == "__main__":
    unittest.main()
