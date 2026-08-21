"""Duo multi-speaker TTS layer.

A DUO conversation is generated in one Gemini multi-speaker TTS request, with
Autonoe + Charon configured together. The transcript deliberately stays clean: performance is directed at scene level
instead of forcing a rotating emotion tag and identical pause onto every turn.
This leaves Gemini room for content-aware prosody and avoids a staged duet
cadence while preserving the exact approved spoken words.
"""
from typing import List, Dict, Any

from core.character_profiles import voice_for_character
from core.config import SES_HIZ_CARPANI


def _duo_transcript(segments: List[Dict[str, Any]]) -> str:
    """Build speaker-labelled clean text; scene-level prompt owns performance."""
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
    """Generate the complete two-speaker conversation in ONE TTS request.

    Returns (ok, info): ok bool, info model kimliği (başarısızsa None).
    """
    valid = [
        s for s in (segments or [])
        if isinstance(s, dict)
        and str(s.get("text", "")).strip()
        and str(s.get("speaker", "")).strip().lower() in ("female", "male")
    ]
    if not valid:
        return False, None

    speakers_present = []
    if any(str(s.get("speaker", "")).strip().lower() == "female" for s in valid):
        speakers_present.append(("Autonoe", "Autonoe"))
    if any(str(s.get("speaker", "")).strip().lower() == "male" for s in valid):
        speakers_present.append(("Charon", "Charon"))

    # Production contract is explicitly two-speaker. If a malformed upstream
    # script reaches this layer, do not silently turn it into a single voice.
    if len(speakers_present) != 2:
        log_ekle("❌ DUO TTS reddedildi: Autonoe + Charon birlikte bulunmuyor.")
        return False, None

    transcript = _duo_transcript(valid)
    if not transcript:
        return False, None

    log_ekle(f"🎙️ DUO TTS tek çağrı: Autonoe + Charon | {len(valid)} segment | doğal sahne yönetimi + etiketsiz akış")
    ok, info = router.coklu_ses_uret(
        transcript,
        speakers_present,
        output_path,
        log_ekle,
        hiz_carpani=hiz_carpani,
    )
    if not ok:
        log_ekle("❌ Tek çağrı DUO TTS başarısız; tek sesli fallback engellendi.")
        return False, None
    return True, info
