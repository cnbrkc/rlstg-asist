import os
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

from core import pipeline
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


class ParallelSocialOutputTests(unittest.TestCase):
    def test_caption_and_threads_execute_concurrently(self):
        caption_started = threading.Event()
        threads_started = threading.Event()

        def caption(*args, **kwargs):
            caption_started.set()
            self.assertTrue(threads_started.wait(0.75), "Threads kolu Caption ile eşzamanlı başlamadı")
            time.sleep(0.03)
            return {"reels_aciklamasi": "caption", "reels_hashtagleri": ["otoXtra"]}, "caption-model"

        def threads(*args, **kwargs):
            threads_started.set()
            self.assertTrue(caption_started.wait(0.75), "Caption kolu Threads ile eşzamanlı başlamadı")
            time.sleep(0.03)
            return {"threads_aciklamasi": "threads"}, "threads-model"

        logs = []
        with patch.object(pipeline, "_caption_calistir", side_effect=caption), patch.object(
            pipeline, "_threads_calistir", side_effect=threads
        ):
            cap, cap_model, thr, thr_model = pipeline._sosyal_ciktilari_paralel_uret(
                object(), {}, {}, {}, {}, logs.append, "teknik"
            )

        self.assertEqual("caption", cap["reels_aciklamasi"])
        self.assertEqual("threads", thr["threads_aciklamasi"])
        self.assertEqual("caption-model", cap_model)
        self.assertEqual("threads-model", thr_model)
        self.assertTrue(any("paralel duvar süresi" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
