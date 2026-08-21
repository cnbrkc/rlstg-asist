"""otoXtra DUO/SOLO anlatım stratejisi için güvenli normalizasyon katmanı.

Kullanıcının açık mod talebi runtime işaretiyle mutlak öncelik taşır. Kullanıcı
mod belirtmediyse Reels Creative modelinin video ve içeriğe göre verdiği karar
korunur; geçersiz karar güvenli biçimde DUO'ya düşer.
"""

VALID_MODES = {"SOLO_FEMALE", "SOLO_MALE", "DUO"}
VALID_SPEAKERS = {"female", "male", "none"}
VALID_PURPOSES = {
    "hook", "fact", "reaction", "challenge", "rebuttal", "explanation",
    "counterpoint", "concession", "backchannel", "transition", "punchline",
    "callback", "closing",
}


def _clamp(value, default=0.0):
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


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
    """Kullanıcı override'ını, yoksa modelin editoryal mod kararını uygula."""
    explicit = str(reels_state.get("_explicit_voice_mode") or "").strip().upper()
    if explicit in VALID_MODES:
        return explicit
    candidate = str(
        reels_state.get("anlatim_modu")
        or raw.get("uygunluk")
        or raw.get("anlatim_modu")
        or raw.get("mode")
        or "DUO"
    ).strip().upper()
    return candidate if candidate in VALID_MODES else "DUO"


def normalize_duo_strategy(reels_state):
    """Kullanıcı öncelikli model kararını doğrulanmış üretim planına dönüştürür."""
    reels_state = reels_state or {}
    raw = reels_state.get("duo_stratejisi") or {}

    mode = _resolve_mode(reels_state, raw)
    allowed_speakers = (
        {"female"} if mode == "SOLO_FEMALE"
        else {"male"} if mode == "SOLO_MALE"
        else {"female", "male"}
    )

    hook = str(raw.get("hook_speaker") or "female").strip().lower()
    ending = str(raw.get("ending_speaker") or "male").strip().lower()
    if hook not in allowed_speakers:
        hook = next(iter(allowed_speakers))
    if ending not in allowed_speakers:
        ending = next(iter(allowed_speakers))

    raw_map = reels_state.get("konusma_haritasi") or []
    segments = []
    fallback_toggle = 0
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
        if requested_speaker not in allowed_speakers:
            requested_speaker = ""

        if requested_speaker:
            # Modelin bilinçli speaker seçimine güven; art arda aynı karakter
            # konuşuyorsa bu doğal bir editoryal tercih olabilir (biri anlatır,
            # diğeri tek satırla tepki verir) — burada zorla alternatif
            # karaktere çevrilmez. Doğal doku bu sayede korunur.
            speaker = requested_speaker
        elif mode == "DUO":
            speaker = "female" if fallback_toggle % 2 == 0 else "male"
            fallback_toggle += 1
        else:
            speaker = next(iter(allowed_speakers))

        segments.append({
            "sira": len(segments) + 1,
            "speaker": speaker,
            "amac": purpose,
            "detay": detail,
            "duygu": emotion,
        })

    if not segments:
        segments = _duo_scaffold() if mode == "DUO" else _solo_scaffold(next(iter(allowed_speakers)))

    if mode == "DUO":
        # Yalnızca DUO modunda iki sesin de temsil edilmesini garanti et;
        # tek satırlı planı diğer sesi ezerek değil güvenli bir dönüş ekleyerek
        # tamamla. Tek tek her segmentin alternatif olması zorunlu değildir.
        if len(segments) == 1:
            missing = "male" if segments[0]["speaker"] == "female" else "female"
            segments.append({
                "sira": 2,
                "speaker": missing,
                "amac": "closing",
                "detay": "ana çıkarım",
                "duygu": "natural",
            })
        elif not any(x["speaker"] == "female" for x in segments):
            segments[0]["speaker"] = "female"
        elif not any(x["speaker"] == "male" for x in segments):
            segments[1]["speaker"] = "male"

    female_count = sum(1 for x in segments if x["speaker"] == "female")
    male_count = sum(1 for x in segments if x["speaker"] == "male")
    total = max(1, female_count + male_count)

    return {
        "mode": mode,
        "hook_speaker": hook,
        "ending_speaker": ending,
        "female_weight": _clamp(raw.get("female_agirligi"), female_count / total),
        "male_weight": _clamp(raw.get("male_agirligi"), male_count / total),
        "interaction_level": max(0.35, _clamp(raw.get("interaction_level"), 0.55)) if mode == "DUO" else _clamp(raw.get("interaction_level"), 0.0),
        "humor_level": _clamp(raw.get("humor_level"), 0.3),
        "tension_level": _clamp(raw.get("tension_level"), 0.2),
        "selected_detail": str(raw.get("selected_detail") or "").strip(),
        "rationale": str(raw.get("rationale") or "").strip(),
        "conversation_map": segments,
    }
