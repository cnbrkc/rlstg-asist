"""Telegram 413 ve büyük video otomatik sıkıştırması için testler.

413'ün kaynağı: 1080x1920 60fps 97s giriş → 105s 1080x1920 60fps çıkış CRF20 >50 MB;
Bot API 50 MB limitini aşıyor. Çözüm: CRF → 720p → 30fps kademeli sıkıştırma.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

from core import media


class DosyaBoyutuTests(unittest.TestCase):
    def test_dosya_boyutu_mb(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"a" * 1024 * 1024)  # 1 MB
            fname = f.name
        try:
            self.assertAlmostEqual(media.dosya_boyutu_mb(fname), 1.0, places=2)
            self.assertGreater(media.dosya_boyutu_mb(fname), 0.9)
        finally:
            Path(fname).unlink(missing_ok=True)

    def test_olmayan_dosya_sifir(self):
        self.assertEqual(media.dosya_boyutu_mb("/tmp/olmayan_12345.mp4"), 0.0)

    def test_limit_kontrolu_gecer(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"0" * 1024 * 200)  # 200 KB < 45 MB
            fname = f.name
        try:
            self.assertFalse(media.video_telegram_limitini_asar_mi(fname, limit_mb=45))
            self.assertTrue(media.video_telegram_limitini_asar_mi(fname, limit_mb=0.1))
        finally:
            Path(fname).unlink(missing_ok=True)

    def test_limit_kontrolu_asar(self):
        # 1 MB dosya, 0.5 MB limit → aşar
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"0" * 1024 * 1024)
            fname = f.name
        try:
            self.assertTrue(media.video_telegram_limitini_asar_mi(fname, limit_mb=0.5))
        finally:
            Path(fname).unlink(missing_ok=True)


class SikistirmaFiltreleriTests(unittest.TestCase):
    def test_hd_video_720p_filtresi(self):
        bilgi = {"width": 1080, "height": 1920, "fps": 60}
        filtreler = media._telegram_sikistirma_filtreleri(max_dimension=1280, fps_limit=30, giris_bilgisi=bilgi)
        self.assertIn("scale=720:1280:force_original_aspect_ratio=decrease", filtreler)
        self.assertIn("setsar=1", filtreler)
        self.assertIn("fps=30", filtreler)

    def test_kucuk_video_filtresiz(self):
        bilgi = {"width": 640, "height": 480, "fps": 24}
        filtreler = media._telegram_sikistirma_filtreleri(max_dimension=1280, fps_limit=30, giris_bilgisi=bilgi)
        self.assertEqual(filtreler, [])

    def test_yalnizca_fps_dusurme(self):
        bilgi = {"width": 640, "height": 480, "fps": 60}
        filtreler = media._telegram_sikistirma_filtreleri(max_dimension=None, fps_limit=30, giris_bilgisi=bilgi)
        self.assertIn("fps=30", filtreler)
        self.assertNotIn("scale=720", " ".join(filtreler))


class TelegramLimitineSigdirTests(unittest.TestCase):
    def test_kucuk_dosya_passthrough(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"0" * 1024)
            fname = f.name
        try:
            # 45 MB limit, 1KB dosya → aynı dosya dönmeli, ffmpeg çağrılmamalı
            with patch("core.media.videoyu_telegram_icin_sikistir") as mock_sikistir:
                result = media.telegram_limitine_sigdir(fname, log_ekle=lambda x: None, limit_mb=45)
                self.assertEqual(result, fname)
                mock_sikistir.assert_not_called()
        finally:
            Path(fname).unlink(missing_ok=True)

    def test_kademeli_sikistirma_basari(self):
        # 50 MB dosya, 45 MB limit → 2 denemede başarı simülasyonu
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(b"0" * 1024)
            fname = f.name
        try:
            # orig_size'ı mocklamak için os.path.getsize patch
            with patch("os.path.getsize", side_effect=[50*1024*1024, 40*1024*1024]):
                with patch("core.media.videoyu_telegram_icin_sikistir", return_value=True):
                    with patch("core.media.medya_raporu", return_value={}):
                        # gecici dosya oluşturması için gecici_dosya_yolu patch
                        tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
                        tmp.close()
                        with patch("core.media.gecici_dosya_yolu", return_value=tmp.name):
                            with patch("os.path.exists", return_value=True):
                                result = media.telegram_limitine_sigdir(fname, log_ekle=lambda x: None, limit_mb=45)
                                # ikinci denemede 40 MB → limit altında → tmp dönmeli
                                self.assertEqual(result, tmp.name)
                        Path(tmp.name).unlink(missing_ok=True)
        finally:
            Path(fname).unlink(missing_ok=True)

    def test_videoyu_sikistir_ffmpeg_cagirisi(self):
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as giris:
            giris.write(b"fake video")
            giris_path = giris.name
        cikti = giris_path + ".out.mp4"
        try:
            with patch("core.media._ffprobe_bilgi_al", return_value={"width": 1080, "height": 1920, "fps": 60}):
                mock_run = MagicMock()
                mock_run.returncode = 0
                with patch("subprocess.run", return_value=mock_run):
                    with patch("os.path.exists", return_value=True):
                        with patch("os.path.getsize", return_value=2048):
                            ok = media.videoyu_telegram_icin_sikistir(giris_path, cikti, log_ekle=lambda x: None, crf=28, max_dimension=1280, fps_limit=30)
                            self.assertTrue(ok)
        finally:
            Path(giris_path).unlink(missing_ok=True)
            Path(cikti).unlink(missing_ok=True)


class ConfigTests(unittest.TestCase):
    def test_telegram_limit_config_var(self):
        from core.config import TELEGRAM_VIDEO_MAX_MB
        self.assertGreaterEqual(TELEGRAM_VIDEO_MAX_MB, 30)
        self.assertLessEqual(TELEGRAM_VIDEO_MAX_MB, 50)

    def test_media_sabitleri(self):
        self.assertEqual(media.TELEGRAM_VIDEO_MAX_BYTES, 45*1024*1024)


class WorkerEntegrasyonTests(unittest.TestCase):
    def test_worker_import(self):
        # worker helper'ları import edilebilir olmalı
        from telegram.telegram_pipeline_worker import _telegram_video_boyutunu_dogrula_ve_sikistir, _send_video_with_fallback, _giris_videosu_uyarisi
        self.assertTrue(callable(_telegram_video_boyutunu_dogrula_ve_sikistir))
        self.assertTrue(callable(_send_video_with_fallback))
        self.assertTrue(callable(_giris_videosu_uyarisi))


if __name__ == "__main__":
    unittest.main()
