"""DUO/SOLO script generation and validation layer.

Turns the normalized voice plan into a strict script contract. DUO requires
both speakers; SOLO follows either an explicit user override or the model's
content-based editorial mode decision.
"""

from typing import Any, Dict, List

from duo.duo_script import normalize_conversation_map, validate_script_segments


CHARACTER_ROLES = {
    "female": {
        "name": "Autonoe",
        "role": (
            "eş/partner karakteri; söyleneni hemen satın almayan, günlük kullanım ve para karşılığını "
            "zekice test eden, gerektiğinde kuru mizahla açık yakalayan doğal konuşmacı"
        ),
    },
    "male": {
        "name": "Charon",
        "role": (
            "eş/partner karakteri; otomobil bilgisini gösteriş için değil iddiasını kanıtlamak için kullanan, "
            "itiraz gelince savunmaya geçmek yerine nüansı kabul edip asıl noktayı açan doğal konuşmacı"
        ),
    },
}


def build_duo_generation_contract(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Build a model-independent contract for generating speaker dialogue."""
    plan = plan or {}
    mode = str(plan.get("mode") or plan.get("uygunluk") or plan.get("anlatim_modu") or "DUO").upper().strip()
    if mode not in {"SOLO_FEMALE", "SOLO_MALE", "DUO"}:
        mode = "DUO"

    conversation_map = normalize_conversation_map({**plan, "anlatim_modu": mode})
    allowed = {"female"} if mode == "SOLO_FEMALE" else {"male"} if mode == "SOLO_MALE" else {"female", "male"}
    conversation_map = [x for x in conversation_map if x["speaker"] in allowed]

    hook = str(plan.get("hook_speaker") or "").lower().strip()
    ending = str(plan.get("ending_speaker") or "").lower().strip()
    if hook not in allowed:
        hook = "female" if mode != "SOLO_MALE" else "male"
    if ending not in allowed:
        ending = "male" if mode != "SOLO_FEMALE" else "female"

    speakers = []
    for speaker in ("female", "male"):
        if speaker in allowed:
            profile = CHARACTER_ROLES[speaker]
            speakers.append({"speaker": speaker, "voice": profile["name"], "role": profile["role"]})

    target_words = plan.get("target_words", plan.get("hedef_kelime"))
    min_words = plan.get("min_words", plan.get("minimum_kelime"))
    max_words = plan.get("max_words", plan.get("maksimum_kelime"))

    def _number(value):
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    target_words = _number(target_words)
    min_words = _number(min_words)
    max_words = _number(max_words)
    if target_words is not None:
        min_words = min_words if min_words is not None else max(5, round(target_words * 0.90))
        max_words = max_words if max_words is not None else max(min_words, round(target_words * 1.10))

    content_tone = str(plan.get("content_tone") or plan.get("icerik_tonu") or "dengeli").strip().lower() or "dengeli"

    return {
        "mode": mode,
        "speakers": speakers,
        "hook_speaker": hook,
        "ending_speaker": ending,
        "female_weight": float(plan.get("female_weight", plan.get("female_agirligi", 0.0)) or 0.0),
        "male_weight": float(plan.get("male_weight", plan.get("male_agirligi", 0.0)) or 0.0),
        "interaction_level": float(plan.get("interaction_level", 0.5) or 0.5),
        "humor_level": float(plan.get("humor_level", 0.3) or 0.3),
        "tension_level": float(plan.get("tension_level", 0.2) or 0.2),
        "selected_detail": str(plan.get("selected_detail", "")).strip(),
        "content_tone": content_tone,
        "target_words": target_words,
        "min_words": min_words,
        "max_words": max_words,
        "conversation_map": conversation_map,
        "rules": [
            "Yalnızca planlanmış speaker'ları kullan.",
            "Fact Lock dışına çıkma; kullanıcı notunu değiştirme.",
            "Karakterler yalnız sırayla bilgi sunmasın; öncekinin belirli iddiasını yakalayıp karşılık versin.",
            "Hook-friction-proof-reversal-payoff omurgası kur; hook vaadini ortada kanıtla ve kapanışta karşılığını ver.",
            "En az bir anlamlı itiraz ve en az bir hak verme/fikir yumuşatma dönüşü bulunsun; sahte kavga üretme.",
            "İki makul tercih tarafı oluşabiliyorsa fiyat-değer, teknik-pratik veya tasarım-kullanım ekseninde ölçülü gerilim kur.",
            "Replik uzunluklarını ve speaker akışını asimetrik tut; eşit ölçülü düet kadansı üretme.",
            "Gereksiz ping-pong, röportaj tipi soru-cevap ve aynı fikrin tekrarını engelle.",
            "Diyalog doğal konuşma ritminde olsun; sırf iki ses var diye tek monoloğu cümle ortasından bölme.",
            "Marka/üretici hedefleme, hakaret veya düşmanca ifade üretme.",
            "Her replik videoya veya otomobil tartışmasına yeni bir değer katmalı.",
            "Seslendirme süresini korumak için hedef kelime aralığı verildiyse bunun dışına çıkma.",
            "İçerik tonunu değiştirme; seçilen ton yalnızca bilgi/yorum dengesini ve anlatım sertliğini yönetsin.",
        ],
    }


def build_generation_prompt(contract: Dict[str, Any], editorial_context: str = "", fact_lock: str = "", regeneration_instruction: str = "") -> str:
    """Return a strict JSON-only generation prompt for the model call."""
    length_rule = ""
    if contract.get("min_words") is not None and contract.get("max_words") is not None:
        length_rule = (
            f"\nKELİME/SÜRE KİLİDİ: Toplam script {contract['min_words']}-{contract['max_words']} kelime arasında olmalı "
            f"(hedef {contract.get('target_words')}). Bu sınırı aşma. Teknik bilgi yığma; en güçlü detayları seç.\n"
        )
    tone_rule = (
        f"\nİÇERİK TONU KİLİDİ: {contract.get('content_tone', 'dengeli')}. "
        "Bu runtime değerini başka bir varsayılan tonla ezme. Bilgi/yorum dengesi ve anlatım sertliği seçilen tona uygun kalmalı.\n"
    )
    if regeneration_instruction and regeneration_instruction.strip():
        length_rule += f"\n🚨 YENİDEN ÜRETİM TALİMATI:\n{regeneration_instruction.strip()}\n"

    return (
        "Sen otoXtra'nın iki karakterli otomobil anlatım yazarı olarak çalışıyorsun.\n"
        "Aşağıdaki sözleşmeye göre yalnızca JSON üret.\n\n"
        "HEDEF: İzleyicinin hazırlanmış iki sesli metin değil, arabaya bakarken kayda yakalanmış iki zeki partnerin "
        "kısa ve akışkan muhabbetini duyduğu hissi. Diyalog viral kısa video temposunda ilerlesin; yapay tiyatro, "
        "podcast sunuculuğu, haber spikerliği, şarkıcı düeti ve iki kişinin aynı ritimde sırayla cümle okuması kesinlikle olmasın.\n\n"
        "MUTLAK KURALLAR (DOĞAL MUHABBET + RETENTION MİMARİSİ):\n"
        "1. COLD OPEN: Selam, konu tanıtımı veya hazırlık cümlesi yok. İlk speaker en güçlü Türkiye ilgi kancasını net ve yarım bırakılmış bir iddia/çelişkiyle açsın. İkinci speaker ilk iddianın belirli kelimesini yakalayıp hemen itiraz etsin, düzeltsin veya beklenmedik yönünü açsın.\n"
        "2. HOOK → FRICTION → PROOF → REVERSAL → PAYOFF: Hook bir merak borcu açsın; sürtüşme iki makul bakış yaratsın; doğrulanmış bilgi kanıtı ödesin; biri nüansı kabul ederek sohbeti ters yöne çevirsin; kapanış hook'a cevap/callback versin. Aşamaları başlık gibi okuma.\n"
        "3. LEXICAL UPTAKE: İlk tur dışındaki repliklerin çoğu önceki replikteki somut bir iddia, rakam veya kelimeyi gerçekten yakalasın. `reply_anchor` alanına yakalanan kısa parçayı yaz. 'Aynen/evet/peki' deyip bağımsız monoloğa geçmek karşılıklılık değildir.\n"
        "4. ASİMETRİK RİTİM: Replikler eşit uzunlukta olmasın. Bir kısa refleks/itirazdan sonra kanıtı taşıyan daha uzun tur gelebilir; aynı speaker gerekirse art arda iki tur konuşabilir. Otomatik kadın-erkek-kadın-erkek salınımı ve her tur sonunda soru yasak.\n"
        "5. GERÇEK SÜRTÜŞME: Fact Lock'un desteklediği fiyat-değer, teknik-pratik, beklenti-gerçek veya tasarım-kullanım eksenlerinden yalnız birini seç. En az bir anlamlı karşı görüş olsun; sırf enerji için sahte kavga, fan küçümsemesi veya dayanaksız iddia üretme.\n"
        "6. İNSANİ DÖNÜŞ: En az bir speaker konuşmanın ortasında karşı tarafın bir noktasına hak versin, ilk pozisyonunu daraltsın veya 'tamam, o kısmı doğru ama...' mantığıyla asıl sürprize dönsün. İki taraf da baştan sona kendi metnini savunmasın.\n"
        "7. MİKRO REAKSİYON: En fazla 1-2 kısa backchannel kullanılabilir; tek başına dolgu olmasın, sonraki kanıt/itirazın rampası olsun. 'Yok artık, hadi canım, valla, aynen' gibi kalıpları otomatik kullanma.\n"
        "8. HER TUR İLERLESİN: Her replik şu işlerden birini yapsın: gerilimi artır, kanıt ver, beklentiyi tersine çevir, Türkiye karşılığını aç veya payoff getir. Önceki bilgiyi başka kelimelerle tekrar etme.\n"
        "9. KAPANIŞ: 'Siz ne düşünüyorsunuz/takip edin' gibi CTA, teşekkür veya resmi sonuç özeti yok. Son replik açılıştaki kelimeye/fikre callback yaparak enerjiyi yukarıda bıraksın.\n"
        "10. KONUŞMA HARİTASI: Haritayı ilham ve bilgi sırası olarak kullan; doğal akış için bitişik görevleri tek turda birleştirebilir veya kısa reaksiyon ekleyebilirsin. Haritayı mekanik olarak bire bir ve eşit uzunlukta seslendirme zorunluluğu yok.\n"
        f"{tone_rule}"
        f"{length_rule}\n"
        f"SÖZLEŞME:\n{contract}\n\n"
        f"EDITORIAL CONTEXT:\n{editorial_context}\n\n"
        f"FACT LOCK:\n{fact_lock}\n\n"
        "ÇIKTI ŞEMASI:\n"
        "{\"conversation_design\":{\"central_tension\":\"...\",\"hook_open_loop\":\"...\","
        "\"reversal\":\"...\",\"payoff_callback\":\"...\"},"
        "\"segments\":[{\"speaker\":\"female|male\",\"purpose\":\"hook|rebuttal|fact|counterpoint|concession|backchannel|callback|closing\","
        "\"reply_anchor\":\"OPENING veya önceki replikten kısa somut parça\",\"text\":\"...\"}]}\n"
        "Sadece bu JSON'u döndür."
    )


def _segment_word_count(segments: Any) -> int:
    import re
    total = 0
    for segment in segments or []:
        if isinstance(segment, dict):
            total += len(re.findall(r"\b\w+(?:[-']\w+)*\b", str(segment.get("text", "")), re.UNICODE))
    return total


def duo_conversation_quality_issues(contract: Dict[str, Any], generated: Any) -> List[str]:
    """Return structural conversation issues without judging factual content.

    This is intentionally separate from the fail-closed speaker validator: the
    pipeline can request one targeted script rewrite instead of falling back to
    a mechanically split monologue.
    """
    if str((contract or {}).get("mode") or "DUO").upper() != "DUO":
        return []
    payload = generated if isinstance(generated, dict) else {"segments": generated}
    segments = payload.get("segments") if isinstance(payload.get("segments"), list) else []
    valid = [
        item for item in segments
        if isinstance(item, dict)
        and str(item.get("speaker") or "").strip().lower() in {"female", "male"}
        and str(item.get("text") or "").strip()
    ]
    issues: List[str] = []
    target = int((contract or {}).get("target_words") or 0)
    min_turns = 5 if target >= 50 else 4
    if len(valid) < min_turns:
        issues.append(f"too_few_turns:{len(valid)}<{min_turns}")

    speakers = [str(item.get("speaker")).strip().lower() for item in valid]
    switches = sum(1 for left, right in zip(speakers, speakers[1:]) if left != right)
    if switches < min(3, max(1, len(valid) - 1)):
        issues.append(f"weak_turn_exchange:{switches}")
    if len(speakers) >= 2 and speakers[0] == speakers[1]:
        issues.append("cold_open_has_no_immediate_reply")

    word_counts = [_segment_word_count([item]) for item in valid]
    total_words = sum(word_counts)
    if total_words:
        by_speaker = {
            speaker: sum(count for normalized, count in zip(speakers, word_counts) if normalized == speaker)
            for speaker in ("female", "male")
        }
        if min(by_speaker.values()) / total_words < 0.16:
            issues.append("speaker_is_only_token_presence")
    if len(word_counts) >= 4 and max(word_counts, default=0) - min(word_counts, default=0) <= 2:
        issues.append("uniform_duet_cadence")
    if any(count > 42 for count in word_counts):
        issues.append("monologue_sized_turn")

    anchored = sum(
        1 for item in valid[1:]
        if str(item.get("reply_anchor") or "").strip()
        and str(item.get("reply_anchor") or "").strip().upper() != "OPENING"
    )
    if valid[1:] and anchored < max(2, len(valid[1:]) // 2):
        issues.append("insufficient_lexical_uptake")

    design = payload.get("conversation_design") if isinstance(payload.get("conversation_design"), dict) else {}
    for field in ("central_tension", "hook_open_loop", "reversal", "payoff_callback"):
        if not str(design.get(field) or "").strip():
            issues.append(f"missing_design:{field}")
    return issues


def validate_generated_duo(contract: Dict[str, Any], generated: Any) -> List[Dict[str, str]]:
    """Validate the model's structured response before any TTS use.

    A DUO script is structurally invalid unless both speakers are present. This
    validator deliberately does not rewrite speaker identity: doing so could
    change the intended dramaturgy. The caller's existing regeneration/QA path
    must regenerate the script instead of silently producing a single-speaker
    result.
    """
    if isinstance(generated, dict):
        generated = generated.get("segments", [])
    mode = str(contract.get("mode", "DUO") or "DUO").upper()
    segments = validate_script_segments(generated, mode)
    if not segments:
        return []

    if mode == "DUO":
        speakers_present = {segment["speaker"] for segment in segments}
        if not {"female", "male"}.issubset(speakers_present):
            return []

    # Kelime sayısı yalnızca AŞIRI uçları yakalamak için kullanılır. Eski sürüm,
    # hedefe ±%10 uymayan (ör. 198 hedefe karşın 190 kelime) geçerli iki sesli
    # bir script'i tamamen reddedip split_for_speakers kural tabanlı yedeğine
    # (daha kısa ve daha düşük kaliteli) düşürüyordu; bu da fazladan bir
    # LLM+TTS yeniden üretim döngüsüne mal oluyordu. Süre/oran denetimi
    # pipeline'ın FFmpeg senkron katmanında yapıldığı için burada yalnızca
    # belirgin şekilde kırık/çok kısa/çok şişirilmiş çıktıları ele.
    count = _segment_word_count(segments)
    min_words = contract.get("min_words")
    max_words = contract.get("max_words")
    hard_min = 1
    if min_words is not None:
        hard_min = max(1, int(int(min_words) * 0.4))   # hedefin ~%40'ının altı = kırık
    if count < hard_min:
        return []
    if max_words is not None and count > int(max_words) * 3:   # 3 kat aşırı şişme
        return []
    return segments
