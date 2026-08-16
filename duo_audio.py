"""Duo/solo multi-speaker TTS layer.

A DUO conversation is generated in one Gemini multi-speaker TTS request, so
there is no per-segment TTS fan-out and no WAV stitching. SOLO modes use the
same single request with only their permitted voice configured. Legacy fallback
is handled by the caller when this request fails.
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
    """Generate the complete selected voice mode in ONE TTS request."""
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

    speakers_present = []
    if any(str(s.get("speaker", "")).strip().lower() == "female" for s in valid):
        speakers_present.append(("Autonoe", "Autonoe"))
    if any(str(s.get("speaker", "")).strip().lower() == "male" for s in valid):
        speakers_present.append(("Charon", "Charon"))

    mode_label = "DUO" if len(speakers_present) == 2 else speakers_present[0][0]
    log_ekle(f"🎙️ {mode_label} TTS tek çağrı: {len(valid)} segment")
    ok, info = router.coklu_ses_uret(
        transcript,
        speakers_present,
        output_path,
        log_ekle,
        hiz_carpani=hiz_carpani,
    )
    if not ok:
        log_ekle("⚠️ Tek çağrı TTS başarısız; legacy fallback kullanılacak.")
        return False, None, []
    return True, info, []
