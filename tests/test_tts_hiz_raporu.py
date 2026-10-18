"""TTS hız çarpanının TEK doğruluk kaynağı olduğunu kilitler.

Kök neden (18.10.2026 kullanıcı şikâyeti): "Hızı 1.35 yaptık, ses hızlandı ama
Telegram'a gelen logda hâlâ 1.2 yazıyor."

`telegram/telegram_pipeline_worker._final_report` içindeki rapor satırı
"⚡ TTS hız: 1.20x" diye SABİT yazılmıştı; `core.config.SES_HIZ_CARPANI`
değiştirildiğinde rapor güncellenmiyordu. Ses gerçekten yeni çarpanla
hızlanırken kullanıcıya eski değer raporlanıyordu.

Bu testler üç şeyi doğrular:
  1. Yapılandırılan çarpan kullanıcının istediği değer (1.3).
  2. Telegram raporu çarpanı config'ten OKUR (sabit metin yok).
  3. Aynı çarpan FFmpeg atempo filtresine ve TTS çağrısına gider.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "bot123456789:test-only-token-value")
os.environ.setdefault("TELEGRAM_CHAT_ID", "123456789")

from core import media
from core.config import SES_HIZ_CARPANI
import telegram.telegram_pipeline_worker as worker

BEKLENEN_CARPAN = 1.3


class CarpanYapilandirmaTests(unittest.TestCase):
    def test_yapilandirilan_carpan_kullanicinin_istedigi_deger(self):
        # Kullanıcı kararı (18.10.2026): 1.35 → 1.3. Bilinçli bir değişiklik
        # yapılırsa bu test ve PR açıklaması birlikte güncellenmeli.
        self.assertAlmostEqual(SES_HIZ_CARPANI, BEKLENEN_CARPAN, places=6)

    def test_depo_genelinde_sabit_hiz_metni_kalmadi(self):
        """Kaynak/şema dosyalarında sabit hız metni kalmamalı.

        Rapor çarpanı config'ten okunur; dosyalara '1.20x' gibi sabit değer
        yazmak aynı hatayı geri getirir (ses hızlanır, rapor eski değeri söyler).
        """
        kok = Path(__file__).resolve().parents[1]
        kalintilar = []
        for klasor in ("core", "telegram", "schema", "cloudflare", ".github"):
            for dosya in sorted((kok / klasor).rglob("*")):
                if not dosya.is_file() or dosya.suffix not in {".py", ".md", ".js", ".txt", ".yml"}:
                    continue
                metin = dosya.read_text(encoding="utf-8")
                for sabit in ("1.20x", "1.20×", "1.2x"):
                    if sabit in metin:
                        kalintilar.append(f"{dosya.relative_to(kok)}: {sabit}")
        self.assertEqual(kalintilar, [], "Sabit hız metni kalıntıları: " + ", ".join(kalintilar))


class RaporSatiriTests(unittest.TestCase):
    def test_etiket_config_degerini_gosterir(self):
        self.assertEqual(worker._tts_hiz_etiketi(), f"⚡ TTS hız: {SES_HIZ_CARPANI:.2f}x")
        self.assertEqual(worker._tts_hiz_etiketi(), "⚡ TTS hız: 1.30x")

    def test_etiket_carpan_degisince_onu_takip_eder(self):
        # Sabit metin geri gelirse bu test kırılır: etiket parametreye bağlı.
        self.assertEqual(worker._tts_hiz_etiketi(1.5), "⚡ TTS hız: 1.50x")

    def test_final_raporu_eski_sabit_degeri_icermez(self):
        rapor = worker._final_report({}, [], [], {"ses_modu": "DUO"}, "dengeli")
        self.assertIn(f"⚡ TTS hız: {SES_HIZ_CARPANI:.2f}x", rapor)
        self.assertNotIn("1.20x", rapor)

    def test_final_raporu_kullanici_notunu_gosterir(self):
        not_metni = "Bu araç Mission S, başka model yazma."
        rapor = worker._final_report({}, [], [], {}, "dengeli", kullanici_notu=not_metni)
        self.assertIn(f"📝 Kullanıcı notu: {not_metni}", rapor)
        bos = worker._final_report({}, [], [], {}, "dengeli")
        self.assertIn("📝 Kullanıcı notu: (gönderilmedi)", bos)


class AtempoFiltreTests(unittest.TestCase):
    """Config çarpanının gerçekten ffmpeg atempo filtresine gittiğini doğrular."""

    def test_sesi_hizlandir_config_carpaniyla_atempo_kurar(self):
        giris = Path(os.environ.get("TMPDIR", "/tmp")) / "atempo_giris.wav"
        cikti = Path(os.environ.get("TMPDIR", "/tmp")) / "atempo_cikti.wav"
        giris.write_bytes(b"RIFF" + b"0" * 100)
        komutlar = []

        class _SahteSonuc:
            returncode = 0
            stderr = ""

        with patch.object(media.subprocess, "run", side_effect=lambda komut, **_: (komutlar.append(list(komut)), _SahteSonuc())[1]), \
             patch.object(media.os.path, "exists", return_value=True), \
             patch.object(media.os.path, "getsize", return_value=4096), \
             patch.object(media, "temp_dosya_temizle", lambda *_: None):
            basarili = media.sesi_hizlandir(str(giris), str(cikti), SES_HIZ_CARPANI, lambda m: None)

        giris.unlink(missing_ok=True)
        cikti.unlink(missing_ok=True)
        self.assertTrue(basarili, "atempo adımı başarısız döndü")
        filtreler = [parca for komut in komutlar for parca in komut if str(parca).startswith("atempo=")]
        self.assertTrue(filtreler, "atempo filtresi kurulmadı")
        self.assertEqual(filtreler[0], f"atempo={SES_HIZ_CARPANI}")

    def test_carpan_bire_yakinsa_kopyalanir(self):
        giris = Path(os.environ.get("TMPDIR", "/tmp")) / "atempo_kopya.wav"
        cikti = Path(os.environ.get("TMPDIR", "/tmp")) / "atempo_kopya_cikti.wav"
        giris.write_bytes(b"RIFF" + b"0" * 100)
        try:
            with patch.object(media.shutil, "copy2", return_value=None) as kopya:
                media.sesi_hizlandir(str(giris), str(cikti), 1.0, lambda m: None)
                kopya.assert_called_once()
        finally:
            giris.unlink(missing_ok=True)
            cikti.unlink(missing_ok=True)


class TtsCagrisiCarpanTests(unittest.TestCase):
    """Agentic TTS katmanının çarpanı config'ten aldığını doğrular."""

    def test_solo_tts_cagrisi_config_carpanini_kullanir(self):
        from core.agentic import _duo_ses_veya_legacy_uret

        gorulen = {}

        class _Router:
            def ses_uret(self, text, voice, output, log, hiz_carpani=1.0, **kwargs):
                gorulen["hiz_carpani"] = hiz_carpani
                Path(output).write_bytes(b"fake-solo-audio")
                return True, "fake-tts"

        cikti = Path(os.environ.get("TMPDIR", "/tmp")) / "solo_carpan.wav"
        duo_script = {
            "status": "ready",
            "segments": [{"speaker": "female", "text": "deneme metni"}],
            "contract": {"mode": "SOLO_FEMALE"},
        }
        try:
            ok, _info, mod = _duo_ses_veya_legacy_uret(
                _Router(), duo_script, "deneme metni", "Autonoe", lambda m: None, str(cikti),
            )
        finally:
            cikti.unlink(missing_ok=True)

        self.assertTrue(ok)
        self.assertEqual(mod, "SOLO_FEMALE")
        self.assertEqual(gorulen["hiz_carpani"], SES_HIZ_CARPANI)


if __name__ == "__main__":
    unittest.main()
