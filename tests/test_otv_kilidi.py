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

    def test_nearby_mtv_or_kdv_percentage_is_not_misclassified_as_otv(self):
        self.assertEqual([80], metindeki_otv_oranlari("MTV yüzde 20, ÖTV yüzde 80."))
        self.assertEqual([80], metindeki_otv_oranlari("ÖTV %80; MTV %20."))
        self.assertEqual([80], metindeki_otv_oranlari("KDV yüzde 20 ve ÖTV yüzde 80."))

    def test_lock_does_not_rewrite_a_nearby_mtv_rate(self):
        kilit = {"durum": "KESIN", "oran": 220, "izinli": [220]}
        metin = metni_otv_kilidine_cek("MTV yüzde 70, ÖTV yüzde 80.", kilit)

        self.assertIn("MTV yüzde 70", metin)
        self.assertIn("ÖTV yüzde 220", metin)
        self.assertEqual([220], metindeki_otv_oranlari(metin))

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
        """Tek oran kilitlenmediyse ÖTV geçişi metinden tamamen çıkarılır
        (04.10.2026: 'net ÖTV/matrah yoksa bundan bahsetmek zorunda değiliz')."""
        kilit = {"durum": "YASAK", "oran": None}
        yeni = metni_otv_kilidine_cek("ÖTV yüzde yetmiş dediler.", kilit)
        self.assertEqual([], metindeki_otv_oranlari(yeni))
        self.assertNotIn("ÖTV", yeni)

    def test_aralik_lock_removes_tax_clause_and_keeps_price(self):
        kilit = {"durum": "ARALIK", "izinli": [75, 80, 90, 100]}
        yeni = metni_otv_kilidine_cek(
            "Fiyatı 1.325.000 TL'den başlıyor, ÖTV matrah dilimine göre %75 veya %100 olabilir.", kilit,
        )
        self.assertIn("1.325.000 TL", yeni)
        self.assertEqual([], metindeki_otv_oranlari(yeni))
        self.assertNotIn("ÖTV", yeni)

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

    def test_aralik_kilitinde_oran_soyleyen_kanal_isaretlenir(self):
        """04.10.2026 politikası: matrah doğrulanmadıysa (ARALIK) içerikte ÖTV
        oranından HİÇ söz edilmez. Tek yanlış oran da kanonik iki uç da kanal
        başına işaretlenir; geçişler deterministik katmanda silinir."""
        kilit = tablo_orani("ice", 1300, None)  # 1400cc altı, matrah yok → ARALIK [70,75,80,90]
        self.assertEqual("ARALIK", kilit["durum"])
        fact = vergi_kilidini_uygula({}, kilit)
        reels = {"seslendirme_metni": "Bu araçta ÖTV matraha göre yüzde 70 veya yüzde 90 olabilir."}
        caption = {"reels_aciklamasi": "ÖTV matrah dilimine göre %70 veya %90."}
        threads = {"threads_aciklamasi": "Matraha göre %70 ya da %90."}
        sorunlar = otv_tutarlilik_sorunlari(reels, caption, threads, fact)
        self.assertEqual(3, len(sorunlar))
        self.assertTrue(all("ÖTV oranı söylüyor" in s for s in sorunlar))
        # Kilit katmanı uygulandığında tüm kanallar vergiden arınır ve QA geçer.
        reels["seslendirme_metni"] = metni_otv_kilidine_cek(reels["seslendirme_metni"], kilit)
        caption, threads = _otv_sosyal_kilitle(reels, caption, threads, fact, lambda *_: None)
        self.assertEqual([], otv_tutarlilik_sorunlari(reels, caption, threads, fact))
        qa = _otv_qa_zorla({"overall": "PASS", "regeneration_targets": []}, reels, caption, threads, fact, lambda *_: None)
        self.assertEqual("PASS", qa["overall"])
        self.assertEqual([], qa["regeneration_targets"])

    def test_aralik_kilidi_sosyal_kilitle_sonrasi_qa_pass(self):
        """Üretim hattının gerçek sırası: önce senaryo (TTS öncesi), sonra sosyal
        kilitle, en son QA zorla. Tüm kanallar vergiden arındıktan sonra PASS."""
        kilit = tablo_orani("ice", 1300, None)
        fact = vergi_kilidini_uygula({}, kilit)
        reels = {
            "seslendirme_metni": metni_otv_kilidine_cek("ÖTV yüzde yetmiş veya yüzde doksan olabilir.", kilit),
            "kapak_basliklari": [],
        }
        caption = {"reels_aciklamasi": "Vergi %170 diyor eski tablolar."}
        threads = {"threads_aciklamasi": "ÖTV yüzdesi yüzde yetmiş."}
        caption, threads = _otv_sosyal_kilitle(reels, caption, threads, fact, lambda *_: None)
        self.assertNotIn("ÖTV", caption["reels_aciklamasi"])
        self.assertNotIn("ÖTV", threads["threads_aciklamasi"])
        qa = _otv_qa_zorla({"overall": "PASS", "regeneration_targets": []}, reels, caption, threads, fact, lambda *_: None)
        self.assertEqual("PASS", qa["overall"])

    def test_aralikta_oran_soyleyen_seslendirme_fail_closed_kalir(self):
        """TTS sonrası ortaya çıkan/kaçan vergi oranı güvenlik ağıdır: ARALIK
        kilidinde oran söyleyen seslendirme temizlenmeden geçmemeli."""
        kilit = tablo_orani("ice", 1300, None)
        fact = vergi_kilidini_uygula({}, kilit)
        reels = {"seslendirme_metni": "ÖTV yüzde yetmiş veya yüzde doksan olabilir."}
        qa = _otv_qa_zorla({"overall": "PASS", "regeneration_targets": []}, reels, {}, {}, fact, lambda *_: None)
        self.assertEqual("FAIL", qa["overall"])
        self.assertIn("VOICEOVER_FAIL", qa["regeneration_targets"])

    def test_aralik_kanallar_farkli_uc_soylerse_hala_isaretlenir(self):
        """Seslendirme %70, açıklama %80: ARALIK'ta tek oran iddiası yasak
        olduğu için her iki kanal da işaretlenir (kanallar-arası çelişki
        kontrolüne gerek kalmaz, kanal-başı kural yeter)."""
        fact = {"vergi_kilidi": {"durum": "ARALIK", "izinli": [70, 80], "marka": "X"}}
        reels = {"seslendirme_metni": "ÖTV matraha göre yüzde 70 olabilir."}
        caption = {"reels_aciklamasi": "ÖTV matraha göre %80."}
        sorunlar = otv_tutarlilik_sorunlari(reels, caption, {}, fact)
        kanal_basi = [s for s in sorunlar if s.startswith(("seslendirme", "aciklama"))]
        self.assertEqual(2, len(kanal_basi))
        self.assertTrue(all("ÖTV oranı söylüyor" in s for s in kanal_basi))
        self.assertTrue(any("farklı ÖTV" in s for s in sorunlar))


if __name__ == "__main__":
    unittest.main()
