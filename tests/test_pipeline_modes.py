import os
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("GEMINI_API_KEY", "test")

from core.pipeline import _explicit_voice_mode_from_notes
from core.agentic import agentic_icerik_uretimi


class _Router:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def metin_uret(self, *args, **kwargs):
        response = self.responses[self.calls % len(self.responses)]
        self.calls += 1
        return response, f"fake-model-{self.calls}"

    def ses_uret(self, text, voice, output, log, hiz_carpani=1.0, **kwargs):
        Path(output).write_bytes(b"fake-audio-data")
        return True, "fake-tts-model"


def _detective():
    return {
        "kronik_sikayetler": ["Yakıt tüketimi yüksek"],
        "turkiye_ozel_magduriyet": "ÖTV dilimi dezavantajı",
        "viral_kan_mali": "Türkiye fiyatı Avrupa'nın 2 katı"
    }


def _hook():
    return {
        "secilen_sablon": "Ters_Kose",
        "kapak_metni": "Fiyat Şoku",
        "ilk_3_saniye_kanca": "Bu fiyat herkesi şaşırttı"
    }


def _script():
    return {
        "segments": [
            {"speaker": "female", "tts_tag": "", "text": "Bu araç çok iyi."},
            {"speaker": "male", "tts_tag": "", "text": "Evet."}
        ],
        "yorum_tetikleyici_soru": "Siz ne düşünüyorsunuz?"
    }


def _critic():
    return {"score": 8, "approved": True, "feedback": ""}


def _metadata():
    return {
        "reels_baslik": "Fiyat Şoku",
        "reels_aciklama": "Bu fiyat herkesi şaşırttı",
        "reels_hashtag": ["#otomobil"]
    }


def _mock_duo_ses(router, segments, output_path, log, hiz_carpani=1.0):
    Path(output_path).write_bytes(b"fake-duo-audio")
    return True, "fake-duo-tts"


_kelime_patch = patch("core.agentic._reels_kelime_ayarlarini_hazirla", return_value=(10, 1, 999, 2.5, 5))
_ses_sure_patch = patch("core.agentic._ses_suresini_al", return_value=25.0)


def test_explicit_solo_and_duo_note_detection():
    assert _explicit_voice_mode_from_notes("Yalnızca kadın sesi kullan") == "SOLO_FEMALE"
    assert _explicit_voice_mode_from_notes("Sadece erkek anlatsın") == "SOLO_MALE"
    assert _explicit_voice_mode_from_notes("Solo olmasın, iki sesli duo olsun") == "DUO"
    assert _explicit_voice_mode_from_notes("Duo olmasın, solo yap") == "SOLO"
    assert _explicit_voice_mode_from_notes("Solo mu duo mu videoya göre sen seç") == ""
    assert _explicit_voice_mode_from_notes("Normal üret") == ""


@_ses_sure_patch
@_kelime_patch
def test_agentic_solo_female_mode(mock_kelime, mock_ses):
    router = _Router([_detective(), _hook(), _script(), _critic(), _metadata()])
    logs = []

    reels, model, plan, script, ses_ok, ses_model, ses_modu, ses_dosyasi, meta = agentic_icerik_uretimi(
        router, {}, {}, {}, 30, "dengeli", "Autonoe", logs.append, mod_karari={"mode": "SOLO_FEMALE"}
    )

    assert plan["mode"] == "SOLO_FEMALE"
    speakers = {seg.get("speaker") for seg in script["segments"]}
    assert speakers == {"female"}


@_ses_sure_patch
@_kelime_patch
def test_agentic_solo_male_mode(mock_kelime, mock_ses):
    router = _Router([_detective(), _hook(), _script(), _critic(), _metadata()])
    logs = []

    reels, model, plan, script, ses_ok, ses_model, ses_modu, ses_dosyasi, meta = agentic_icerik_uretimi(
        router, {}, {}, {}, 30, "dengeli", "Charon", logs.append, mod_karari={"mode": "SOLO_MALE"}
    )

    assert plan["mode"] == "SOLO_MALE"
    speakers = {seg.get("speaker") for seg in script["segments"]}
    assert speakers == {"male"}


@_ses_sure_patch
@_kelime_patch
@patch("core.agentic.duo_ses_uret", side_effect=_mock_duo_ses)
def test_agentic_duo_mode(mock_tts, mock_kelime, mock_ses):
    router = _Router([_detective(), _hook(), _script(), _critic(), _metadata()])
    logs = []

    reels, model, plan, script, ses_ok, ses_model, ses_modu, ses_dosyasi, meta = agentic_icerik_uretimi(
        router, {}, {}, {}, 30, "dengeli", "Autonoe", logs.append, mod_karari={"mode": "DUO"}
    )

    assert plan["mode"] == "DUO"
    speakers = {seg.get("speaker") for seg in script["segments"]}
    assert "female" in speakers or "male" in speakers


@_ses_sure_patch
@_kelime_patch
@patch("core.agentic.duo_ses_uret", side_effect=_mock_duo_ses)
def test_metadata_generated(mock_tts, mock_kelime, mock_ses):
    router = _Router([_detective(), _hook(), _script(), _critic(), _metadata()])
    logs = []

    reels, model, plan, script, ses_ok, ses_model, ses_modu, ses_dosyasi, meta = agentic_icerik_uretimi(
        router, {}, {}, {}, 30, "dengeli", "Autonoe", logs.append, mod_karari={"mode": "DUO"}
    )

    assert meta.get("reels_baslik") == "Fiyat Şoku"
    assert meta.get("reels_aciklama") == "Bu fiyat herkesi şaşırttı"
    assert "#otomobil" in meta.get("reels_hashtag", [])
