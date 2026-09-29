"""Workflow sağlığı: aynı sohbette ikinci istek devam eden render'ı KESMEMELİ
(#23) ve pipeline düşerse kullanıcı Telegram'dan haberdar edilmeli."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

import pytest

yaml = pytest.importorskip("yaml")

KOK = Path(__file__).resolve().parents[1]


class WorkflowSaglikTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dosya = KOK / ".github/workflows/telegram-video-optimized.yml"
        cls.wf = yaml.safe_load(cls.dosya.read_text(encoding="utf-8"))

    def test_ikinci_istek_renderi_kesmez_kuyruga_girer(self):
        concurrency = self.wf.get("concurrency") or {}
        self.assertEqual("telegram-video-${{ inputs.telegram_chat_id }}", concurrency.get("group"))
        self.assertFalse(
            concurrency.get("cancel-in-progress", False),
            "cancel-in-progress: true, uzun render'ı ikinci mesajla öldürüyordu",
        )

    def test_pipeline_duserse_kullanici_bildirilir(self):
        adimlar = (self.wf.get("jobs") or {}).get("telegram-video", {}).get("steps") or []
        bildirim = [a for a in adimlar if "Notify user on failure" in str(a.get("name", ""))]
        self.assertEqual(1, len(bildirim), "başarısızlık bildirim adımı yok")
        kosul = str(bildirim[0].get("if", ""))
        self.assertIn("always()", kosul)
        self.assertIn("failure()", kosul)
        self.assertTrue(bildirim[0].get("env", {}).get("TELEGRAM_BOT_TOKEN"))
        # Bildirim pipeline adımından SONRA gelmeli (job sonucunu görür).
        pipeline_idx = next(i for i, a in enumerate(adimlar) if "Run Reels pipeline" in str(a.get("name", "")))
        self.assertGreater(adimlar.index(bildirim[0]), pipeline_idx)

    def test_tani_adimlari_dususte_de_calisir(self):
        adimlar = (self.wf.get("jobs") or {}).get("telegram-video", {}).get("steps") or []
        for isim in ("Publish diagnostic summary", "Upload pipeline diagnostics"):
            adim = next((a for a in adimlar if isim in str(a.get("name", ""))), None)
            self.assertIsNotNone(adim, f"{isim} adımı yok")
            self.assertIn("always()", str(adim.get("if", "")))


if __name__ == "__main__":
    unittest.main()
