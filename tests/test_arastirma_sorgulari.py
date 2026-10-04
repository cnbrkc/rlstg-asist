"""Araştırma sorguları: 'UNKNOWN' yer tutucusu ve basitleştirilmiş yeniden deneme.

Kök neden (01.10.2026 CI logu): Forensic şema modelden emin olamadığında
'UNKNOWN' yazmasını istiyor. Bu değer sorguya sızınca DDGS sorguyu terim sayıp
"No results found" dönüyordu:

    Kia Seltos UNKNOWN kullanıcı şikayet...  → 4 sonuç
    Kia Seltos UNKNOWN Türkiye fiyat satış    → No results found
    Kia Seltos UNKNOWN özellikleri teknik     → No results found
    Kia Seltos UNKNOWN Türkiye ÖTV oranı      → No results found

Fiyat/ÖTV sorguları sonuçsuz kalınca Fact Lock'ta fiyat alanı boş kaldı, ÖTV
kilidi matrahı bulamayınca ARALIK'a düştü ve seslendirme ile açıklama kelime
bütçesini "matrah dilimine göre %75 veya %80 veya %90 veya %100" bracket'larına
harcadı — fiyat hiç söylenmedi.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

import core.web_search as web_search


def _video_state(**kimlik):
    taban = {
        "video_identity": {
            "brand": "Kia", "exact_model": "Seltos", "variant": "UNKNOWN",
            "confidence": "medium",
        },
        "observed_facts": [],
        "viral_arastirma_ihtiyaclari": [
            "Türkiye'deki Kia Seltos UNKNOWN fiyatlandırması nasıl olacak?",
        ],
    }
    taban["video_identity"].update(kimlik)
    return taban


class YerTutucuAykilamaTests(unittest.TestCase):
    def test_sorgularda_unknown_tokeni_kalmamali(self):
        sorgular = web_search.arastirma_sorgulari_olustur(_video_state())
        self.assertTrue(sorgular)
        for sorgu in sorgular:
            self.assertNotIn("UNKNOWN", sorgu.upper())
        self.assertIn("Kia Seltos Türkiye fiyat satış", sorgular)

    def test_bilinmiyor_ve_belirsiz_de_ayiklanir(self):
        sorgular = web_search.arastirma_sorgulari_olustur(_video_state(variant="bilinmiyor"))
        self.assertTrue(all("bilinmiyor" not in s.casefold() for s in sorgular))

    def test_model_bilinmiyorsa_marka_ile_devam_edilir(self):
        sorgular = web_search.arastirma_sorgulari_olustur(_video_state(exact_model="UNKNOWN"))
        self.assertTrue(sorgular)
        self.assertTrue(all(s.startswith("Kia ") for s in sorgular))

    def test_marka_bilinmiyorsa_sorgu_uretilmez(self):
        self.assertEqual([], web_search.arastirma_sorgulari_olustur(_video_state(brand="UNKNOWN")))

    def test_otv_ek_arastirma_tirnak_kullanmaz_ve_unknown_icermez(self):
        cagrilan = []

        def fake_sorgu(sorgu, max_sonuc=4, log_ekle=None, hata_bildir=None):
            cagrilan.append(sorgu)
            return [{"baslik": "t", "icerik": "i", "kaynak": ""}]

        with patch.object(web_search, "duckduckgo_sorgu", side_effect=fake_sorgu):
            metin = web_search.otv_ek_arastirma(_video_state(), lambda m: None)
        self.assertTrue(cagrilan)
        for sorgu in cagrilan:
            self.assertNotIn('"', sorgu)
            self.assertNotIn("UNKNOWN", sorgu.upper())
        self.assertIn("Kia Seltos motor hacmi cc", metin)

    def test_otv_ek_arastirma_model_yoksa_marka_ile_duzer(self):
        cagrilan = []

        def fake_sorgu(sorgu, max_sonuc=4, log_ekle=None, hata_bildir=None):
            cagrilan.append(sorgu)
            return []

        with patch.object(web_search, "duckduckgo_sorgu", side_effect=fake_sorgu):
            web_search.otv_ek_arastirma(_video_state(exact_model="UNKNOWN"), lambda m: None)
        self.assertTrue(cagrilan)
        self.assertTrue(all(s.startswith("Kia ") for s in cagrilan))


class BasitlestirilmisYenidenDenemeTests(unittest.TestCase):
    def test_sonucsuz_uzun_sorgu_kisaltilip_tekrar_denenir(self):
        state = _video_state()
        cagri = {}
        uzun = "Kia Seltos kullanıcı şikayet sorun gizli kusur"

        def fake_sorgu(sorgu, max_sonuc=4, log_ekle=None, hata_bildir=None):
            cagri[sorgu] = cagri.get(sorgu, 0) + 1
            # Uzun/terim ağırlıklı sorgu sonuçsuz; kısaltılmış hâli veri getirir.
            if sorgu == uzun:
                return []
            if sorgu == "Kia Seltos kullanıcı şikayet sorun gizli":
                return [{"baslik": "Kia Seltos şikayet", "icerik": "Kullanıcılar 1599 cc motoru eleştiriyor.", "kaynak": "u"}]
            return [{"baslik": "x", "icerik": "y", "kaynak": ""}]

        logs = []
        with patch.object(web_search, "duckduckgo_sorgu", side_effect=fake_sorgu), \
             patch.dict(os.environ, {"WEB_SEARCH_PARALLEL": "1"}):
            metin = web_search.web_arastirma_yap(state, logs.append)
        self.assertIn(uzun, web_search.arastirma_sorgulari_olustur(state))
        self.assertIn("1599 cc", metin)
        self.assertTrue(any("basitleştirilip tekrar deneniyor" in l for l in logs))
        self.assertEqual(cagri.get("Kia Seltos kullanıcı şikayet sorun gizli"), 1)

    def test_ayni_kalan_sorgu_tekrar_denenmez(self):
        """Kısaltma sorguyu değiştirmiyorsa (zaten kısa sorgu) ikinci deneme yok."""
        state = _video_state()
        cagri = {}

        def fake_sorgu(sorgu, max_sonuc=4, log_ekle=None, hata_bildir=None):
            cagri[sorgu] = cagri.get(sorgu, 0) + 1
            return []

        with patch.object(web_search, "duckduckgo_sorgu", side_effect=fake_sorgu), \
             patch.dict(os.environ, {"WEB_SEARCH_PARALLEL": "1"}):
            web_search.web_arastirma_yap(state, lambda m: None)
        # "Kia Seltos Türkiye fiyat satış" zaten kısadır: aynen tek denenir.
        self.assertEqual(cagri.get("Kia Seltos Türkiye fiyat satış"), 1)

    def test_sonuclu_sorgulara_ikinci_deneme_yapilmaz(self):
        state = _video_state()
        cagri = {}

        def fake_sorgu(sorgu, max_sonuc=4, log_ekle=None, hata_bildir=None):
            cagri[sorgu] = cagri.get(sorgu, 0) + 1
            return [{"baslik": "x", "icerik": "y", "kaynak": ""}]

        with patch.object(web_search, "duckduckgo_sorgu", side_effect=fake_sorgu), \
             patch.dict(os.environ, {"WEB_SEARCH_PARALLEL": "1"}):
            web_search.web_arastirma_yap(state, lambda m: None)
        self.assertTrue(cagri)
        self.assertTrue(all(v == 1 for v in cagri.values()), cagri)


if __name__ == "__main__":
    unittest.main()
