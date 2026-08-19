import os
from pathlib import Path

os.environ.setdefault("GEMINI_API_KEY", "test")

from core.pipeline import _duo_plan_hazirla, _explicit_voice_mode_from_notes
from telegram import telegram_pipeline_guard as guard


def _creative_state(mode="SOLO_FEMALE"):
    return {
        "anlatim_modu": mode,
        "seslendirme_metni": "Bu açık bir deneme metnidir.",
        "duo_stratejisi": {
            "uygunluk": mode,
            "hook_speaker": "female",
            "ending_speaker": "female",
        },
        "konusma_haritasi": [
            {"speaker": "female", "amac": "hook", "detay": "Açılış"},
            {"speaker": "female", "amac": "closing", "detay": "Sonuç"},
        ],
    }


def test_model_voice_mode_is_kept_without_explicit_user_request():
    plan = _duo_plan_hazirla(_creative_state(), 30, "dengeli", notes="Aracın fiyatını anlat.")
    assert plan["mode"] == "SOLO_FEMALE"
    assert {turn["speaker"] for turn in plan["conversation_map"]} == {"female"}


def test_explicit_user_duo_overrides_model_solo():
    plan = _duo_plan_hazirla(_creative_state(), 30, "dengeli", notes="Bunu iki sesli duo yap.")
    assert plan["mode"] == "DUO"
    assert {turn["speaker"] for turn in plan["conversation_map"]} == {"female", "male"}


def test_explicit_user_solo_overrides_model_duo():
    plan = _duo_plan_hazirla(_creative_state("DUO"), 30, "dengeli", notes="Yalnızca erkek sesi kullan.")
    assert plan["mode"] == "SOLO_MALE"
    assert {turn["speaker"] for turn in plan["conversation_map"]} == {"male"}


def test_generic_solo_request_lets_ai_character_preference_choose_voice():
    plan = _duo_plan_hazirla(_creative_state("DUO"), 30, "dengeli", notes="Bu içerik tek sesli solo olsun.")
    assert plan["mode"] == "SOLO_FEMALE"


def test_explicit_solo_and_duo_note_detection():
    assert _explicit_voice_mode_from_notes("Yalnızca kadın sesi kullan") == "SOLO_FEMALE"
    assert _explicit_voice_mode_from_notes("Sadece erkek anlatsın") == "SOLO_MALE"
    assert _explicit_voice_mode_from_notes("Solo olmasın, iki sesli duo olsun") == "DUO"
    assert _explicit_voice_mode_from_notes("Duo olmasın, solo yap") == "SOLO"
    assert _explicit_voice_mode_from_notes("Solo mu duo mu videoya göre sen seç") == ""
    assert _explicit_voice_mode_from_notes("Normal üret") == ""


def test_guard_routes_explicit_solo_to_single_voice_tts():
    calls = []

    class FakeRouter:
        def ses_uret(self, text, voice, output, log, hiz_carpani=1.0):
            calls.append((text, voice, hiz_carpani))
            Path(output).write_bytes(b"valid-placeholder")
            return True, "fake-tts"

    original_reels = guard._original_reels_creative
    original_duration = guard._pipeline._ses_sure_uyumlu_mu
    guard._original_reels_creative = lambda *args, **kwargs: (_creative_state(), "fake-text")
    guard._pipeline._ses_sure_uyumlu_mu = lambda *args, **kwargs: (True, 25.0, 0.9)
    try:
        result = guard._single_pass_reels_and_tts(
            FakeRouter(), {}, {}, {}, "Sadece kadın sesi kullan", 30,
            "dengeli", "Autonoe", lambda _msg: None,
        )
    finally:
        guard._original_reels_creative = original_reels
        guard._pipeline._ses_sure_uyumlu_mu = original_duration

    assert result[6] == "SOLO_FEMALE"
    assert result[4] is True
    assert calls and calls[0][1] == "Autonoe"
