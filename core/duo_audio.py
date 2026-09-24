"""DUO/tek ses TTS katmanı: karakter-voice eşlemesi + multi-speaker üretimi.

DUO üretiminde Autonoe ve Charon aynı multi-speaker TTS çağrısında kullanılır
(tek çağrı, tek WAV). SOLO kullanıcı override'ı veya içerik-temelli AI kararıyla
bu haritadaki tek voice'a iner.
"""
from typing import Dict, List

from core.config import SES_HIZ_CARPANI

# Karakter kimliği → Gemini prebuilt voice (tek gerçek kaynak).
CHARACTER_VOICES = {
    "female": "Autonoe",
    "male": "Charon",
}

DEFAULT_SINGLE_SPEAKER = "female"


def voice_for_character(character: str) -> str:
    """Karakter kimliğinden Gemini TTS voice adını döndürür."""
    key = (character or "").strip().lower()
    return CHARACTER_VOICES.get(key, CHARACTER_VOICES[DEFAULT_SINGLE_SPEAKER])


def _duo_transcript(segments: List[Dict[str, str]]) -> str:
    lines = []
    for segment in segments or []:
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text", "")).strip()
        speaker = str(segment.get("speaker", "")).strip().lower()
        if not text or speaker not in ("female", "male"):
            continue
        lines.append(f"{voice_for_character(speaker)}: {text}")
    return "\n".join(lines)


def duo_ses_uret(router, segments, output_path, log_ekle, hiz_carpani=SES_HIZ_CARPANI):
    """Tek Gemini multi-speaker çağrısıyla DUO WAV üretir; tek sesli fallback YOK."""
    valid = [s for s in (segments or []) if isinstance(s, dict) and str(s.get("text", "")).strip() and str(s.get("speaker", "")).strip().lower() in ("female", "male")]
    if not valid:
        return False, None

    speakers_present = []
    if any(str(s.get("speaker", "")).strip().lower() == "female" for s in valid):
        speakers_present.append((voice_for_character("female"), voice_for_character("female")))
    if any(str(s.get("speaker", "")).strip().lower() == "male" for s in valid):
        speakers_present.append((voice_for_character("male"), voice_for_character("male")))

    if len(speakers_present) != 2:
        log_ekle("❌ DUO TTS reddedildi: İki farklı ses bulunmuyor.")
        return False, None

    transcript = _duo_transcript(valid)
    if not transcript:
        return False, None

    log_ekle(f"🎙️ DUO TTS tek çağrı: {len(valid)} segment | doğal sahne yönetimi + etiketsiz akış")
    ok, info = router.coklu_ses_uret(transcript, speakers_present, output_path, log_ekle, hiz_carpani=hiz_carpani)
    if not ok:
        log_ekle("❌ Tek çağrı DUO TTS başarısız; tek sesli fallback engellendi.")
        return False, None
    return True, info
