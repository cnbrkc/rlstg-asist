"""core/router.py yönlendirme testleri (kullanıcı tasarımı: hızlı tam tur).

Gerçek ağ çağrısı yapılmaz; genai.Client davranışı kontrollü sahte istemcilerle
değiştirilir. Doğrulanan davranış:
  * Her model tüm API key'lerinde 1'er kez (beklemeden) denenir.
  * Bir tam tur (tüm key'ler) başarısız olmadan diğer modele geçilmez.
  * Geçici (503/kota) hataların adımlar arası hafızası yoktur: her adım turları
    fresh yapar (3. key hata verse 4. çalışabilir).
  * Kalıcı hatalar (404 / bozuk config) modeli tüm adımlarda atlar.
  * free-tier key/project'e bağlıdır: yalnızca o key yasaklanır, diğerleri denenir.
"""
import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.router import SmartRouter


class _FakeModels:
    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        behavior = self.behavior.get(model, "boom")
        if behavior == "ok":
            class _R:
                text = '{"ok": true}'
            return _R()
        if behavior == "503":
            raise Exception("503 Service Unavailable")
        if behavior == "quota":
            raise Exception("429 RESOURCE_EXHAUSTED quota exceeded")
        if behavior == "404":
            raise Exception("404 not_found model not found")
        if behavior == "model_config":
            raise Exception("400 invalid_argument unsupported")
        if behavior == "free_tier":
            raise Exception('limit: 0 for this model')
        if behavior == "empty":
            class _R:
                text = ""
            return _R()
        raise Exception("boom")


class _FakeClient:
    def __init__(self, behavior):
        self.models = _FakeModels(behavior)


def _router_with_keys(key_mails):
    """SmartRouter kurar; _ordered_api_items verilen key listesini döner."""
    router = SmartRouter()
    router._ordered_api_items = lambda: [(m, "x") for m in key_mails]
    return router


class ParseHataTests(unittest.TestCase):
    def setUp(self):
        self.router = SmartRouter()

    def test_classifies_errors(self):
        cases = {
            "503 Service Unavailable": "unavailable",
            "429 RESOURCE_EXHAUSTED quota": "quota",
            "404 not_found model not found": "model_key",
            "rate limit: 0 exceeded": "free_tier_yok",
            "400 invalid_argument unsupported": "model_config",
            "deadline exceeded (timed out)": "combo",
        }
        for hata, expected in cases.items():
            self.assertEqual(self.router._parse_hata(hata)[0], expected, msg=hata)


class FullTourTests(unittest.TestCase):
    """Her key 1'er kez denenir; tüm key'ler tükenmeden diğer modele geçilmez."""

    def test_each_key_tried_once_then_next_model(self):
        router = _router_with_keys(["k0", "k1", "k2"])
        behavior = {"m-flash": "503", "m-flash2": "ok"}
        router.clients = {m: _FakeClient(behavior) for m in ("k0", "k1", "k2")}
        logs = []
        _, info = router._make_request(["m-flash", "m-flash2"], "x", None, logs.append, require_text=True)
        self.assertTrue(info.endswith("m-flash2"))
        # m-flash her key'de TAM 1 kez denendi (retry/sleep yok).
        for m in ("k0", "k1", "k2"):
            self.assertEqual(router.clients[m].models.calls.count("m-flash"), 1)
        # m-flash2 ilk key'de başarılı oldu, diğer key'lere gerek kalmadı.
        self.assertEqual(router.clients["k0"].models.calls.count("m-flash2"), 1)

    def test_quota_also_tries_all_keys(self):
        router = _router_with_keys(["k0", "k1", "k2"])
        behavior = {"m-search": "quota", "m-fallback": "ok"}
        router.clients = {m: _FakeClient(behavior) for m in ("k0", "k1", "k2")}
        _, info = router._make_request(["m-search", "m-fallback"], "x", None, lambda *a: None, require_text=True)
        self.assertTrue(info.endswith("m-fallback"))
        for m in ("k0", "k1", "k2"):
            self.assertEqual(router.clients[m].models.calls.count("m-search"), 1)

    def test_empty_response_moves_to_next_key(self):
        router = _router_with_keys(["k0", "k1"])
        # k0 boş yanıt verir, k1 dolu.
        router.clients = {"k0": _FakeClient({"m": "empty"}), "k1": _FakeClient({"m": "ok"})}
        _, info = router._make_request(["m"], "x", None, lambda *a: None, require_text=True)
        self.assertIn("k1", info)


