import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

from core.prompts import (
    caption_promptunu_olustur,
    editorial_promptunu_olustur,
    qa_promptunu_olustur,
    reels_creative_promptunu_olustur,
    threads_promptunu_olustur,
)


class ContentToneContractTests(unittest.TestCase):
    def test_tone_is_locked_in_every_editorial_output_prompt(self):
        builders = (
            editorial_promptunu_olustur,
            caption_promptunu_olustur,
            threads_promptunu_olustur,
            qa_promptunu_olustur,
        )
        for builder in builders:
            with self.subTest(builder=builder.__name__):
                prompt = builder("teknik")
                self.assertIn("RUNTIME İÇERİK TÜRÜ KİLİDİ", prompt)
                self.assertIn("Seçili tür: teknik", prompt)
                self.assertIn("yaklaşık %90 bilgi", prompt)

    def test_reels_prompt_uses_distinct_selected_profile(self):
        fun = reels_creative_promptunu_olustur(30, "eglence")
        informative = reels_creative_promptunu_olustur(30, "bilgi")
        self.assertIn("yaklaşık %25 bilgi", fun)
        self.assertIn("yaklaşık %75 bilgi", informative)
        self.assertIn("Seçili tür: eglence", fun)
        self.assertIn("Seçili tür: bilgi", informative)

    def test_unknown_tone_fails_safe_to_balanced(self):
        prompt = editorial_promptunu_olustur("bilinmeyen")
        self.assertIn("Seçili tür: dengeli", prompt)
        self.assertIn("yaklaşık %50 bilgi", prompt)


if __name__ == "__main__":
    unittest.main()
