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

def test_fiil_baglantili_erkek_sesi_komutu_solo_male_doner():
    from core.pipeline import _explicit_voice_mode_from_notes

    assert _explicit_voice_mode_from_notes("erkek sesiyle anlat") == "SOLO_MALE"
    assert _explicit_voice_mode_from_notes("bu videoyu erkek sesi okusun") == "SOLO_MALE"


def test_fiil_baglantili_kadin_sesi_komutu_solo_female_doner():
    from core.pipeline import _explicit_voice_mode_from_notes

    assert _explicit_voice_mode_from_notes("kadın sesiyle anlat") == "SOLO_FEMALE"
    assert _explicit_voice_mode_from_notes("kadın sesi anlatsın") == "SOLO_FEMALE"


def test_fiilsiz_cinsiyet_ifadesi_modu_degistirmez():
    from core.pipeline import _explicit_voice_mode_from_notes

    # "videoda kadın sesi var" gözlem bildirimi; komut değil → karar verilmez.
    assert _explicit_voice_mode_from_notes("videoda kadın sesi var") == ""
    assert _explicit_voice_mode_from_notes("erkek sesi güzel duruyor") == ""


def test_duo_negasyonuyla_birlikte_fiil_komutu_galip_cikar():
    from core.pipeline import _explicit_voice_mode_from_notes

    # "duo istemiyorum" tek ses ister; fiil komutu cinsiyeti de söyler.
    assert _explicit_voice_mode_from_notes("duo istemiyorum, erkek sesiyle anlat") == "SOLO_MALE"

