"""[Kural: Reels Kapak Yazısı Formatı] regresyon testleri.

- Her üretimde TAM 5 farklı kapak alternatifi olmalı (tek başlık asla yeterli değil).
- Her alternatif iki katman: Üst (2-4 kelime, TAMAMI BÜYÜK HARF) + Alt (4-7 kelime, cümle düzeni).
- Ajan eksik/boş dönerse yerel olarak 5'e tamamlanır.
- Hook ajanı schema'sı kapak_basliklari alanını zorunlu kılar.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

from core.cover_titles import (
    ALT_MAX_KELIME,
    ALTERNATIF_SAYISI,
    UST_MAX_KELIME,
    alt_baslik_duzenle,
    alt_kurala_uygun_mu,
    kapak_basliklarini_normalize_et,
    kapak_basliklarini_metne_dok,
    ust_baslik_duzenle,
    ust_kurala_uygun_mu,
)
from core.schemas import HOOK_GEN_SCHEMA


class CoverFormatKuralTestleri(unittest.TestCase):
    def test_hook_schema_kapak_5_alternatifi_zorunlu_kilar(self):
        self.assertIn("kapak_basliklari", HOOK_GEN_SCHEMA["required"])
        props = HOOK_GEN_SCHEMA["properties"]
        desc = props["kapak_basliklari"]["description"].lower()
        self.assertIn("5", desc)
        item_req = props["kapak_basliklari"]["items"]["required"]
        self.assertEqual(["ust", "alt"], item_req)

    def test_ust_buyuk_ve_2_4_kelime(self):
        ust = ust_baslik_duzenle("işte ilginç bir durum")
        self.assertEqual(ust, ust.upper())  # Türkçe büyük harf + noktalama temizliği
        # 5+ kelimelik aday 4 kelimeye kırpılır
        ust2 = ust_baslik_duzenle("Bu SUV Türkiye'de Böyle Satılmaz Asla")
        self.assertLessEqual(len(ust2.split()), UST_MAX_KELIME)

    def test_alt_cumle_duzeni_2_7_kelime(self):
        alt = alt_baslik_duzenle("BU SUV NORMAL DEĞİL ASLINDA.")
        self.assertFalse(alt == alt.upper())  # BÜYÜK başlık düzeni kırılır
        self.assertLessEqual(len(alt.split()), ALT_MAX_KELIME)

    def test_tam_5_farkli_alternatif_her_zaman(self):
        # 1) Hiçbir girdi yoksa bile 5 şablon alternatifi döner.
        bos = kapak_basliklarini_normalize_et(None, {}, {}, {})
        self.assertEqual(len(bos), ALTERNATIF_SAYISI)
        # 2) Eski tip tek başlık (kapak_metni string) da 5 alternatife tamamlanır.
        hook = {"kapak_metni": "Fiyat Şoku", "ilk_3_saniye_kanca": "Bu fiyat herkesi şaşırttı"}
        tek = kapak_basliklarini_normalize_et(hook["kapak_metni"], {}, {}, hook)
        self.assertEqual(len(tek), ALTERNATIF_SAYISI)

    def test_5_alternatif_her_biri_kurala_uygun_ve_birbirinden_farkli(self):
        agent = [
            {"ust": "bu suv normal değil", "alt": "AİLE ARABASI GİBİ DURUYOR AMA DEĞİL."},
            {"ust": "ÖTV ŞOKU", "alt": "Türkiye fiyatı Avrupa'nın iki katı çıktı"},
            {"ust": "BİR KELİME", "alt": "kısa"},
            {"ust": "ÖTV ŞOKU", "alt": "tekrar eden üst başlık burada elenmeli"},
            {"ust": "GERÇEK MENZİL", "alt": "Kışın Yüzde Otuz Eriyor"},
        ]
        out = kapak_basliklarini_normalize_et(
            agent, {"core_story": "Fiyat"}, {"viral_kan_mali": "Türkiye fiyatı 2 katı"}, {}
        )
        self.assertEqual(len(out), ALTERNATIF_SAYISI)
        ustler = []
        for item in out:
            self.assertTrue(ust_kurala_uygun_mu(item["ust"]), item)
            self.assertTrue(alt_kurala_uygun_mu(item["alt"]), item)
            self.assertEqual(item["ana"], item["ust"])  # geriye dönük uyum
            ustler.append(item["ust"])
        self.assertEqual(len(set(ustler)), ALTERNATIF_SAYISI)  # üstler benzersiz

    def test_alternatifler_kullanici_sablonu_ile_yazilir(self):
        out = kapak_basliklarini_normalize_et(None, {"core_story": "Fiyat"}, {}, {})
        doc = kapak_basliklarini_metne_dok(out, "🎯 KAPAK BAŞLIĞI ALTERNATİFLERİ")
        self.assertIn("Alternatif 1:", doc)
        self.assertIn("Alternatif 5:", doc)
        self.assertIn("Üst:", doc)
        self.assertIn("Alt:", doc)


if __name__ == "__main__":
    unittest.main()
