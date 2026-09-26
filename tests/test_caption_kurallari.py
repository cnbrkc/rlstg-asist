"""Deterministik caption/hashtag kuralları + Metadata prompt tek kaynağı testleri.

Eylül 2026 üretim logu: Telegram'a giden caption'ı üreten Metadata ajanının
promptu 2 satırlıktı — karakter hedefi ve 5-hashtag kuralı hiç yoktu; sonuç
~400 karakterlik caption + 7 hashtag. Kurallar artık (1) metadata promptuna
caption_prompt.txt'ten (tek kaynak) taşınıyor ve (2) kod tarafında sert
tavanlarla uygulanıyor: 900 karakter / 5 hashtag.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

from core.pipeline import (
    CAPTION_AZAM_KARAKTER, HASHTAG_AZAM_SAYI,
    _caption_kurallari_uygula, _caption_state_normalize,
)
from core.prompts import metadata_promptunu_olustur
from core.agentic import _hook_gen_calistir, _metadata_gen_calistir


class CaptionKuralTestleri(unittest.TestCase):

    def test_uzun_aciklama_kelime_sinirinda_kesilir(self):
        # 1000 karakterlik metin 900 altına, kelime sınırında kesilmeli.
        deger = "otomobilde asıl mesele gerçek kullanımın rakamla örtüşüp örtüşmemesidir " * 20
        deger = deger[:1000]
        kesilen, _ = _caption_kurallari_uygula(deger, [])
        self.assertLessEqual(len(kesilen), CAPTION_AZAM_KARAKTER)
        self.assertNotIn("  ", kesilen)
        self.assertFalse(kesilen.endswith((" ", ",", ";", ".")))
        # Kesim kelime ORTASINDA olmamalı: kelime başlıca 900 sınırında olmalı.
        self.assertGreater(len(deger[:CAPTION_AZAM_KARAKTER].rsplit(" ", 1)[0]), CAPTION_AZAM_KARAKTER - 40)

    def test_kisa_aciklama_degistirilmez(self):
        deger, _ = _caption_kurallari_uygula("Kısa ama tam bir cümle.", [])
        self.assertEqual(deger, "Kısa ama tam bir cümle.")

    def test_hashtag_besi_azami_dusurulur_sira_korunur(self):
        ham = ["#otoxtra", "#otomobil", "#es300h", "#lexus", "#fiyat", "#servis", "#otomobilhaber"]
        _, etiketler = _caption_kurallari_uygula("Metin.", ham)
        self.assertEqual(etiketler, ["#otoxtra", "#otomobil", "#es300h", "#lexus", "#fiyat"])
        self.assertEqual(len(etiketler), HASHTAG_AZAM_SAYI)

    def test_hashtag_tekrari_duser_boslugu_digerlerle_doldurur(self):
        ham = ["otoxtra", "#otoxtra", "otomobil", "es300h", "lexus", "fiyat"]
        _, etiketler = _caption_kurallari_uygula("Metin.", ham)
        self.assertEqual(len(etiketler), 5)
        self.assertNotIn("#otoxtra", etiketler[1:], "aynı etiket iki kez gezemez")
        self.assertIn("fiyat", etiketler, "tekrar düşünce yer diğer etikete geçer")

    def test_normalize_metadata_klarini_uygular_ve_loglar(self):
        logs = []
        deger = "karakter " * 200  # 1600 karakter
        out = _caption_state_normalize(
            {"reels_aciklama": deger, "reels_hashtag": ["a", "b", "c", "d", "e", "f", "g"]},
            logs.append,
        )
        self.assertLessEqual(len(out["reels_aciklamasi"]), CAPTION_AZAM_KARAKTER)
        self.assertEqual(len(out["reels_hashtagleri"]), HASHTAG_AZAM_SAYI)
        self.assertTrue(any("✂️ Caption kuralları uygulandı" in l for l in logs))

    def test_normalize_kurallari_disinda_degilse_sessizdir(self):
        logs = []
        out = _caption_state_normalize(
            {"reels_aciklamasi": "Tam uzunlukta bir açıklama.", "reels_hashtagleri": ["a", "b"]},
            logs.append,
        )
        self.assertEqual(out["reels_aciklamasi"], "Tam uzunlukta bir açıklama.")
        self.assertEqual(out["reels_hashtagleri"], ["a", "b"])
        self.assertEqual(logs, [], "kural ihlali yoksa log yazılmaz")


class MetadataPromptTestleri(unittest.TestCase):
    def _router(self):
        prompts = []

        class _R:
            def metin_uret(self, content, prompt, schema, log, arama_kullan=False, **_):
                prompts.append(prompt)
                return {"reels_baslik": "t", "reels_aciklama": "a", "reels_hashtag": ["x"]}, "m"

        return _R(), prompts

    def test_metadata_promptu_caption_kurallarini_tasir(self):
        p = metadata_promptunu_olustur("dengeli")
        # Karakter hedefi ve hashtag kuralı (caption_prompt.txt'ten tek kaynak)
        self.assertIn("700-850", p)
        self.assertIn("TAM 5 adet", p)
        # Metadata şeması: reels_baslik + reels_aciklama + reels_hashtag
        self.assertIn('"reels_baslik"', p)
        self.assertIn('"reels_aciklama"', p)
        self.assertIn('"reels_hashtag": [', p)
        # Ton kilidi metadata için de ekleniyor.
        self.assertIn("RUNTIME İÇERİK TÜRÜ KİLİDİ", p)
        # Caption şemasının JSON kuyruğu metadata promptunda KALMAMALI.
        self.assertNotIn('"reels_aciklamasi": "...', p)

    def test_metadata_ajani_kurali_promptu_kullanir(self):
        router, prompts = self._router()
        _metadata_gen_calistir(router, {}, {}, {}, lambda m: None, ton="dengeli")
        self.assertEqual(len(prompts), 1)
        self.assertIn("700-850", prompts[0])
        self.assertIn("TAM 5 adet", prompts[0])
        self.assertIn("BAŞLIK (reels_baslik)", prompts[0])

    def test_hook_promptu_kalite_cubugunu_icerir(self):
        router, prompts = self._router()
        _hook_gen_calistir(router, {}, {}, {}, lambda m: None)
        self.assertEqual(len(prompts), 1)
        p = prompts[0]
        self.assertIn("SOMUT DAYANAK ZORUNLU", p)
        self.assertIn("GENEL GEÇER KALİPLER YASAK", p)
        self.assertIn("1. SIRA KULLANILACAK KAPAKTIR", p)
        self.assertIn("ARTIK HİÇBİR ŞEY AYNI", p, "yasak kalıplar promptta somut örnek olarak gösterilmeli")


if __name__ == "__main__":
    unittest.main()
