"""otoXtra Duo karakter profilleri ve ses kimlikleri.

Bu katman Phase 2'de yalnızca karakter/voice kimliğini merkezileştirir.
Mevcut tek sesli TTS akışı hâlâ kadın karakteri (Autonoe) kullanır.
Multi-speaker TTS sonraki aşamada bu haritayı kullanacaktır.
"""

CHARACTER_VOICES = {
    "female": "Autonoe",
    "male": "Charon",
}

CHARACTER_LABELS = {
    "female": "Eş karakteri",
    "male": "Erkek karakter",
}

DEFAULT_SINGLE_SPEAKER = "female"


def voice_for_character(character: str) -> str:
    """Karakter kimliğinden Gemini TTS voice adını döndürür."""
    key = (character or "").strip().lower()
    return CHARACTER_VOICES.get(key, CHARACTER_VOICES[DEFAULT_SINGLE_SPEAKER])
