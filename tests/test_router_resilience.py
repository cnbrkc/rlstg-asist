"""core/router.py dayanıklılık testleri: artan backoff ve kalıcı (cross-request) ban.

Gerçek ağ çağrısı yapılmaz; genai.Client, generate_content davranışı kontrollü
olan sahte istemcilerle değiştirilir. Amaç, sürekli 503 veren bir modelin artık
HER pipeline adımında yeniden denenmemesini (23 dk'lık koşunun asıl nedeni)
doğrulamaktır.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import router as router_module
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
        if behavior == "empty":
            class _R:
                text = ""
            return _R()
        raise Exception("unexpected boom")


class _FakeClient:
    def __init__(self, behavior):
        self.models = _FakeModels(behavior)


def _patch_sleep(test_case):
    patcher = mock.patch("core.router.time.sleep", lambda *a, **k: None)
    patcher.start()
    test_case.addCleanup(patcher.stop)


class TransientBackoffTests(unittest.TestCase):
    def setUp(self):
        _patch_sleep(self)
        self.router = SmartRouter()

    def test_cooldown_escalates_and_caps(self):
        self.assertEqual(self.router._transient_cooldown_for("m"), 30)  # n=0
        self.router._record_transient_failure("m")  # n=1 -> 30
        self.assertEqual(self.router._transient_cooldown_for("m"), 30)
        self.router._record_transient_failure("m")  # n=2 -> 60
        self.assertEqual(self.router._transient_cooldown_for("m"), 60)
        self.router._record_transient_failure("m")  # n=3 -> 120
        self.assertEqual(self.router._transient_cooldown_for("m"), 120)
        for _ in range(10):
            self.router._record_transient_failure("m")
        self.assertLessEqual(self.router._transient_cooldown_for("m"), 15 * 60)

    def test_success_resets_counter(self):
        self.router._record_transient_failure("m")
        self.router._record_transient_failure("m")
        self.router._record_success("m")
        self.assertNotIn("m", self.router._transient_fails)
        self.assertEqual(self.router._transient_cooldown_for("m"), 30)

    def test_parse_hata_classifies_errors(self):
        cases = {
            "503 Service Unavailable": "unavailable",
            "RESOURCE_EXHAUSTED quota exceeded": "quota",
            "429 Too Many Requests": "quota",
            "404 not_found model not found": "model_key",
            "rate limit: 0 exceeded": "free_tier_yok",
            "400 invalid_argument unsupported": "model_config",
            "deadline exceeded (timed out)": "combo",
        }
        for hata, expected in cases.items():
            self.assertEqual(self.router._parse_hata(hata)[0], expected, msg=hata)

    def test_clear_transient_keeps_permanent_bans(self):
        self.router._ban("GEMINI_API_KEY", "broken-config", 99999, "model")  # *+model
        self.router._ban("GEMINI_API_KEY", "flaky", 30, "combo")             # key+model
        self.router._clear_transient_bans(["flaky"])
        self.assertTrue(self.router._is_banned("GEMINI_API_KEY", "broken-config"))
        self.assertFalse(self.router._is_banned("GEMINI_API_KEY", "flaky"))


class PersistentBanTests(unittest.TestCase):
    """Bir model bir istekte tüm key'lerde 503 verirse, sonraki istekte atlanmalı."""

    def setUp(self):
        _patch_sleep(self)
        self.router = SmartRouter()

    def _run(self, clients, models):
        self.router.clients = clients
        logs = []
        response, info = self.router._make_request(
            models, "contents", None, logs.append, require_text=True
        )
        return response, info, logs

    def test_flaky_model_is_skipped_on_second_request(self):
        behavior = {"m-flash": "503", "m-flash2": "ok"}

        # 1. istek: m-flash tek key'de 503 verir -> banlanır; m-flash2 başarılı.
        client1 = _FakeClient(behavior)
        _, info1, logs1 = self._run({"GEMINI_API_KEY": client1}, ["m-flash", "m-flash2"])
        self.assertTrue(info1.endswith("m-flash2"))
        self.assertIn("m-flash", client1.models.calls)  # ilk istekte denendi

        # 2. istek: m-flash hâlâ yasaklı olduğu için generate_content ÇAĞRILMAMALI.
        client2 = _FakeClient(behavior)
        _, info2, logs2 = self._run({"GEMINI_API_KEY": client2}, ["m-flash", "m-flash2"])
        self.assertTrue(info2.endswith("m-flash2"))
        self.assertEqual(client2.models.calls, ["m-flash2"])  # m-flash atlandı
        # 2. istekte 503 retry fırtınası olmamalı.
        self.assertFalse(any("geçici 503" in line for line in logs2))

    def test_permanent_model_ban_not_cleared_across_requests(self):
        # Bozuk config (model_config) kalıcı (model düzeyi) yasağı tetikler ve
        # bu tüm çalışma boyunca korunmalı (her TTS çağrısında yeniden denenmemeli).
        behavior = {"bad-tts": "400", "good-tts": "ok"}
        client1 = _FakeClient(behavior)
        _, info1, _ = self._run({"GEMINI_API_KEY": client1}, ["bad-tts", "good-tts"])
        self.assertTrue(info1.endswith("good-tts"))

        client2 = _FakeClient(behavior)
        _, info2, _ = self._run({"GEMINI_API_KEY": client2}, ["bad-tts", "good-tts"])
        self.assertEqual(client2.models.calls, ["good-tts"])  # bad-tts hâlâ yasaklı

    def test_anti_lockup_recovers_when_all_transiently_banned(self):
        # Her iki model de geçici olarak yasaklı; anti-lockoff geçici yasakları
        # temizleyip en azından bir deneme yapmalı.
        self.router._ban("GEMINI_API_KEY", "m-flash", 30, "combo")
        self.router._ban("GEMINI_API_KEY", "m-flash2", 30, "combo")
        behavior = {"m-flash": "503", "m-flash2": "ok"}
        client = _FakeClient(behavior)
        self.router.clients = {"GEMINI_API_KEY": client}
        logs = []
        _, info = self.router._make_request(
            ["m-flash", "m-flash2"], "contents", None, logs.append, require_text=True
        )
        self.assertTrue(info.endswith("m-flash2"))
        self.assertTrue(any("geçici engeller temizlenip" in line for line in logs))


if __name__ == "__main__":
    unittest.main()
