VALID_MODES = {"SOLO_FEMALE", "SOLO_MALE", "DUO"}
VALID_SPEAKERS = {"female", "male", "none"}
VALID_PURPOSES = {
    "hook", "fact", "reaction", "challenge", "rebuttal", "explanation",
    "counterpoint", "concession", "backchannel", "transition", "punchline",
    "callback", "closing",
}

def _clamp(value, default=0.0):
    try: return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError): return default

def _duo_scaffold():
    return [
        {"sira": 1, "speaker": "female", "amac": "hook", "detay": "en güçlü Türkiye ilgi kancasıyla net iddia", "duygu": "natural"},
        {"sira": 2, "speaker": "male", "amac": "rebuttal", "detay": "ilk iddianın belirli noktasına kısa karşılık", "duygu": "natural"},
        {"sira": 3, "speaker": "male", "amac": "fact", "detay": "karşılığı destekleyen en güçlü doğrulanmış kanıt", "duygu": "natural"},
        {"sira": 4, "speaker": "female", "amac": "counterpoint", "detay": "kanıtın Türkiye'deki gerçek kullanım karşılığı", "duygu": "natural"},
        {"sira": 5, "speaker": "male", "amac": "concession", "detay": "hak verme ve asıl sürprize dönüş", "duygu": "natural"},
        {"sira": 6, "speaker": "female", "amac": "callback", "detay": "açılışa dönen net payoff", "duygu": "natural"},
    ]

def _solo_scaffold(speaker):
    return [
        {"sira": 1, "speaker": speaker, "amac": "hook", "detay": "en güçlü hikâye açısı", "duygu": "curious"},
        {"sira": 2, "speaker": speaker, "amac": "fact", "detay": "en güçlü doğrulanmış detay", "duygu": "confident"},
        {"sira": 3, "speaker": speaker, "amac": "closing", "detay": "ana çıkarım", "duygu": "serious"},
    ]

def _resolve_mode(reels_state, raw):
    explicit = str(reels_state.get("_explicit_voice_mode") or "").strip().upper()
    if explicit in VALID_MODES: return explicit
    candidate = str(reels_state.get("anlatim_modu") or raw.get("uygunluk") or raw.get("anlatim_modu") or raw.get("mode") or "DUO").strip().upper()
    return candidate if candidate in VALID_MODES else "DUO"

def normalize_duo_strategy(reels_state):
    reels_state = reels_state or {}
    raw = reels_state.get("duo_stratejisi") or {}
    mode = _resolve_mode(reels_state, raw)
    allowed_speakers = {"female"} if mode == "SOLO_FEMALE" else {"male"} if mode == "SOLO_MALE" else {"female", "male"}
    solo_speaker = next(iter(allowed_speakers)) if len(allowed_speakers) == 1 else None
    default_hook = solo_speaker or "female"
    default_ending = solo_speaker or "male"

    hook = str(raw.get("hook_speaker") or default_hook).strip().lower()
    ending = str(raw.get("ending_speaker") or default_ending).strip().lower()
    if hook not in allowed_speakers: hook = default_hook
    if ending not in allowed_speakers: ending = default_ending

    raw_map = reels_state.get("konusma_haritasi") or []
    segments = []
    
    for item in raw_map:
        if not isinstance(item, dict): continue
        purpose = str(item.get("amac") or "transition").strip().lower()
        if purpose not in VALID_PURPOSES: purpose = "transition"
        detail = str(item.get("detay") or "").strip()
        if not detail: continue
        emotion = str(item.get("duygu") or "natural").strip()
        if not emotion: emotion = "natural"
        requested_speaker = str(item.get("speaker") or "").strip().lower()
        
        # TESTİN BEKLEDİĞİ: SOLO modda yanlış speaker'ı silmek yerine izin verilen speaker'a çevir
        if requested_speaker in allowed_speakers:
            speaker = requested_speaker
        else:
            # Eğer speaker geçersizse, izin verilen speaker'a çevir (SOLO modda solo_speaker)
            speaker = solo_speaker if solo_speaker else "female"
            
        segments.append({"sira": len(segments) + 1, "speaker": speaker, "amac": purpose, "detay": detail, "duygu": emotion})

    if not segments: segments = _duo_scaffold() if mode == "DUO" else _solo_scaffold(solo_speaker)

    if mode == "DUO":
        if len(segments) == 1:
            missing = "male" if segments[0]["speaker"] == "female" else "female"
            segments.append({"sira": 2, "speaker": missing, "amac": "closing", "detay": "ana çıkarım", "duygu": "natural"})
        elif not any(x["speaker"] == "female" for x in segments): segments[0]["speaker"] = "female"
        elif not any(x["speaker"] == "male" for x in segments): segments[1]["speaker"] = "male"

    female_count = sum(1 for x in segments if x["speaker"] == "female")
    male_count = sum(1 for x in segments if x["speaker"] == "male")
    total = max(1, female_count + male_count)

    return {
        "mode": mode, "hook_speaker": hook, "ending_speaker": ending,
        "female_weight": _clamp(raw.get("female_agirligi"), female_count / total),
        "male_weight": _clamp(raw.get("male_agirligi"), male_count / total),
        "interaction_level": max(0.35, _clamp(raw.get("interaction_level"), 0.55)) if mode == "DUO" else _clamp(raw.get("interaction_level"), 0.0),
        "humor_level": _clamp(raw.get("humor_level"), 0.3),
        "tension_level": _clamp(raw.get("tension_level"), 0.2),
        "selected_detail": str(raw.get("selected_detail") or "").strip(),
        "rationale": str(raw.get("rationale") or "").strip(),
        "conversation_map": segments,
    }
