"""Research/Fact Lock dayanıklılığı (#18): model erişilebilir ama BOŞ state
döndürdüyse sessizce kabul edilmez; gözlemlenen gerçeklerle güvenli fallback
kullanılır (QA katmanındaki boş-state korumasının üretim başındaki eşleniği)."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

from core.pipeline import _research_calistir


class _BosRouter:
    def metin_uret(self, *a, **k):
        return {}, "fake-bos"


class ResearchBosStateTests(unittest.TestCase):
    def _calistir(self, router, video_state=None):
        logs = []
        with patch("core.pipeline.web_arastirma_yap", return_value=""), \
             patch("core.pipeline.otv_kilidi_hesapla", return_value={"durum": "YOK"}), \
             patch("core.pipeline.vergi_kilidini_uygula", side_effect=lambda s, k, *a, **b: s):
            state, model = _research_calistir(router, video_state or {}, logs.append)
        return state, model, logs

    def test_bos_state_fallback_ile_degistirilir(self):
        state, model, logs = self._calistir(_BosRouter(), {
            "video_identity": {"brand": "Kia", "exact_model": "EV3"},
            "observed_facts": ["Kaputta Kia logosu görülüyor."],
        })
        self.assertEqual("forensic-fallback", model)
        # Gözlemlenen gerçekler boş state ile çöpe gitmez.
        bulgular = " ".join(f.get("fact", "") for f in state.get("facts", []))
        self.assertIn("Kia EV3", bulgular)
        self.assertIn("Kaputta Kia logosu", bulgular)
        self.assertTrue(any("boş state" in line for line in logs))

    def test_dolu_state_dokunulmaz(self):
        dolu = {
            "facts": [{"fact": "Fiyat 1.500.000 TL.", "status": "VERIFIED", "source": "x", "confidence": "high"}],
            "turkiye_satis_durumu": "SATISTA",
        }

        class _DoluRouter:
            def metin_uret(self, *a, **k):
                return dolu, "fake-dolu"

        state, model, _ = self._calistir(_DoluRouter())
        self.assertEqual("fake-dolu", model)
        self.assertEqual(dolu["facts"], state["facts"])


if __name__ == "__main__":
    unittest.main()
