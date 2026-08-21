import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

from core.pipeline import _editorial_oncelik_denetimi
from core.prompts import editorial_promptunu_olustur, qa_promptunu_olustur, reels_creative_promptunu_olustur, research_promptunu_olustur
from core.schemas import EDITORIAL_SCHEMA, FACT_LOCK_SCHEMA, QA_SCHEMA, REELS_CREATIVE_SCHEMA


class ViralResearchContractTests(unittest.TestCase):
    def test_research_always_checks_price_and_turkey_interest_for_known_model(self):
        prompt = research_promptunu_olustur()
        self.assertIn("fiyat ayrıca sorulmamış olsa bile", prompt)
        self.assertIn("TÜRKİYE İLGİ SİNYALLERİ", prompt)
        self.assertIn("en güçlü 3-6 adayı", prompt)
        self.assertIn("aynı fiyat avantajını koruyarak gelse", prompt)
        self.assertIn("Türkiye'de şu kadar olur/ucuz olur", prompt)
        self.assertNotIn("{bugunun_tarihi}", prompt)

    def test_fact_lock_requires_structured_turkey_interest_signals(self):
        self.assertIn("turkiye_ilgi_sinyalleri", FACT_LOCK_SCHEMA["required"])
        signal = FACT_LOCK_SCHEMA["properties"]["turkiye_ilgi_sinyalleri"]["items"]
        self.assertEqual(
            ["kategori", "bulgu", "neden_turkiyede_ilginc", "guvenli_anlatim", "onem_puani"],
            signal["required"],
        )


class EditorialPriorityTests(unittest.TestCase):
    def test_editorial_contract_scores_turkish_relevance_and_economic_impact(self):
        prompt = editorial_promptunu_olustur("dengeli")
        self.assertIn("TÜRKİYE İLGİ ÖNCELİĞİ", prompt)
        self.assertIn("GÖRSEL DESTEK ≠ KONU", prompt)
        self.assertIn("economic_or_practical_impact", prompt)
        self.assertIn("selected_story_index", EDITORIAL_SCHEMA["required"])

    def test_runtime_audit_marks_micro_detail_priority_mismatch(self):
        logs = []
        state = {
            "story_options": [
                {"isim": "Ucuz global fiyat", "kategori": "fiyat_deger", "toplam_oncelik": 9.4},
                {"isim": "Kapı detayı", "kategori": "tasarim", "toplam_oncelik": 5.1},
            ],
            "selected_story_index": 1,
        }
        audited = _editorial_oncelik_denetimi(state, logs.append)
        audit = audited["_runtime_priority_audit"]
        self.assertEqual("review", audit["status"])
        self.assertEqual("fiyat_deger", audit["top_category"])
        self.assertEqual(4.3, audit["score_gap"])
        self.assertTrue(any("öncelik sapması" in line for line in logs))

    def test_runtime_audit_accepts_highest_scored_story(self):
        state = {
            "story_options": [
                {"isim": "Fiyat", "kategori": "fiyat_deger", "toplam_oncelik": 9},
                {"isim": "Kapı", "kategori": "tasarim", "toplam_oncelik": 6},
            ],
            "selected_story_index": 0,
        }
        audited = _editorial_oncelik_denetimi(state, lambda _msg: None)
        self.assertEqual("aligned", audited["_runtime_priority_audit"]["status"])


class CreativeAndQaPriorityTests(unittest.TestCase):
    def test_reels_must_carry_selected_turkey_hook(self):
        prompt = reels_creative_promptunu_olustur(30, "dengeli")
        self.assertIn("TÜRKİYE İLGİ KANCASI", prompt)
        self.assertIn("kapı/far/ekran gibi daha zayıf", prompt)
        self.assertIn("turkiye_ilgi_kancasi", REELS_CREATIVE_SCHEMA["required"])
        self.assertIn("ana_hikaye_sadakat_kontrolu", REELS_CREATIVE_SCHEMA["required"])

    def test_qa_fails_when_micro_detail_hides_stronger_verified_signal(self):
        prompt = qa_promptunu_olustur("dengeli")
        self.assertIn("viral_priority_check", prompt)
        self.assertIn("kapı/far/ekran gibi zayıf mikro detaya", prompt)
        self.assertIn("viral_priority_check", QA_SCHEMA["required"])


if __name__ == "__main__":
    unittest.main()
