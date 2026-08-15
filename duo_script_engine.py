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
        "conversation_map": conversation_map,
        "rules": [
            "Yalnızca planlanmış speaker'ları kullan.",
            "Fact Lock dışına çıkma; kullanıcı notunu değiştirme.",
            "Karakterler birbirine değil konuya tepki versin.",
            "Gereksiz ping-pong ve aynı fikrin tekrarını engelle.",
            "Diyalog doğal konuşma ritminde olsun; sırf iki ses var diye cümleleri bölme.",
            "Marka/üretici hedefleme, hakaret veya düşmanca ifade üretme.",
            "Her replik videoya veya otomobil tartışmasına yeni bir değer katmalı.",
        ],
    }


def build_generation_prompt(contract: Dict[str, Any], editorial_context: str = "", fact_lock: str = "") -> str:
    """Return a strict JSON-only generation prompt for the model call."""
    return (
        "Sen otoXtra'nın iki karakterli otomobil anlatım yazarı olarak çalışıyorsun.\n"
        "Aşağıdaki sözleşmeye göre yalnızca JSON üret.\n\n"
        "HEDEF: Doğal bir eş/partner otomobil sohbeti. Diyalog yapay skeç gibi olmayacak. "
        "İki kişi sırf konuşsun diye gereksiz replik eklenmeyecek.\n"
        "KONUŞMA HARİTASINDAKİ HER GEÇERLİ SEGMENT İÇİN BİR REPLİK ÜRET; "
        "haritayı gereksiz yere boş bırakma. Speaker yalnızca sözleşmede izin verilen değerlerden biri olmalı.\n\n"
        f"SÖZLEŞME:\n{contract}\n\n"
        f"EDITORIAL CONTEXT:\n{editorial_context}\n\n"
        f"FACT LOCK:\n{fact_lock}\n\n"
        "ÇIKTI ŞEMASI:\n"
        "{\"segments\":[{\"speaker\":\"female|male\",\"text\":\"...\"}]}\n"
        "Sadece bu JSON'u döndür."
    )


def validate_generated_duo(contract: Dict[str, Any], generated: Any) -> List[Dict[str, str]]:
    """Validate the model's structured response before any TTS use.

    The model router returns the schema object itself, i.e. {"segments": [...]};
    the validator consumes the segments array. Accepting a bare list as well keeps
    this layer tolerant of older/test callers.
    """
    if isinstance(generated, dict):
        generated = generated.get("segments", [])
    return validate_script_segments(generated, contract.get("mode", "DUO"))
