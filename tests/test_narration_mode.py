import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.narration_mode import (
    anlatim_modu_karar_ver,
    mod_kilit_talimati,
    mod_kilidini_uygula,
)


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


def test_solo_lock_collapses_map_to_single_speaker_without_mutating_input():
    reels = {
        "anlatim_modu": "DUO",
        "duo_stratejisi": {"uygunluk": "DUO", "hook_speaker": "male"},
        "konusma_haritasi": [
            {"speaker": "female", "detay": "a"},
            {"speaker": "male", "detay": "b"},
        ],
    }
    out = mod_kilidini_uygula(reels, {"mode": "SOLO_FEMALE"})
    assert out["anlatim_modu"] == "SOLO_FEMALE"
    assert out["duo_stratejisi"]["uygunluk"] == "SOLO_FEMALE"
    assert {x["speaker"] for x in out["konusma_haritasi"]} == {"female"}
    assert [x["detay"] for x in out["konusma_haritasi"]] == ["a", "b"]
    assert reels["anlatim_modu"] == "DUO"


def test_no_decision_means_no_lock():
    reels = {"anlatim_modu": "DUO"}
    assert mod_kilidini_uygula(reels, {}) == reels
    assert mod_kilit_talimati({}) == ""
    assert "SOLO_MALE" in mod_kilit_talimati({"mode": "SOLO_MALE"})


def test_lock_replaces_stale_model_rationale_with_decision_reason():
    reels = {"anlatim_modu": "DUO", "duo_stratejisi": {"rationale": "iki ses daha canlı"}}
    out = mod_kilidini_uygula(reels, {"mode": "SOLO_MALE", "reason": "teknik yoğun içerik"})
    assert out["duo_stratejisi"]["rationale"] == "[SOLO_MALE] teknik yoğun içerik"
