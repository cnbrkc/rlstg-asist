"""otoXtra Duo anlatım stratejisi için güvenli normalizasyon katmanı.

Reels Creative'in anlati_modu + duo_stratejisi + konusma_haritasi çıktısını
kontrollü bir iki-karakter planına dönüştürür. Telegram üretim sözleşmesinde
DUO zorunludur: gerçek TTS katmanına iki karakterin de ulaşması garanti edilir.
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


def _duo_scaffold():
    return [
        {"sira": 1, "speaker": "female", "amac": "hook", "detay": "en güçlü hikâye açısı", "duygu": "curious"},
        {"sira": 2, "speaker": "male", "amac": "fact", "detay": "en güçlü doğrulanmış detay", "duygu": "confident"},
        {"sira": 3, "speaker": "female", "amac": "reaction", "detay": "gerçek kullanım açısından doğal tepki", "duygu": "amused"},
        {"sira": 4, "speaker": "male", "amac": "closing", "detay": "ana çıkarım", "duygu": "serious"},
    ]


def normalize_duo_strategy(reels_state):
    """Model çıktısını deterministik, iki karakterli üretim planına dönüştürür."""
    reels_state = reels_state or {}
    # Telegram otoXtra production contract: single-speaker output is not allowed.
    # The creative model may still choose a solo mode internally, but the actual
    # speaker plan is normalized to DUO before script generation/TTS.
    mode = "DUO"

    raw = reels_state.get("duo_stratejisi") or {}
    hook = str(raw.get("hook_speaker") or "female").strip().lower()
    ending = str(raw.get("ending_speaker") or "male").strip().lower()
    if hook not in {"female", "male"}:
        hook = "female"
    if ending not in {"female", "male"}:
        ending = "male"

    raw_map = reels_state.get("konusma_haritasi") or []
    segments = []
    for item in raw_map:
        if not isinstance(item, dict):
            continue
        purpose = str(item.get("amac") or "transition").strip().lower()
        if purpose not in VALID_PURPOSES:
            purpose = "transition"
        detail = str(item.get("detay") or "").strip()
        if not detail:
            # Empty map rows cannot produce a meaningful dialogue turn. Do not
            # let them survive into the contract and later disappear silently.
            continue
        emotion = str(item.get("duygu") or "").strip()
        requested_speaker = str(item.get("speaker") or "").strip().lower()
        if requested_speaker not in {"female", "male"}:
            requested_speaker = ""

        if requested_speaker:
            speaker = requested_speaker
        else:
            speaker = "female" if len(segments) % 2 == 0 else "male"
        if segments and all(x["speaker"] == speaker for x in segments):
            speaker = "male" if speaker == "female" else "female"

        segments.append({
            "sira": len(segments) + 1,
            "speaker": speaker,
            "amac": purpose,
            "detay": detail,
            "duygu": emotion,
        })

    if not segments:
        segments = _duo_scaffold()

    # Guarantee both voices are represented without forcing equal airtime.
    if not any(x["speaker"] == "female" for x in segments):
        segments[0]["speaker"] = "female"
    if not any(x["speaker"] == "male" for x in segments):
        idx = 1 if len(segments) > 1 else 0
        segments[idx]["speaker"] = "male"

    female_count = sum(1 for x in segments if x["speaker"] == "female")
    male_count = sum(1 for x in segments if x["speaker"] == "male")
    total = max(1, female_count + male_count)

    return {
        "mode": mode,
        "hook_speaker": hook,
        "ending_speaker": ending,
        "female_weight": _clamp(raw.get("female_agirligi"), female_count / total),
        "male_weight": _clamp(raw.get("male_agirligi"), male_count / total),
        "interaction_level": max(0.35, _clamp(raw.get("interaction_level"), 0.55)),
        "humor_level": _clamp(raw.get("humor_level"), 0.3),
        "tension_level": _clamp(raw.get("tension_level"), 0.2),
        "selected_detail": str(raw.get("selected_detail") or "").strip(),
        "rationale": "Telegram production contract: two-character Duo TTS is required.",
        "conversation_map": segments,
    }
