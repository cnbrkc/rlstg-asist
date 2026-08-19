"""otoXtra karakter profilleri ve üretimdeki Gemini voice kimlikleri.

DUO üretiminde Autonoe ve Charon aynı multi-speaker TTS çağrısında kullanılır.
SOLO kullanıcı override'ı veya içerik-temelli AI kararıyla bu haritadaki tek voice'a iner.
"""

CHARACTER_VOICES = {
    "female": "Autonoe",
    "male": "Charon",
}

DEFAULT_SINGLE_SPEAKER = "female"


def voice_for_character(character: str) -> str:
    """Karakter kimliğinden Gemini TTS voice adını döndürür."""
    key = (character or "").strip().lower()
    return CHARACTER_VOICES.get(key, CHARACTER_VOICES[DEFAULT_SINGLE_SPEAKER])
