"""Duo Autonoe + Charon ses timeline katmanı.

Duo konuşması tek bir Gemini multi-speaker TTS isteğinde üretilir. Böylece
konuşma segmentleri arasında ayrı WAV üretip birleştirme yapılmaz; Gemini tek
bir sürekli ses akışı üretir. Solo/legacy TTS akışına dokunulmaz.
"""
from typing import List, Dict, Any

from character_profiles import voice_for_character
from config import SES_HIZ_CARPANI


def _duo_transcript(segments: List[Dict[str, Any]]) -> str:
    lines = []
    for segment in segments or []:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text", "")).strip()
        speaker = str(segment.get("speaker", "")).strip().lower()
        if not text or speaker not in ("female", "male"):
            continue
        voice = voice_for_character(speaker)
        speaker_name = "Autonoe" if voice == "Autonoe" else "Charon"
        lines.append(f"{speaker_name}: {text}")
    return "\n".join(lines)


def duo_ses_uret(router, segments, output_path, log_ekle, hiz_carpani=SES_HIZ_CARPANI):
    """Generate the complete Duo dialogue in ONE multi-speaker TTS request."""
    valid = [
        s for s in (segments or [])
        if isinstance(s, dict)
        and str(s.get("text", "")).strip()
        and str(s.get("speaker", "")).strip().lower() in ("female", "male")
    ]
    if not valid:
        return False, None, []

    transcript = _duo_transcript(valid)
    if not transcript:
        return False, None, []

    # Gemini supports up to two speakers in one TTS request. The configured
    # speaker names in the transcript are mapped to the existing Autonoe/Charon
    # voices. Speed adjustment is applied once to the complete returned WAV.
    speaker_voices = [("Autonoe", "Autonoe"), ("Charon", "Charon")]
    log_ekle(f"🎙️ Duo TTS tek çağrı: {len(valid)} segment → Autonoe + Charon")
    ok, info = router.coklu_ses_uret(
        transcript,
        speaker_voices,
        output_path,
        log_ekle,
        hiz_carpani=hiz_carpani,
    )
    if not ok:
        log_ekle("⚠️ Tek çağrı Duo TTS başarısız; legacy tek sesli fallback kullanılacak.")
        return False, None, []
    return True, info, []