class FreshTourEveryRequestTests(unittest.TestCase):
    """Geçici (503) hatanın adımlar arası hafızası yoktur."""

    def test_flaky_model_retried_on_every_request(self):
        router = _router_with_keys(["k0", "k1"])
        behavior = {"m-flash": "503", "m-flash2": "ok"}

        router.clients = {m: _FakeClient(behavior) for m in ("k0", "k1")}
        _, info1 = router._make_request(["m-flash", "m-flash2"], "x", None, lambda *a: None, require_text=True)
        self.assertTrue(info1.endswith("m-flash2"))
        # 1. turda m-flash her iki key'de denendi.
        self.assertEqual(router.clients["k0"].models.calls.count("m-flash"), 1)
        self.assertEqual(router.clients["k1"].models.calls.count("m-flash"), 1)

        # 2. tur (yeni pipeline adımı): m-flash YINE fresh denenir (kaçınılmaz).
        router.clients = {m: _FakeClient(behavior) for m in ("k0", "k1")}
        _, info2 = router._make_request(["m-flash", "m-flash2"], "x", None, lambda *a: None, require_text=True)
        self.assertTrue(info2.endswith("m-flash2"))
        self.assertEqual(router.clients["k0"].models.calls.count("m-flash"), 1)
        self.assertEqual(router.clients["k1"].models.calls.count("m-flash"), 1)

    def test_third_key_fails_fourth_succeeds_scenario(self):
        """Kullanıcının örneği: 3 key hata verir, 4. key çalışır."""
        router = _router_with_keys(["k0", "k1", "k2", "k3"])
        router.clients = {
            "k0": _FakeClient({"m": "503"}),
            "k1": _FakeClient({"m": "503"}),
            "k2": _FakeClient({"m": "503"}),
            "k3": _FakeClient({"m": "ok"}),   # 4. key çalışır
        }
        _, info = router._make_request(["m"], "x", None, lambda *a: None, require_text=True)
        self.assertIn("k3", info)
        # Hiçbir key atlanmadı; hepsi 1'er kez denendi.
        for m in ("k0", "k1", "k2", "k3"):
            self.assertEqual(router.clients[m].models.calls.count("m"), 1)


class PermanentBanTests(unittest.TestCase):
    """Kalıcı hatalar (404 / bozuk config) modeli tüm adımlarda atlar."""

    def test_404_model_skipped_on_next_request(self):
        router = _router_with_keys(["k0", "k1"])
        behavior = {"dead": "404", "good": "ok"}

        router.clients = {m: _FakeClient(behavior) for m in ("k0", "k1")}
        _, info1 = router._make_request(["dead", "good"], "x", None, lambda *a: None, require_text=True)
        self.assertTrue(info1.endswith("good"))

        # 2. tur: dead artık denenmez (kalıcı model yasağı).
        router.clients = {m: _FakeClient(behavior) for m in ("k0", "k1")}
        logs = []
        _, info2 = router._make_request(["dead", "good"], "x", None, logs.append, require_text=True)
        self.assertTrue(info2.endswith("good"))
        self.assertEqual(router.clients["k0"].models.calls.count("dead"), 0)
        self.assertEqual(router.clients["k1"].models.calls.count("dead"), 0)
        # "Model deneniyor: dead" logu bile çıkmaz (en başta atlanır).
        self.assertFalse(any("Model deneniyor: dead" in l for l in logs))

    def test_model_config_skipped_on_next_request(self):
        router = _router_with_keys(["k0", "k1"])
        behavior = {"bad-tts": "model_config", "good-tts": "ok"}

        router.clients = {m: _FakeClient(behavior) for m in ("k0", "k1")}
        _, info1 = router._make_request(["bad-tts", "good-tts"], "x", None, lambda *a: None, require_text=True)
        self.assertTrue(info1.endswith("good-tts"))

        router.clients = {m: _FakeClient(behavior) for m in ("k0", "k1")}
        _, info2 = router._make_request(["bad-tts", "good-tts"], "x", None, lambda *a: None, require_text=True)
        self.assertTrue(info2.endswith("good-tts"))
        self.assertEqual(router.clients["k0"].models.calls.count("bad-tts"), 0)
        self.assertEqual(router.clients["k1"].models.calls.count("bad-tts"), 0)


