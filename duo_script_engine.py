"""Duo script generation/validation layer.

Keeps the legacy single-speaker production path intact while turning the
normalized conversation plan into a strict, validated speaker script.
"""

from typing import Any, Dict, List

from duo_script import normalize_conversation_map, validate_script_segments


CHARACTER_ROLES = {
    "female": {
        "name": "Autonoe",
        "role": "eş/partner karakteri; doğal, zeki, gerektiğinde esprili ve hafif meydan okuyucu",
    },
    "male": {
        "name": "Charon",
        "role": "eş/partner karakteri; otomobil meraklısı, sakin ama gerektiğinde net ve hafif iddialı",
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
            "Karakterler birbirine değil konuya tepki versin.",
            "Gereksiz ping-pong ve aynı fikrin tekrarını engelle.",
            "Diyalog doğal konuşma ritminde olsun; sırf iki ses var diye cümleleri bölme.",
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
        "HEDEF: Doğal bir eş/partner otomobil sohbeti. Diyalog yapay skeç gibi olmayacak. "
        "İki kişi sırf konuşsun diye gereksiz replik eklenmeyecek.\n"
        "KONUŞMA HARİTASINDAKİ HER GEÇERLİ SEGMENT İÇİN BİR REPLİK ÜRET; "
        "haritayı gereksiz yere boş bırakma. Speaker yalnızca sözleşmede izin verilen değerlerden biri olmalı.\n"
        f"{tone_rule}"
        f"{length_rule}\n"
        f"SÖZLEŞME:\n{contract}\n\n"
        f"EDITORIAL CONTEXT:\n{editorial_context}\n\n"
        f"FACT LOCK:\n{fact_lock}\n\n"
        "ÇIKTI ŞEMASI:\n"
        "{\"segments\":[{\"speaker\":\"female|male\",\"text\":\"...\"}]}\n"
        "Sadece bu JSON'u döndür."
    )


def _segment_word_count(segments: Any) -> int:
    import re
    total = 0
    for segment in segments or []:
        if isinstance(segment, dict):
            total += len(re.findall(r"\b\w+(?:[-']\w+)*\b", str(segment.get("text", "")), re.UNICODE))
    return total


def validate_generated_duo(contract: Dict[str, Any], generated: Any) -> List[Dict[str, str]]:
    """Validate the model's structured response before any TTS use."""
    if isinstance(generated, dict):
        generated = generated.get("segments", [])
    segments = validate_script_segments(generated, contract.get("mode", "DUO"))
    if not segments:
        return []

    min_words = contract.get("min_words")
    max_words = contract.get("max_words")
    if min_words is not None or max_words is not None:
        count = _segment_word_count(segments)
        if min_words is not None and count < int(min_words):
            return []
        if max_words is not None and count > int(max_words):
            return []
    return segments
