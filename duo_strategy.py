"""otoXtra Duo anlatım stratejisi için güvenli normalizasyon katmanı.

Bu modül mevcut tek sesli TTS/render akışını değiştirmez. Reels Creative'in
ürettiği anlatım_modu + duo_stratejisi + konusma_haritasi çıktısını tek bir
kontrollü yapıya toplar. Multi-speaker TTS sonraki fazda bu çıktıyı kullanır.
"""

VALID_MODES = {"SOLO_FEMALE", "SOLO_MALE", "DUO"}
VALID_SPEAKERS = {"female", "male", "none"}
VALID_PURPOSES = {
    "hook", "fact", "reaction", "challenge", "explanation",
    "counterpoint", "transition", "punchline", "closing",
}


def _clamp(value, default=0.0):
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def normalize_duo_strategy(reels_state):
    """Model çıktısını güvenli ve deterministik bir anlatım planına dönüştürür."""
    reels_state = reels_state or {}
    mode = str(reels_state.get("anlatim_modu") or "DUO").strip().upper()
    if mode not in VALID_MODES:
        mode = "DUO"

    raw = reels_state.get("duo_stratejisi") or {}
    hook = str(raw.get("hook_speaker") or "none").strip().lower()
    ending = str(raw.get("ending_speaker") or "none").strip().lower()
    if hook not in VALID_SPEAKERS:
        hook = "none"
    if ending not in VALID_SPEAKERS:
        ending = "none"

    # Solo modda ikinci karakter yanlışlıkla plana sızmasın.
    allowed = {"female"} if mode == "SOLO_FEMALE" else {"male"} if mode == "SOLO_MALE" else {"female", "male"}
    if hook not in allowed:
        hook = next(iter(allowed))
    if ending not in allowed:
        ending = next(iter(allowed))

    segments = []
    for item in reels_state.get("konusma_haritasi") or []:
        if not isinstance(item, dict):
            continue
        speaker = str(item.get("speaker") or "").strip().lower()
        if speaker not in allowed:
            continue
        purpose = str(item.get("amac") or "transition").strip().lower()
        if purpose not in VALID_PURPOSES:
            purpose = "transition"
        segments.append({
            "sira": len(segments) + 1,
            "speaker": speaker,
            "amac": purpose,
            "detay": str(item.get("detay") or "").strip(),
            "duygu": str(item.get("duygu") or "").strip(),
        })

    return {
        "mode": mode,
        "hook_speaker": hook,
        "ending_speaker": ending,
        "female_weight": _clamp(raw.get("female_agirligi")),
        "male_weight": _clamp(raw.get("male_agirligi")),
        "interaction_level": _clamp(raw.get("interaction_level")),
        "humor_level": _clamp(raw.get("humor_level")),
        "tension_level": _clamp(raw.get("tension_level")),
        "selected_detail": str(raw.get("selected_detail") or "").strip(),
        "rationale": str(raw.get("rationale") or "").strip(),
        "conversation_map": segments,
    }
