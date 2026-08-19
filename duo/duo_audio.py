"""Duo multi-speaker TTS layer.

A DUO conversation is generated in one Gemini multi-speaker TTS request, with
Autonoe + Charon configured together. The transcript receives a small amount
of Gemini-native performance direction (English audio tags) so the voices have
natural emotional variation and turn-taking pauses without changing spoken
words or the content/word-count contract.
"""
from typing import List, Dict, Any

from core.character_profiles import voice_for_character
from core.config import SES_HIZ_CARPANI


_FEMALE_TAGS = ("[curious]", "[amazed]", "[amused]", "[serious]")
_MALE_TAGS = ("[confident]", "[excitedly]", "[serious]", "[amused]")


def _performance_tag(speaker: str, turn_index: int) -> str:
    tags = _FEMALE_TAGS if speaker == "female" else _MALE_TAGS
    return tags[turn_index % len(tags)]


def _duo_transcript(segments: List[Dict[str, Any]]) -> str:
    lines = []
    turn_index = {"female": 0, "male": 0}
    for segment in segments or []:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text", "")).strip()
        speaker = str(segment.get("speaker", "")).strip().lower()
        if not text or speaker not in ("female", "male"):
            continue
        voice = voice_for_character(speaker)
        speaker_name = "Autonoe" if voice == "Autonoe" else "Charon"
        tag = _performance_tag(speaker, turn_index[speaker])
        pause = "[short pause] " if lines else ""
        lines.append(f"{speaker_name}: {pause}{tag} {text}")
        turn_index[speaker] += 1
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

    log_ekle(f"🎙️ DUO TTS tek çağrı: Autonoe + Charon | {len(valid)} segment | expressive tags + kısa duraklar")
    ok, info = router.coklu_ses_uret(
        transcript,
        speakers_present,
        output_path,
        log_ekle,
        hiz_carpani=hiz_carpani,
    )
    if not ok:
        log_ekle("⚠️ Tek çağrı DUO TTS başarısız; legacy fallback kullanılacak.")
        return False, None
    return True, info
