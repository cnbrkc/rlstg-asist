"""Hibrit ÖTV: tablo satırı karışmasın, seslendirme ile açıklama aynı rakamı kullansın."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

from core.otv_kilidi import (
    metindeki_otv_oranlari,
    metni_otv_kilidine_cek,
    otv_kilidi_hesapla,
    otv_tutarlilik_sorunlari,
    tablo_orani,
    vergi_kilidini_uygula,
)
from core.pipeline import _otv_qa_zorla, _otv_sosyal_kilitle


def _arac(variant="HEV", model="ES 300h"):
    return {"video_identity": {"brand": "Lexus", "exact_model": model, "variant": variant}}


class OtvTabloTests(unittest.TestCase):
    def test_25l_hev_under_100kw_is_220_not_70_or_170(self):
        """Kullanıcının araştırdığı durum: 2.5 hibrit, özel dilim şartı yok → %220."""
        kilit = tablo_orani("hev", 2487, 88)
        self.assertEqual("KESIN", kilit["durum"])
        self.assertEqual(220, kilit["oran"])

    def test_nominal_25_litre_from_text_locks_220(self):
        fact = {
            "facts": [
                {"fact": "Lexus ES 300h 2.5 litre tam hibrit.", "status": "VERIFIED"},
                {"fact": "Elektrik motoru 88 kW.", "status": "VERIFIED"},
                {"fact": "ÖTV yüzde yetmiş deniyor.", "status": "VERIFIED"},
            ]
        }
        web = (
            "Hibrit araçlarda ÖTV yüzde 70, yüzde 170 veya yüzde 220 olabilir. "
            "1800 cm³'ü geçmeyenler yüzde 70. 2500 cm³'ü geçmeyenler yüzde 170."
        )
        kilit = otv_kilidi_hesapla(fact, web, _arac())
        self.assertEqual("KESIN", kilit["durum"])
        self.assertEqual(220, kilit["oran"])

    def test_high_kw_25l_does_not_become_220(self):
        kilit = tablo_orani("hev", 2487, 120)
        self.assertEqual("ARALIK", kilit["durum"])
        self.assertEqual([150, 170], kilit["izinli"])
        self.assertNotEqual(220, kilit.get("oran"))

    def test_missing_kw_does_not_invent_a_single_rate(self):
        kilit = tablo_orani("hev", 2487, None)
        self.assertEqual("YASAK", kilit["durum"])
        self.assertIsNone(kilit["oran"])

    def test_small_hev_is_not_called_220(self):
        kilit = tablo_orani("hev", 1798, 70)
        self.assertEqual("ARALIK", kilit["durum"])
        self.assertEqual([70, 80], kilit["izinli"])

    def test_threshold_rows_are_not_the_vehicles_displacement(self):
        fact = {"facts": [{"fact": "Hibrit.", "status": "VERIFIED"}]}
        web = "Lexus ES 300h için tablo: motor silindir hacmi 1800 cm³'ü geçmeyenler yüzde 70."
        kilit = otv_kilidi_hesapla(fact, web, _arac())
        self.assertNotEqual(1800, kilit.get("motor_hacmi_cc"))
        self.assertNotEqual(70, kilit.get("oran"))


class OtvMetinTests(unittest.TestCase):
    def test_voice_and_caption_percent_words_are_detected(self):
        self.assertEqual([70], metindeki_otv_oranlari("Bu hibritte ÖTV yüzde yetmiş."))
        self.assertEqual([170], metindeki_otv_oranlari("Açıklamada vergi %170."))
        self.assertEqual([220], metindeki_otv_oranlari("ÖTV yüzde iki yüz yirmi."))
        self.assertEqual([], metindeki_otv_oranlari("KDV yüzde 20."))

    def test_lock_rewrites_70_and_170_to_220_in_both_channels(self):
        kilit = {"durum": "KESIN", "oran": 220, "izinli": [220]}
        ses = metni_otv_kilidine_cek("Bu hibritte ÖTV yüzde yetmiş.", kilit)
        aciklama = metni_otv_kilidine_cek("Açıklamada vergi %170 çıktı.", kilit)
        self.assertIn("iki yüz yirmi", ses)
        self.assertNotIn("yetmiş", ses)
        self.assertIn("%220", aciklama)
        self.assertNotIn("170", aciklama)
        self.assertEqual(metindeki_otv_oranlari(ses), metindeki_otv_oranlari(aciklama))

    def test_competitor_rate_is_left_alone(self):
        kilit = {"durum": "KESIN", "oran": 220, "marka": "Lexus"}
        rakip = metni_otv_kilidine_cek("BMW'de ÖTV yüzde 70.", kilit)
        konu = metni_otv_kilidine_cek("Lexus için ÖTV yüzde 170.", kilit)
        self.assertIn("70", rakip)
        self.assertNotIn("170", konu)
        self.assertIn("220", konu)

    def test_unknown_lock_removes_invented_percent(self):
        kilit = {"durum": "YASAK", "oran": None}
        yeni = metni_otv_kilidine_cek("ÖTV yüzde yetmiş dediler.", kilit)
        self.assertEqual([], metindeki_otv_oranlari(yeni))
        self.assertIn("tek oran yok", yeni)

    def test_fact_lock_drops_wrong_row_and_publishes_canonical_rate(self):
        fact = {
            "facts": [{"fact": "ÖTV yüzde 70.", "status": "VERIFIED"}],
            "turkiye_ilgi_sinyalleri": [{
                "kategori": "vergi",
                "bulgu": "Vergi %170.",
                "neden_turkiyede_ilginc": "x",
                "guvenli_anlatim": "ÖTV %70",
                "onem_puani": 8,
            }],
        }
        kilit = tablo_orani("hev", 2487, 88)
        yeni = vergi_kilidini_uygula(fact, kilit)
        metin = " ".join(f["fact"] for f in yeni["facts"])
        self.assertIn("%220", metin)
        self.assertEqual(220, yeni["vergi_kilidi"]["oran"])
        self.assertNotIn("70", yeni["turkiye_ilgi_sinyalleri"][0]["bulgu"])
        self.assertIn("220", yeni["turkiye_ilgi_sinyalleri"][0]["guvenli_anlatim"])


class OtvTutarlilikTests(unittest.TestCase):
    def test_70_voice_and_170_caption_fail_even_before_lock(self):
        sorunlar = otv_tutarlilik_sorunlari(
            {"seslendirme_metni": "ÖTV yüzde yetmiş."},
            {"reels_aciklamasi": "Vergi %170."},
            {},
            {},
        )
        self.assertTrue(any("farklı ÖTV" in s for s in sorunlar))

    def test_social_lock_makes_caption_match_voice_and_qa_passes(self):
        fact = vergi_kilidini_uygula({}, tablo_orani("hev", 2487, 88))
        reels = {"seslendirme_metni": "ÖTV yüzde iki yüz yirmi.", "kapak_basliklari": [{"ust": "ÖTV", "alt": "Vergi yüzde yetmiş"}]}
        caption = {"reels_aciklamasi": "Bu hibritte vergi %170."}
        threads = {"threads_aciklamasi": "Vergi yüzdesi yüzde 70."}
        caption, threads = _otv_sosyal_kilitle(reels, caption, threads, fact, lambda *_: None)
        self.assertEqual([220], metindeki_otv_oranlari(caption["reels_aciklamasi"]))
        self.assertEqual([220], metindeki_otv_oranlari(threads["threads_aciklamasi"]))
        self.assertEqual([220], metindeki_otv_oranlari(reels["kapak_basliklari"][0]["alt"]))
        qa = _otv_qa_zorla({"overall": "PASS", "regeneration_targets": []}, reels, caption, threads, fact, lambda *_: None)
        self.assertEqual("PASS", qa["overall"])

    def test_wrong_voiceover_still_forces_regeneration(self):
        fact = vergi_kilidini_uygula({}, tablo_orani("hev", 2487, 88))
        reels = {"seslendirme_metni": "ÖTV yüzde yetmiş."}
        caption = {"reels_aciklamasi": "ÖTV yüzde iki yüz yirmi."}
        qa = _otv_qa_zorla({"overall": "PASS", "regeneration_targets": []}, reels, caption, {}, fact, lambda *_: None)
        self.assertEqual("FAIL", qa["overall"])
        self.assertIn("VOICEOVER_FAIL", qa["regeneration_targets"])
        self.assertTrue(qa["fact_check"].startswith("FAIL:"))


if __name__ == "__main__":
    unittest.main()
