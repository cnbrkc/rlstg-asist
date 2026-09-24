"""Eylül 2026 pipeline hız (503 yoğunluk) regresyon testleri.

Kök nedenler:
- İsteğe bağlı ajanlar (Detective/Critic/anlatım modu) tüm model+key komboları
  503 dönünce bile tüm turları deniyor ve bekliyordu (Detective ~77s, Critic ~236s).
- Kelime sayısı hedef aralığın dışındaki küçük sapmalar tam Script+Critic turunu
  tekrar çalıştırıyordu (1083s'lik 4/9 aşamasının ana kalemi).
- Aşırı yük tam turlar arasındaki 20/40/60s beklemeler istek başına dakikalar ekledi.

Artık: yakın zamanda aşırı yük görmüş router'da atlanabilir ajanlar denenmez,
kelime kontrolü tolerans eşiğinin altındaki sapmalarda yeniden yazım başlatmaz.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

from core.pipeline import (
    VOICE_WORD_TOLERANCE_RATIO,
    agentic_icerik_uretimi,
)


def _mock_duo_ses(router, segments, output_path, log, hiz_carpani=1.0):
    Path(output_path).write_bytes(b"fake-duo-audio")
    return True, "fake-duo-tts"


class _DetectiveRouter:
    def __init__(self):
        self.metin_calls = 0

    def metin_uret(self, content, prompt, schema, log, arama_kullan=False, **_):
        from core.schemas import DETECTIVE_SCHEMA
        self.metin_calls += 1
        if schema is DETECTIVE_SCHEMA:
            raise Exception("503 UNAVAILABLE high demand")
        if schema.get("properties", {}).get("segments"):
            return {"segments": [{"speaker": "female", "tts_tag": "", "text": "Bu araç çok iyi."}],
                    "yorum_tetikleyici_soru": "Siz alır mıydınız?"}, "m"
        if "kapak_basliklari" in schema.get("properties", {}):
            return {
                "secilen_sablon": "Ters_Kose",
                "kapak_basliklari": [
                    {"ust": "ÖTV ŞOKU", "alt": "Türkiye fiyatı Avrupa'nın iki katı çıktı"},
                    {"ust": "GERÇEK RAKAM ŞOK", "alt": "Kağıt üzerindeki rakam gerçeği anlatmıyor"},
                    {"ust": "BUNU KİMSE SÖYLEMEDİ", "alt": "Videoda anlatılan detay hesabı değiştiriyor"},
                    {"ust": "HERKES YANLIŞ BİLİYOR", "alt": "Kullanıcıların asıl şikayet ettiği nokta bu"},
                    {"ust": "ALMADAN ÖNCE İZLE", "alt": "Bu kararı vermeden önce bunu bil"},
                ],
                "kapak_metni": "ÖTV ŞOKU",
                "ilk_3_saniye_kanca": "Bu fiyat herkesi şaşırttı",
            }, "m"
        if "approved" in schema.get("properties", {}):
            return {"score": 8, "approved": True, "feedback": ""}, "m"
        return {"reels_baslik": "T", "reels_aciklama": "açıklama", "reels_hashtag": ["#oto"]}, "m"


class AsiriYukAtlamaTestleri(unittest.TestCase):
    @patch("core.pipeline._ses_suresini_al", return_value=25.0)
    @patch("core.pipeline.duo_ses_uret", side_effect=_mock_duo_ses)
    @patch("core.pipeline._reels_kelime_ayarlarini_hazirla", return_value=(10, 5, 15, 2.5, 5))
    def test_dedektif_asiri_yukta_hic_denenmez(self, mock_kelime, mock_ses_uret, mock_ses_sure):
        router = _DetectiveRouter()
        router.yakin_zamanda_asiri_yuk_var_mi = lambda pencere_saniye=None: True
        logs = []
        reels, model, plan, script, ok, *_ = agentic_icerik_uretimi(
            router, {}, {}, {}, 30, "dengeli", "Autonoe", logs.append, mod_karari={"mode": "DUO"}
        )
        self.assertTrue(ok)
        # Detective hiç denenmedi
        self.assertFalse(any("Detective Ajan (Derin Analiz)" in l for l in logs))
        self.assertTrue(any("atlandı" in l for l in logs))
        self.assertEqual(len(reels["kapak_basliklari"]), 5)

    @patch("core.pipeline._ses_suresini_al", return_value=25.0)
    @patch("core.pipeline.duo_ses_uret", side_effect=_mock_duo_ses)
    @patch("core.pipeline._reels_kelime_ayarlarini_hazirla", return_value=(10, 5, 15, 2.5, 5))
    def test_dedektif_asiri_yuk_yoksa_denenir(self, mock_kelime, mock_ses_uret, mock_ses_sure):
        router = _DetectiveRouter()
        router.yakin_zamanda_asiri_yuk_var_mi = lambda pencere_saniye=None: False
        logs = []
        reels, model, plan, script, ok, *_ = agentic_icerik_uretimi(
            router, {}, {}, {}, 30, "dengeli", "Autonoe", logs.append, mod_karari={"mode": "DUO"}
        )
        self.assertTrue(ok)
        self.assertTrue(any("Detective Ajan" in l and "kullanılamadı" in l for l in logs))

    def test_tolerans_esigi_sabittir(self):
        self.assertAlmostEqual(VOICE_WORD_TOLERANCE_RATIO, 0.20)


if __name__ == "__main__":
    unittest.main()