class MultiSpeakerConfigTests(unittest.TestCase):
    """DUO isteğinin API'ye boş SpeechConfig olarak gitmesini engeller."""

    def test_duo_config_is_nested_under_speech_config(self):
        router = SmartRouter.__new__(SmartRouter)
        captured = {}

        class _Inline:
            data = b"pcm"

        class _Part:
            inline_data = _Inline()

        class _Content:
            parts = [_Part()]

        class _Candidate:
            content = _Content()

        class _Response:
            candidates = [_Candidate()]

        def fake_request(models, contents, config, log):
            captured["prompt"] = contents
            captured["config"] = config
            return _Response(), "key+tts"

        router._make_request = fake_request
        router._tts_kaydet = lambda audio, path, speed, log: audio == b"pcm"
        logs = []

        ok, _ = router.coklu_ses_uret(
            "Autonoe: Merhaba.\nCharon: Selam.",
            [("Autonoe", "Autonoe"), ("Charon", "Charon")],
            "/tmp/not-written.wav",
            logs.append,
        )

        self.assertTrue(ok)
        speech = captured["config"].speech_config
        self.assertIsNotNone(speech)
        multi = speech.multi_speaker_voice_config
        self.assertIsNotNone(multi)
        self.assertEqual(
            [(item.speaker, item.voice_config.prebuilt_voice_config.voice_name)
             for item in multi.speaker_voice_configs],
            [("Autonoe", "Autonoe"), ("Charon", "Charon")],
        )
        # SDK'nin API'ye göndereceği wire shape boş `speechConfig: {}` değil,
        # mutlaka nested `multiSpeakerVoiceConfig` içermeli.
        wire = captured["config"].model_dump(exclude_none=True, by_alias=True)
        self.assertEqual(
            len(wire["speechConfig"]["multiSpeakerVoiceConfig"]["speakerVoiceConfigs"]),
            2,
        )
        self.assertTrue(any("Multi-speaker API config doğrulandı" in line for line in logs))
        self.assertIn("Do not sing, chant, harmonize", captured["prompt"])
        self.assertIn("Never insert the same pause between every speaker", captured["prompt"])
        self.assertIn("Fast, alert cold open", captured["prompt"])

    def test_rejects_duplicate_voice(self):
        router = SmartRouter.__new__(SmartRouter)
        logs = []
        ok, info = router.coklu_ses_uret(
            "A: Bir.\nB: İki.",
            [("A", "Autonoe"), ("B", "Autonoe")],
            "/tmp/not-written.wav",
            logs.append,
        )
        self.assertFalse(ok)
        self.assertIsNone(info)
        self.assertTrue(any("sesler farklı olmalı" in line for line in logs))


class FreeTierPerKeyTests(unittest.TestCase):
    """free-tier key/project'e bağlı: yalnızca o key yasaklanır."""

    def test_other_key_still_tried_after_free_tier(self):
        router = _router_with_keys(["k0", "k1"])
        # k0 free-tier, k1 çalışır.
        router.clients = {"k0": _FakeClient({"m": "free_tier"}), "k1": _FakeClient({"m": "ok"})}
        _, info1 = router._make_request(["m"], "x", None, lambda *a: None, require_text=True)
        self.assertIn("k1", info1)  # k1 başarılı
        self.assertEqual(router.clients["k0"].models.calls.count("m"), 1)

        # 2. tur: k0 free-tier yasaklı (kalıcı, key düzeyi), k1 denenir.
        router.clients = {"k0": _FakeClient({"m": "free_tier"}), "k1": _FakeClient({"m": "ok"})}
        _, info2 = router._make_request(["m"], "x", None, lambda *a: None, require_text=True)
        self.assertIn("k1", info2)
        self.assertEqual(router.clients["k0"].models.calls.count("m"), 0)  # k0 artık atlanır
        self.assertEqual(router.clients["k1"].models.calls.count("m"), 1)


if __name__ == "__main__":
    unittest.main()
