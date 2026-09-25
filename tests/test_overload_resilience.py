"""Üretim durması (Eylül 2026) regresyon testleri.

Kök neden: Gemini 503 yoğunluğunda Script Writer isteğinin tüm model+key
kombinasyonları ~30 saniyede düşüyor ve exception tüm pipeline'ı öldürüyordu.
Ayrıca Metadata ajanının çıktı anahtarları caption'a hiç eşlenmiyordu.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("GEMINI_API_KEY", "test-only")

import core.router as router_mod
from core.router import SmartRouter
from core.pipeline import _caption_state_normalize
from core.agentic import agentic_icerik_uretimi


class _SeqModels:
    """Her çağrıda sıradaki davranışı uygular (tur bazlı senaryolar için)."""

    def __init__(self, behaviors):
        self.behaviors = list(behaviors)
        self.calls = 0

    def generate_content(self, model, contents, config):
        behavior = self.behaviors[min(self.calls, len(self.behaviors) - 1)]
        self.calls += 1
        if behavior == "ok":
            class _R:
                text = '{"ok": true}'
            return _R()
        if behavior == "503":
            raise Exception("503 UNAVAILABLE high demand")
        if behavior == "404":
            raise Exception("404 not_found model not found")
        raise Exception("boom")


class _Client:
    def __init__(self, behaviors):
        self.models = _SeqModels(behaviors)


def _router(behaviors):
    router = SmartRouter()
    router._ordered_api_items = lambda: [("k0", "x")]
    router.clients = {"k0": _Client(behaviors)}
    return router


class OverloadRetryTests(unittest.TestCase):
    def test_full_round_503_waits_then_succeeds(self):
        router = _router(["503", "ok"])
        sleeps = []
        logs = []
        with patch.object(router_mod, "_sleep", sleeps.append):
            _, info = router._make_request(["m"], "x", None, logs.append, require_text=True)
        self.assertEqual(info, "k0+m")
        self.assertEqual(len(sleeps), 1)
        self.assertTrue(any("tam tur yeniden deneniyor" in line for line in logs))

    def test_permanent_errors_do_not_wait(self):
        router = _router(["404"])
        sleeps = []
        with patch.object(router_mod, "_sleep", sleeps.append):
            with self.assertRaises(Exception):
                router._make_request(["m-dead"], "x", None, lambda *a: None, require_text=True)
        self.assertEqual(sleeps, [])

    def test_retry_rounds_are_bounded(self):
        router = _router(["503"])
        sleeps = []
        with patch.object(router_mod, "_sleep", sleeps.append):
            with self.assertRaises(Exception):
                router._make_request(["m"], "x", None, lambda *a: None, require_text=True)
        self.assertEqual(len(sleeps), router_mod.OVERLOAD_RETRY_ROUNDS)

    def test_fast_fail_context_skips_overload_wait(self):
        router = _router(["503"])
        sleeps = []
        with patch.object(router_mod, "_sleep", sleeps.append):
            with router.hizli_basarisizlik():
                with self.assertRaises(Exception):
                    router._make_request(["m"], "x", None, lambda *a: None, require_text=True)
        self.assertEqual(sleeps, [])
        self.assertEqual(router._overload_retry_off, 0)

    def test_wait_budget_is_shared_across_requests(self):
        router = _router(["503"])
        sleeps = []
        with patch.object(router_mod, "_sleep", sleeps.append), \
                patch.object(router_mod, "OVERLOAD_WAIT_BUDGET_SECONDS", 30):
            for _ in range(2):
                with self.assertRaises(Exception):
                    router._make_request(["m"], "x", None, lambda *a: None, require_text=True)
        self.assertLessEqual(sum(sleeps), 30)


    def test_optional_profile_retries_from_separate_pool(self):
        # Kalite: isteğe bağlı ajan da aşırı yükte bekleyip yeniden dener; ama
        # bekleme zorunlu adımların havuzundan düşülmez.
        router = _router(["503", "ok"])
        sleeps = []
        with patch.object(router_mod, "_sleep", sleeps.append):
            with router.istek_profili("istege_bagli"):
                _, info = router._make_request(["m"], "x", None, lambda *a: None, require_text=True)
        self.assertEqual(info, "k0+m")
        self.assertEqual(len(sleeps), 1)
        self.assertEqual(router._overload_wait_spent, 0.0)
        self.assertGreater(router._optional_overload_wait_spent, 0.0)

    def test_exhausted_optional_pool_does_not_block_mandatory_waits(self):
        router = _router(["503"])
        sleeps = []
        with patch.object(router_mod, "_sleep", sleeps.append), \
                patch.object(router_mod, "OVERLOAD_OPTIONAL_WAIT_BUDGET_SECONDS", 0):
            with router.istek_profili("istege_bagli"):
                with self.assertRaises(Exception):
                    router._make_request(["m"], "x", None, lambda *a: None, require_text=True)
            self.assertEqual(sleeps, [])
            router.clients = {"k0": _Client(["503", "ok"])}
            _, info = router._make_request(["m"], "x", None, lambda *a: None, require_text=True)
        self.assertEqual(info, "k0+m")
        self.assertEqual(len(sleeps), 1)


class CaptionMappingTests(unittest.TestCase):
    def test_metadata_agent_keys_map_to_caption(self):
        out = _caption_state_normalize({
            "reels_baslik": "Fiyat Şoku",
            "reels_aciklama": "Bu fiyat herkesi şaşırttı",
            "reels_hashtag": ["#otomobil"],
        })
        self.assertEqual(out["reels_aciklamasi"], "Bu fiyat herkesi şaşırttı")
        self.assertEqual(out["reels_hashtagleri"], ["#otomobil"])

    def test_legacy_caption_keys_still_work(self):
        out = _caption_state_normalize({"reels_aciklamasi": "a", "reels_hashtagleri": ["b"]})
        self.assertEqual(out, {"reels_aciklamasi": "a", "reels_hashtagleri": ["b"]})


def _mock_duo_ses(router, segments, output_path, log, hiz_carpani=1.0):
    Path(output_path).write_bytes(b"fake-duo-audio")
    return True, "fake-duo-tts"


_SCRIPT = {
    "segments": [
        {"speaker": "female", "tts_tag": "", "text": "Bu araç çok iyi."},
        {"speaker": "male", "tts_tag": "", "text": "Fiyatına bak önce."},
    ],
    "yorum_tetikleyici_soru": "Siz alır mıydınız?",
}


class _FlakyAgentRouter:
    """Script Writer dışındaki ajanlar 503 ile düşer."""

    def __init__(self):
        self.calls = []

    def metin_uret(self, content, prompt, schema, log, arama_kullan=False, **_):
        from core.schemas import SCRIPT_WRITER_SCHEMA
        self.calls.append(schema)
        if schema is SCRIPT_WRITER_SCHEMA:
            return dict(_SCRIPT), "fake-script"
        raise Exception("503 UNAVAILABLE high demand")


class AgenticDegradationTests(unittest.TestCase):
    @patch("core.agentic._ses_suresini_al", return_value=25.0)
    @patch("core.agentic._reels_kelime_ayarlarini_hazirla", return_value=(10, 1, 999, 2.5, 5))
    @patch("core.agentic.duo_ses_uret", side_effect=_mock_duo_ses)
    def test_optional_agents_failing_do_not_stop_production(self, *_):
        router = _FlakyAgentRouter()
        logs = []
        editorial = {"potential_hook_territories": ["Bu fiyat Türkiye'de olmaz"], "core_story": "Fiyat"}
        reels, model, plan, script, ses_ok, _info, ses_modu, ses_dosyasi, meta = agentic_icerik_uretimi(
            router, {}, {}, editorial, 30, "dengeli", "Autonoe", logs.append, mod_karari={"mode": "DUO"}
        )
        self.assertTrue(ses_ok)
        self.assertEqual(ses_modu, "DUO")
        self.assertTrue(os.path.exists(ses_dosyasi))
        self.assertEqual(script["status"], "ready")
        self.assertTrue(reels["kapak_basliklari"][0]["ana"])
        self.assertEqual(meta, {})
        self.assertTrue(any("Detective Ajan kullanılamadı" in line for line in logs))

    @patch("core.agentic._ses_suresini_al", return_value=60.0)
    @patch("core.agentic._reels_kelime_ayarlarini_hazirla", return_value=(10, 1, 999, 2.5, 5))
    @patch("core.agentic.duo_ses_uret", side_effect=_mock_duo_ses)
    def test_valid_wav_kept_even_if_duration_ratio_is_off(self, mock_tts, *_):
        from test_pipeline_modes import _Router, _detective, _hook, _critic, _metadata
        router = _Router([_detective(), _hook(), dict(_SCRIPT), _critic(), _metadata()])
        logs = []
        *_rest, ses_ok, _info, _mod, ses_dosyasi, meta = agentic_icerik_uretimi(
            router, {}, {}, {}, 30, "dengeli", "Autonoe", logs.append, mod_karari={"mode": "DUO"}
        )
        self.assertTrue(ses_ok)
        self.assertEqual(mock_tts.call_count, 1)
        self.assertEqual(meta.get("reels_baslik"), "Fiyat Şoku")


if __name__ == "__main__":
    unittest.main()
