"""Video-TTS senkronu ses kuyruğu sözleşmesi (#9):

Yavaşlatma sınırında (TTS >> video) eski davranış ses kuyruğunu SESSİZCE
kesiyordu: kapanış CTA'sı cümlenin ortasında susuyordu. Yeni davranış:
  * Kuyruk makul süreye sığıyorsa video son kare dondurularak uzatılır,
    ses eksiksiz kalır.
  * Çok uzun kuyrukta kırpma kaçınılmazsa en azından açıkça loglanır.
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

from core import media


def _ffmpeg_var() -> bool:
    return bool(media.FFMPEG_BIN) and Path(str(media.FFMPEG_BIN)).exists()


class SenkronKomutuTests(unittest.TestCase):
    def test_komut_t_sureyi_icerir(self):
        komut = media._senkron_komutu("v.mp4", "a.wav", "cikti.mp4", "setpts=PTS/0.5", 12.5, 30.0)
        self.assertIn("-filter:v", komut)
        self.assertIn("-t", komut)
        self.assertIn("12.500000", komut)
        # Son komut argümanı çıktı yolu olmalı.
        self.assertEqual("cikti.mp4", komut[-1])


class SenkronKararTests(unittest.TestCase):
    """video_ve_sesi_birlestir'un kuyruk kararını komut üzerinden doğrular."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.kok = Path(self.tmp.name)
        if not _ffmpeg_var():
            self.skipTest("ffmpeg binary yok")

    def tearDown(self):
        self.tmp.cleanup()

    def _girdi_uret(self, video_sure, ses_sure):
        video = self.kok / "v.mp4"
        ses = self.kok / "a.wav"
        subprocess.run([
            str(media.FFMPEG_BIN), "-y", "-f", "lavfi", "-i",
            f"testsrc=duration={video_sure}:size=128x128:rate=10", str(video),
        ], capture_output=True, timeout=60)
        subprocess.run([
            str(media.FFMPEG_BIN), "-y", "-f", "lavfi", "-i",
            f"sine=frequency=440:duration={ses_sure}", str(ses),
        ], capture_output=True, timeout=60)
        return str(video), str(ses)

    def test_kisa_kuyruk_son_kare_ile_tamamen_korunur(self):
        # 6 sn video + 14 sn ses → clamp 0.5x → eski davranış 12 sn'de keser (2 sn kayıp).
        video, ses = self._girdi_uret(6, 14)
        cikti = str(self.kok / "cikti.mp4")
        logs = []
        self.assertTrue(media.video_ve_sesi_birlestir(video, ses, cikti, logs.append))
        self.assertTrue(any("son kare dondurularak" in line for line in logs))
        cikti_sure = media.video_suresini_al(cikti)
        # Sesin TAMAMI (14 sn) çıktıda olmalı; eski davranış 12 sn'de kesiyordu.
        self.assertGreaterEqual(cikti_sure, 13.5, f"ses kuyruğu kesilmiş: {cikti_sure:.2f}s")
        self.assertLessEqual(cikti_sure, 16.0)

    def test_uzun_kuyrukta_kirpma_acikca_loglanir(self):
        # 6 sn video + 40 sn ses → 17 sn kuyruk → dondurma sınırı dışı → kırpma + uyarı.
        video, ses = self._girdi_uret(6, 40)
        cikti = str(self.kok / "cikti.mp4")
        logs = []
        self.assertTrue(media.video_ve_sesi_birlestir(video, ses, cikti, logs.append))
        self.assertTrue(any("kesilecek" in line for line in logs), "uzun kuyruk kırpması sessiz kalıyor")
        self.assertFalse(any("son kare dondurularak" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
