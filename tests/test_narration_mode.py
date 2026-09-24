import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.narration_mode import anlatim_modu_karar_ver


class FakeRouter:
    def __init__(self, output):
        self.output = output
        self.calls = 0

    def metin_uret(self, content, prompt, schema, log, arama_kullan=False):
        self.calls += 1
        if isinstance(self.output, Exception):
            raise self.output
        return self.output, "fake-model"


def _log(_msg):
    pass


def test_ai_decision_solo_is_returned():
    router = FakeRouter({
        "anlatim_modu": "SOLO_MALE", "duo_katma_degeri": "yok",
        "solo_katma_degeri": "akıcı", "guven": 0.8, "gerekce": "teknik yoğun",
    })
    karar = anlatim_modu_karar_ver(router, {}, {}, {"core_story": "x"}, 20, "teknik", _log)
    assert karar["mode"] == "SOLO_MALE"
    assert karar["source"] == "ai"


def test_invalid_or_failed_decision_returns_empty_so_pipeline_keeps_old_behavior():
    assert anlatim_modu_karar_ver(FakeRouter({"anlatim_modu": "???"}), {}, {}, {}, 20, "dengeli", _log) == {}
    assert anlatim_modu_karar_ver(FakeRouter(RuntimeError("x")), {}, {}, {}, 20, "dengeli", _log) == {}


def test_env_override_skips_ai_call(monkeypatch):
    monkeypatch.setenv("ANLATIM_MODU_ZORLA", "DUO")
    router = FakeRouter({})
    karar = anlatim_modu_karar_ver(router, {}, {}, {}, 20, "dengeli", _log)
    assert karar["mode"] == "DUO"
    assert router.calls == 0
